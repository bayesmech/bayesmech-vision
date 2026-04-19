"""
Gaussian Splatting training using gsplat.

Trains a 3D Gaussian Splatting model from a COLMAP workspace and exports
the result as a .ply file. Uses the MCMC training strategy which caps the
total Gaussian count — this is important for 6 GB VRAM GPUs.

Requirements
------------
    uv pip install gsplat --index-url https://docs.gsplat.studio/whl/pt27cu126
    # or: uv pip install gsplat  (builds CUDA kernels on first run)
"""

import logging
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# SH DC coefficient: (rgb - 0.5) / C0  where C0 = sqrt(1/(4*pi)) ≈ 0.28209
_SH_C0 = 0.28209479177387814


def _rgb_to_sh(rgb):
    """Convert linear RGB → spherical harmonics DC component (torch)."""
    return (rgb - 0.5) / _SH_C0


class _ColmapDataset:
    """Minimal COLMAP dataset container built from pycolmap Reconstruction."""

    def __init__(self, points, points_rgb, camtoworlds, Ks, image_paths, train_indices):
        self.points = points                  # (N, 3) float32 numpy
        self.points_rgb = points_rgb          # (N, 3) uint8 numpy
        self.camtoworlds = camtoworlds        # (M, 4, 4) float64 numpy
        self.Ks = Ks                          # (M, 3, 3) float64 numpy
        self.image_paths = image_paths        # list[Path], length M
        self.train_indices = train_indices    # list[int]


def _load_colmap_dataset(data_dir: Path, data_factor: int):
    """Load the COLMAP sparse reconstruction into a dataset object.

    Reads the sparse model via pycolmap and returns a _ColmapDataset.
    Returns None on failure.
    """
    sparse_dir = data_dir / "sparse" / "0"
    images_dir = data_dir / "images"

    try:
        import pycolmap
    except ImportError:
        log.error("pycolmap not installed. Install with: uv pip install pycolmap")
        return None

    try:
        recon = pycolmap.Reconstruction(str(sparse_dir))
    except Exception as exc:
        log.error("  Could not load COLMAP reconstruction from %s: %s", sparse_dir, exc)
        return None

    # ── 3D points ────────────────────────────────────────────────────────
    pts3d = recon.points3D
    if not pts3d:
        log.error("  COLMAP reconstruction has no 3D points")
        return None

    points = np.array([p.xyz for p in pts3d.values()], dtype=np.float32)
    points_rgb = np.array([p.color[:3] for p in pts3d.values()], dtype=np.uint8)

    # ── Camera poses ──────────────────────────────────────────────────────
    # Sort images by name so index order is deterministic
    images_sorted = sorted(recon.images.values(), key=lambda im: im.name)

    camtoworlds_list = []
    Ks_list = []
    image_paths_list = []

    for img in images_sorted:
        if not img.has_pose:
            continue

        cam = recon.cameras[img.camera_id]

        # Camera-to-world: inverse of cam_from_world (world→camera)
        cfw = img.cam_from_world()
        R_cw = cfw.rotation.matrix()    # 3×3, camera←world
        t_cw = cfw.translation          # (3,) translation in camera frame

        # world←camera rotation and translation
        R_wc = R_cw.T
        t_wc = -R_wc @ t_cw

        c2w = np.eye(4, dtype=np.float64)
        c2w[:3, :3] = R_wc
        c2w[:3, 3] = t_wc
        camtoworlds_list.append(c2w)

        # PINHOLE camera intrinsics: [fx, fy, cx, cy]
        params = cam.params
        fx, fy, cx, cy = params[0], params[1], params[2], params[3]
        if data_factor > 1:
            fx /= data_factor; fy /= data_factor
            cx /= data_factor; cy /= data_factor

        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
        Ks_list.append(K)

        img_path = images_dir / img.name
        if not img_path.exists():
            log.warning("  Image not found: %s", img_path)
        image_paths_list.append(img_path)

    if not camtoworlds_list:
        log.error("  No registered images with valid poses found")
        return None

    camtoworlds = np.stack(camtoworlds_list)
    Ks = np.stack(Ks_list)
    train_indices = list(range(len(camtoworlds_list)))

    log.info("  gsplat dataset: %d 3D points, %d training images",
             len(points), len(train_indices))

    return _ColmapDataset(points, points_rgb, camtoworlds, Ks, image_paths_list, train_indices)


