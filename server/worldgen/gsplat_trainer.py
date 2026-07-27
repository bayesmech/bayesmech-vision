"""Reusable Gaussian Splatting training helpers for Worldgen.

Trains a 3D Gaussian Splatting model from Worldgen camera and point-cloud
data and exports the result as a .ply file. Uses the MCMC training strategy
to cap the total Gaussian count.

Requirements
------------
    uv pip install gsplat --index-url https://docs.gsplat.studio/whl/pt27cu126
    # or: uv pip install gsplat  (builds CUDA kernels on first run)
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# SH DC coefficient: (rgb - 0.5) / C0  where C0 = sqrt(1/(4*pi)) ≈ 0.28209
_SH_C0 = 0.28209479177387814


def _rgb_to_sh(rgb):
    """Convert linear RGB → spherical harmonics DC component (torch)."""
    return (rgb - 0.5) / _SH_C0


def train_splat_dataset(dataset, output_ply: Path, cfg: dict) -> None:
    """Train a 3DGS model from a Worldgen camera and point-cloud dataset."""
    import os
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch
    from torch.optim import Adam

    try:
        from gsplat import rasterization
        from gsplat.strategy import MCMCStrategy
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

    points = torch.from_numpy(dataset.points).float().to(device)
    point_colors = torch.from_numpy(dataset.points_rgb).float().to(device) / 255.0

    log.info("  gsplat: %d Worldgen points, %d training images",
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

def _scene_scale(dataset) -> float:
    """Estimate scene scale from the largest point-cloud extent."""
    pts = dataset.points
    if len(pts) == 0:
        return 1.0
    extents = pts.max(0) - pts.min(0)
    return float(max(extents.max(), 1.0))


def _build_train_dataset(dataset, data_factor: int, device: str) -> list[dict]:
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
    """Initialise splat parameters from a Worldgen point cloud."""
    import torch

    N = len(points)

    # Scale from nearest-neighbour distances (log space)
    dist = _knn_dist(points, k=4)
    neighbour_dist = dist[:, 1:] if dist.shape[1] > 1 else dist
    scales = torch.log(torch.sqrt(neighbour_dist.mean(dim=-1, keepdim=True).clamp_min(1e-6))).repeat(1, 3)

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
    if len(pts) <= 1:
        return torch.ones((len(pts), 1), device=points.device, dtype=points.dtype) * 0.01
    k = min(k, len(pts))

    if len(pts) > 50000:
        try:
            from sklearn.neighbors import NearestNeighbors
            pts_np = pts.detach().cpu().numpy()
            nn = NearestNeighbors(n_neighbors=k, algorithm="auto", n_jobs=-1)
            nn.fit(pts_np)
            dist, _ = nn.kneighbors(pts_np, return_distance=True)
            return torch.from_numpy(dist).to(device=points.device, dtype=points.dtype)
        except Exception as exc:
            log.warning("  sklearn nearest-neighbour init failed (%s), falling back to torch cdist", exc)

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