def train_splat(workspace: Path, output_ply: Path, cfg: dict) -> None:
    """
    Train a 3DGS model from the COLMAP workspace and write *output_ply*.

    Parameters
    ----------
    workspace:   COLMAP workspace dir (must contain sparse/0/ and images/)
    output_ply:  Destination .ply path for the trained Gaussian model
    cfg:         Full pipeline config dict (uses cfg["gaussian_splatting"])
    """
    import os
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch
    import torch.nn.functional as F
    from torch.optim import Adam

    try:
        from gsplat import rasterization
        from gsplat.strategy import MCMCStrategy
        from gsplat.cuda import _C as _gsplat_C
        if _gsplat_C is None:
            raise RuntimeError(
                "gsplat CUDA extensions are not compiled. "
                "Reinstall with a matching wheel, e.g.:\n"
                "  uv pip install gsplat --index-url https://docs.gsplat.studio/whl/pt27cu124\n"
                "Check your CUDA version with: nvcc --version && python -c \"import torch; print(torch.version.cuda)\""
            )
    except ImportError as e:
        log.error("gsplat not installed: %s", e)
        raise

    gs_cfg = cfg["gaussian_splatting"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("  gsplat: device=%s", device)

    data_factor = gs_cfg["data_factor"]
    max_steps = gs_cfg["max_steps"]
    max_gaussians = gs_cfg["mcmc_max_gaussians"]
    sh_degree = gs_cfg["sh_degree"]

    # ── Load COLMAP dataset ────────────────────────────────────────────────
    dataset = _load_colmap_dataset(workspace, data_factor)
    if dataset is None:
        log.error("  gsplat: dataset loading failed, skipping training")
        return

    points = torch.from_numpy(dataset.points).float().to(device)
    point_colors = torch.from_numpy(dataset.points_rgb).float().to(device) / 255.0

    log.info("  gsplat: %d SfM points, %d training images",
             len(points), len(dataset.train_indices))

    if device == "cpu":
        log.warning("  gsplat: no GPU found — training will be very slow")

    # ── Initialise Gaussians ───────────────────────────────────────────────
    splats = _init_gaussians(points, point_colors, device, sh_degree)

    # ── Optimisers ────────────────────────────────────────────────────────
    scene_lr_scale = _scene_scale(dataset)
    optimizers = {
        "means":      Adam([splats["means"]],      lr=1.6e-4 * scene_lr_scale),
        "scales":     Adam([splats["scales"]],     lr=5e-3),
        "quats":      Adam([splats["quats"]],      lr=1e-3),
        "opacities":  Adam([splats["opacities"]],  lr=5e-2),
        "sh0":        Adam([splats["sh0"]],        lr=2.5e-3),
        "shN":        Adam([splats["shN"]],        lr=2.5e-4 / 20),
    }

    # ── MCMC strategy ──────────────────────────────────────────────────────
    strategy = None
    strategy_state: dict = {}
    try:
        strategy = MCMCStrategy(
            cap_max=max_gaussians,
            min_opacity=0.005,
            refine_start_iter=500,
            refine_stop_iter=max_steps,
            refine_every=100,
        )
        strategy.check_sanity(splats, optimizers)
        strategy_state = strategy.initialize_state()  # populates binoms + other state
    except Exception as exc:
        log.warning("  MCMCStrategy init failed (%s), training without strategy", exc)
        strategy = None

    # ── Build training dataset ─────────────────────────────────────────────
    train_dataset = _build_train_dataset(dataset, data_factor, device)
    if not train_dataset:
        log.error("  gsplat: no training views could be loaded")
        return

    # ── Training loop ──────────────────────────────────────────────────────
    log.info("  gsplat: training for %d steps", max_steps)
    try:
        _train_loop(splats, optimizers, strategy, strategy_state,
                    train_dataset, max_steps, sh_degree, device, gs_cfg)
    except Exception as exc:
        log.error("  gsplat training failed: %s", exc)
        raise

    # ── Export ────────────────────────────────────────────────────────────
    _export_ply(splats, output_ply)
    log.info("  gsplat: saved %d Gaussians to %s", len(splats["means"]), output_ply)

    if torch.cuda.is_available():
        allocated = torch.cuda.max_memory_allocated() / 1024 ** 3
        log.info("  gsplat: peak VRAM %.2f GB", allocated)
        torch.cuda.empty_cache()


# ── Dataset helpers ───────────────────────────────────────────────────────────

def _scene_scale(dataset: _ColmapDataset) -> float:
    """Estimate scene scale (largest extent of SfM points) for LR scaling."""
    pts = dataset.points
    if len(pts) == 0:
        return 1.0
    extents = pts.max(0) - pts.min(0)
    return float(max(extents.max(), 1.0))


def _build_train_dataset(dataset: _ColmapDataset, data_factor: int, device: str) -> list[dict]:
    """Build a list of training view dicts from the dataset."""
    import torch
    import cv2

    views = []
    for idx in dataset.train_indices:
        img_path = dataset.image_paths[idx]
        if not img_path.exists():
            continue
        raw = cv2.imread(str(img_path))
        if raw is None:
            log.warning("  Could not read image: %s", img_path)
            continue
        img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
        if data_factor > 1:
            h, w = img.shape[:2]
            img = cv2.resize(img, (w // data_factor, h // data_factor),
                             interpolation=cv2.INTER_AREA)
        pixels = torch.from_numpy(img).float() / 255.0  # (H, W, 3)

        c2w = dataset.camtoworlds[idx]
        K = dataset.Ks[idx]

        views.append({
            "pixels": pixels.to(device),
            "camtoworld": torch.from_numpy(c2w).float().to(device),
            "K": torch.from_numpy(K).float().to(device),
            "height": pixels.shape[0],
            "width": pixels.shape[1],
        })

    return views


# ── Gaussian initialisation ───────────────────────────────────────────────────

def _init_gaussians(points, colors, device: str, sh_degree: int) -> dict:
    """Initialise splat parameters from SfM point cloud."""
    import torch

    N = len(points)

    # Scale from nearest-neighbour distances (log space)
    dist = _knn_dist(points, k=4)
    scales = torch.log(torch.sqrt(dist[:, 1:].mean(dim=-1, keepdim=True))).repeat(1, 3)

    # Identity quaternion [w=1, x=0, y=0, z=0]
    quats = torch.zeros(N, 4, device=device)
    quats[:, 0] = 1.0

    # Low initial opacity (logit space)
    opacities = torch.logit(torch.full((N,), 0.1, device=device))

    # SH DC component from point colours
    num_coeffs = (sh_degree + 1) ** 2
    sh = torch.zeros(N, num_coeffs, 3, device=device)
    sh[:, 0, :] = _rgb_to_sh(colors)

    splats = {
        "means":     torch.nn.Parameter(points.clone()),
        "scales":    torch.nn.Parameter(scales.to(device)),
        "quats":     torch.nn.Parameter(quats),
        "opacities": torch.nn.Parameter(opacities),
        "sh0":       torch.nn.Parameter(sh[:, :1, :]),
        "shN":       torch.nn.Parameter(sh[:, 1:, :]),
    }
    return splats


def _knn_dist(points, k: int = 4):
    """Compute k nearest-neighbour distances (brute-force, batched)."""
    import torch
    pts = points.float()
    batch = min(4096, len(pts))
    dists = []
    for i in range(0, len(pts), batch):
        d = torch.cdist(pts[i:i + batch], pts)
        d_sorted, _ = d.sort(dim=-1)
        dists.append(d_sorted[:, :k])
    return torch.cat(dists, dim=0)


# ── Training loop ─────────────────────────────────────────────────────────────

def _train_loop(splats, optimizers, strategy, strategy_state,
                train_dataset, max_steps: int, sh_degree: int,
                device: str, gs_cfg: dict) -> None:
    """Core training loop."""
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization

    n_views = len(train_dataset)

    for step in range(1, max_steps + 1):
        view = train_dataset[(step - 1) % n_views]
        H, W = view["height"], view["width"]

        camtoworld = view["camtoworld"].unsqueeze(0)   # (1, 4, 4)
        K = view["K"].unsqueeze(0)                    # (1, 3, 3)
        pixels = view["pixels"]                        # (H, W, 3)

        # Ramp up SH degree over training
        sh_degree_active = min(sh_degree, step // (max_steps // (sh_degree + 1) + 1))

        sh_coeffs = torch.cat([splats["sh0"], splats["shN"]], dim=1)  # (N, C, 3)

        renders, alphas, info = rasterization(
            means=splats["means"],
            quats=splats["quats"] / splats["quats"].norm(dim=-1, keepdim=True),
            scales=torch.exp(splats["scales"]),
            opacities=torch.sigmoid(splats["opacities"]),
            colors=sh_coeffs,
            viewmats=torch.linalg.inv(camtoworld),
            Ks=K,
            width=W,
            height=H,
            sh_degree=sh_degree_active,
            near_plane=0.01,
            far_plane=1000.0,
            packed=True,   # required for MCMCStrategy (needs gaussian_ids in info)
        )
        renders = renders.squeeze(0)  # (H, W, 3)

        l1 = F.l1_loss(renders, pixels)

        # MCMC pre-backward: initialises state['binoms'] and other bookkeeping
        if strategy is not None:
            try:
                strategy.step_pre_backward(
                    params=splats,
                    optimizers=optimizers,
                    state=strategy_state,
                    step=step,
                    info=info,
                )
            except Exception as e:
                if step == 1:
                    log.warning("  MCMCStrategy step_pre_backward failed: %s — Gaussians will not grow", e)
                strategy = None

        l1.backward()

        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)

        # MCMC post-backward: relocate/add/remove Gaussians
        if strategy is not None:
            try:
                means_lr = optimizers["means"].param_groups[0]["lr"]
                strategy.step_post_backward(
                    params=splats,
                    optimizers=optimizers,
                    state=strategy_state,
                    step=step,
                    info=info,
                    lr=means_lr,
                )
            except Exception as e:
                if step == 1:
                    log.warning("  MCMCStrategy step_post_backward failed: %s — Gaussians will not grow", e)
                strategy = None

        if step % 500 == 0 or step == max_steps:
            log.info("    step %d/%d  loss=%.4f  Gaussians=%d",
                     step, max_steps, l1.item(), len(splats["means"]))


# ── Video render ─────────────────────────────────────────────────────────────

def render_video(workspace: Path, ply_path: Path, output_video: Path, cfg: dict,
                 side_by_side: bool = True) -> None:
    """
    Render the trained Gaussian Splatting model along the COLMAP camera trajectory
    and write an mp4 video.

    Parameters
    ----------
    workspace:     COLMAP workspace (must contain sparse/0/ and images/)
    ply_path:      Trained .splat.ply
    output_video:  Destination .mp4 path
    cfg:           Full pipeline config dict
    side_by_side:  If True, render at full resolution and place original RGB frame
                   next to the gsplat render (2W × H).  Default True to match
                   the gsplat_flythrough.mp4 format.
    """
    import time
    import torch
    import cv2
    import numpy as np
    from gsplat import rasterization
    from tqdm import tqdm

    gs_cfg = cfg["gaussian_splatting"]
    sh_degree = gs_cfg["sh_degree"]
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Always render at full resolution (data_factor=1) for quality
    render_factor = 1

    log.info("  render_video: loading COLMAP dataset...")
    t0 = time.time()
    dataset = _load_colmap_dataset(workspace, render_factor)
    if dataset is None:
        log.error("  render_video: could not load COLMAP dataset")
        return
    log.info("  render_video: COLMAP dataset loaded in %.2fs", time.time() - t0)

    log.info("  render_video: loading %d training images into memory...", len(dataset.train_indices))
    t0 = time.time()
    train_dataset = _build_train_dataset(dataset, render_factor, device)
    if not train_dataset:
        log.error("  render_video: no training views to render")
        return
    log.info("  render_video: images loaded in %.2fs", time.time() - t0)

    log.info("  render_video: loading splat PLY...")
    t0 = time.time()
    splats = _load_ply_splats(ply_path, device)
    if splats is None:
        log.error("  render_video: could not load splat PLY from %s", ply_path)
        return
    log.info("  render_video: PLY loaded in %.2fs", time.time() - t0)

    H = train_dataset[0]["height"]
    W = train_dataset[0]["width"]
    render_cfg = cfg.get("render", {})
    fps = render_cfg.get("fps", 30)

    out_W = W * 2 if side_by_side else W
    output_video.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_video), fourcc, fps, (out_W, H))

    log.info("  render_video: rendering %d frames at %dx%d%s → %s",
             len(train_dataset), out_W, H,
             " (side-by-side)" if side_by_side else "", output_video)

    n_gaussians = len(splats["means"])
    render_times = []

    with torch.no_grad():
        # Pre-compute SH coefficients and normalised quaternions once — not per frame
        sh_coeffs = torch.cat([splats["sh0"], splats["shN"]], dim=1)
        quats_norm = splats["quats"] / splats["quats"].norm(dim=-1, keepdim=True)
        scales_exp = torch.exp(splats["scales"])
        opacities_sig = torch.sigmoid(splats["opacities"])

        with tqdm(total=len(train_dataset), desc="Rendering frames",
                  unit="frame", dynamic_ncols=True) as pbar:
            for i, view in enumerate(train_dataset):
                camtoworld = view["camtoworld"].unsqueeze(0)
                K = view["K"].unsqueeze(0)

                t_frame = time.time()
                renders, _, _ = rasterization(
                    means=splats["means"],
                    quats=quats_norm,
                    scales=scales_exp,
                    opacities=opacities_sig,
                    colors=sh_coeffs,
                    viewmats=torch.linalg.inv(camtoworld),
                    Ks=K,
                    width=W,
                    height=H,
                    sh_degree=sh_degree,
                    near_plane=0.01,
                    far_plane=1000.0,
                    packed=True,
                )
                if device == "cuda":
                    torch.cuda.synchronize()
                render_ms = (time.time() - t_frame) * 1000
                render_times.append(render_ms)

                rendered = (renders.squeeze(0).clamp(0, 1).cpu().numpy() * 255).astype("uint8")
                rendered_bgr = cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)

                if side_by_side:
                    orig = (view["pixels"].cpu().numpy() * 255).astype("uint8")
                    orig_bgr = cv2.cvtColor(orig, cv2.COLOR_RGB2BGR)
                    frame = np.concatenate([orig_bgr, rendered_bgr], axis=1)
                else:
                    frame = rendered_bgr

                writer.write(frame)

                avg_ms = sum(render_times[-20:]) / len(render_times[-20:])
                pbar.set_postfix({
                    "gaussians": f"{n_gaussians:,}",
                    "ms/frame":  f"{render_ms:.0f}",
                    "avg20":     f"{avg_ms:.0f}",
                    "res":       f"{W}x{H}",
                })
                pbar.update(1)

    writer.release()
    if render_times:
        avg = sum(render_times) / len(render_times)
        p95 = sorted(render_times)[int(len(render_times) * 0.95)]
        log.info("  render_video: %.0f ms/frame avg, %.0f ms p95 over %d frames  (%d gaussians)",
                 avg, p95, len(render_times), n_gaussians)
    log.info("  render_video: saved %s", output_video)


def _load_ply_splats(ply_path: Path, device: str) -> dict | None:
    """Load a trained .splat.ply back into a splats dict for rendering."""
    import time
    import torch
    import numpy as np

    try:
        from plyfile import PlyData
    except ImportError:
        log.error("  plyfile not installed — install with: uv pip install plyfile")
        return None

    t0 = time.time()
    try:
        plydata = PlyData.read(str(ply_path))
    except Exception as exc:
        log.error("  Could not read PLY: %s", exc)
        return None
    log.info("  _load_ply_splats: PLY file read in %.2fs", time.time() - t0)

    v = plydata["vertex"]
    N = len(v)
    log.info("  _load_ply_splats: loading %d Gaussians from %s", N, ply_path.name)

    t1 = time.time()
    # Use numpy vectorised ops — avoids O(N*fields) Python-level iterations
    means = np.stack([np.asarray(v["x"]), np.asarray(v["y"]), np.asarray(v["z"])],
                     axis=1).astype(np.float32)

    # Scales stored as log; opacities stored as logit
    scales = np.stack([np.asarray(v[f"scale_{j}"]) for j in range(3)],
                      axis=1).astype(np.float32)
    quats = np.stack([np.asarray(v[f"rot_{j}"]) for j in range(4)],
                     axis=1).astype(np.float32)
    opacities = np.asarray(v["opacity"], dtype=np.float32)

    # SH: f_dc_0..2 → sh0 (N,1,3); f_rest_0.. → shN (N,C-1,3)
    field_names = v.data.dtype.names
    n_dc   = sum(1 for name in field_names if name.startswith("f_dc_"))
    n_rest = sum(1 for name in field_names if name.startswith("f_rest_"))

    sh0_np = np.stack([np.asarray(v[f"f_dc_{j}"])   for j in range(n_dc)],
                      axis=1).astype(np.float32)     # (N, 3)
    sh0_data = torch.from_numpy(sh0_np).reshape(N, 1, n_dc).to(device)  # (N, 1, 3)

    if n_rest > 0:
        shN_np = np.stack([np.asarray(v[f"f_rest_{j}"]) for j in range(n_rest)],
                          axis=1).astype(np.float32)  # (N, n_rest)
        # Standard 3DGS PLY stores f_rest as all-R coeffs, then all-G, then all-B
        n_coeffs = n_rest // 3
        shN_data = (torch.from_numpy(shN_np).to(device)
                         .reshape(N, 3, n_coeffs)
                         .permute(0, 2, 1))           # (N, C-1, 3)
    else:
        shN_data = torch.zeros(N, 0, 3, device=device)

    log.info("  _load_ply_splats: tensors built in %.2fs  (means=%s scales=%s sh0=%s shN=%s)",
             time.time() - t1,
             tuple(means.shape), tuple(scales.shape),
             tuple(sh0_data.shape), tuple(shN_data.shape))

    return {
        "means":     torch.from_numpy(means).to(device),
        "scales":    torch.from_numpy(scales).to(device),   # already log
        "quats":     torch.from_numpy(quats).to(device),
        "opacities": torch.from_numpy(opacities).to(device),  # already logit
        "sh0":       sh0_data,
        "shN":       shN_data,
    }


# ── PLY export ────────────────────────────────────────────────────────────────

def _export_ply(splats: dict, output_ply: Path) -> None:
    """Write Gaussian Splatting model as a .ply file."""
    import torch

    output_ply.parent.mkdir(parents=True, exist_ok=True)

    try:
        from gsplat.exporter import export_splats
        data = export_splats(
            means=splats["means"].detach(),
            scales=torch.exp(splats["scales"].detach()),
            quats=splats["quats"].detach(),
            opacities=torch.sigmoid(splats["opacities"].detach()),
            sh0=splats["sh0"].detach(),
            shN=splats["shN"].detach(),
            format="ply",
            save_to=str(output_ply),
        )
        if data is not None and not output_ply.exists():
            output_ply.write_bytes(data)
        return
    except (ImportError, AttributeError, TypeError) as e:
        log.warning("  export_splats failed (%s), falling back to manual PLY", e)

    # Fallback: write a standard 3DGS-compatible PLY
    _write_ply_fallback(splats, output_ply)


def _write_ply_fallback(splats: dict, output_ply: Path) -> None:
    """Write a 3DGS-compatible binary PLY file."""
    import torch

    means = splats["means"].detach().cpu().numpy()
    scales_raw = splats["scales"].detach().cpu().numpy()     # log space
    quats = splats["quats"].detach().cpu().numpy()
    opacities_raw = splats["opacities"].detach().cpu().numpy()  # logit space

    sh0 = splats["sh0"].detach().cpu()    # (N, 1, 3)
    shN = splats["shN"].detach().cpu()    # (N, C-1, 3)

    # Flatten SH: (N, C*3) → interleaved as (N, 3*C) per Gaussian viewer convention
    sh0_flat = sh0.permute(0, 2, 1).reshape(len(means), -1).numpy()  # (N, 3)
    shN_flat = shN.permute(0, 2, 1).reshape(len(means), -1).numpy()  # (N, 3*(C-1))

    N = len(means)
    n_sh_dc = sh0_flat.shape[1]
    n_sh_rest = shN_flat.shape[1]

    with open(output_ply, "wb") as f:
        header = (
            "ply\nformat binary_little_endian 1.0\n"
            f"element vertex {N}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property float nx\nproperty float ny\nproperty float nz\n"
        )
        for j in range(n_sh_dc):
            header += f"property float f_dc_{j}\n"
        for j in range(n_sh_rest):
            header += f"property float f_rest_{j}\n"
        header += "property float opacity\n"
        for j in range(scales_raw.shape[1]):
            header += f"property float scale_{j}\n"
        for j in range(quats.shape[1]):
            header += f"property float rot_{j}\n"
        header += "end_header\n"
        f.write(header.encode())

        # Build one contiguous float32 array (N × fields) and write in a single call —
        # avoids a per-row Python loop that is O(N) slow for large Gaussian counts.
        import numpy as np
        normals = np.zeros((N, 3), dtype=np.float32)
        rows = np.concatenate([
            means.astype(np.float32),
            normals,
            sh0_flat.astype(np.float32),
            shN_flat.astype(np.float32),
            opacities_raw.reshape(-1, 1).astype(np.float32),
            scales_raw.astype(np.float32),
            quats.astype(np.float32),
        ], axis=1)
        f.write(rows.tobytes())
