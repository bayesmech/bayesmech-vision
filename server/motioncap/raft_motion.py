"""
RAFT-based motion residual computation.

Pipeline per frame pair:
  1. Run RAFT to get dense optical flow (ref → curr).
  2. Use ARCore camera pose + per-pixel depth to compute the *expected* flow
     for every pixel assuming the world is completely static.
  3. Residual = actual_flow − expected_flow.
     Static background → residual ≈ 0.
     Independently moving objects → large residual.

The caller is responsible for thresholding, blurring, and normalisation so
that global (across-video) normalisation can be applied correctly.
"""

from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

_models_dir = Path(__file__).parent / "models" / "raft"


# ── Model loading ─────────────────────────────────────────────────────────────

def load_raft(variant: str = "large",
              device: torch.device | None = None) -> tuple[torch.nn.Module, torch.device]:
    """
    Load a RAFT model.

    Checks for a locally downloaded weight file at
        server/motioncap/models/raft/raft_{variant}.pth
    first; falls back to torchvision's auto-download otherwise.

    Returns (model, device).
    """
    from torchvision.models.optical_flow import (
        raft_large, raft_small,
        Raft_Large_Weights, Raft_Small_Weights,
    )

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    local_path = _models_dir / f"raft_{variant}.pth"

    if local_path.exists():
        print(f"Loading RAFT-{variant} from local weights …")
        model = raft_large() if variant == "large" else raft_small()
        state_dict = torch.load(local_path, map_location="cpu")
        model.load_state_dict(state_dict)
    else:
        print(f"Local weights not found — downloading RAFT-{variant} via torchvision …")
        print("  (run  uv run python motioncap/download_models.py  to pre-cache)")
        if variant == "large":
            model = raft_large(weights=Raft_Large_Weights.DEFAULT)
        else:
            model = raft_small(weights=Raft_Small_Weights.DEFAULT)

    model = model.to(device).eval()
    print(f"RAFT-{variant} ready on {device}")
    return model, device


# ── Image → tensor ────────────────────────────────────────────────────────────

def _to_tensor(img: np.ndarray,
               device: torch.device) -> tuple[torch.Tensor, int, int]:
    """
    Convert an RGB uint8 (H, W, 3) array to a (1, 3, H_pad, W_pad) float
    tensor in [0, 255] for torchvision RAFT.

    H and W are zero-padded to the next multiple of 8 as required by RAFT.

    Returns (tensor, original_h, original_w).
    """
    h, w = img.shape[:2]
    pad_h = (8 - h % 8) % 8
    pad_w = (8 - w % 8) % 8
    t = torch.from_numpy(img).permute(2, 0, 1).float().div(255.0)   # [0, 1]
    if pad_h > 0 or pad_w > 0:
        t = F.pad(t, [0, pad_w, 0, pad_h])
    return t.unsqueeze(0).to(device), h, w


# ── Dense optical flow ────────────────────────────────────────────────────────

@torch.no_grad()
def compute_flow(img_ref: np.ndarray,
                 img_curr: np.ndarray,
                 model: torch.nn.Module,
                 device: torch.device,
                 iters: int = 20) -> np.ndarray:
    """
    Run RAFT and return the dense optical flow from img_ref to img_curr.

    flow[y, x] = (dx, dy): the pixel at (x, y) in img_ref is found at
    (x + dx, y + dy) in img_curr.

    Returns flow  (H, W, 2)  float32.
    """
    t_ref,  orig_h, orig_w = _to_tensor(img_ref,  device)
    t_curr, _,      _      = _to_tensor(img_curr, device)

    flow_preds = model(t_ref, t_curr, num_flow_updates=iters)
    flow = flow_preds[-1]                      # finest resolution: (1, 2, H_pad, W_pad)
    flow = flow[:, :, :orig_h, :orig_w]       # strip padding
    return flow.squeeze(0).permute(1, 2, 0).cpu().numpy()   # (H, W, 2)


# ── Pose-based background flow prediction ─────────────────────────────────────

def _pose_predicted_flow(h: int, w: int,
                         K: np.ndarray,
                         R_ref: np.ndarray, t_ref: np.ndarray,
                         R_curr: np.ndarray, t_curr: np.ndarray,
                         depth: np.ndarray | None,
                         assumed_depth: float) -> np.ndarray:
    """
    Compute the optical flow every pixel *would* have if the scene were
    completely static and only the camera moved.

    ARCore pose convention (as used throughout geometry.py):
      - R is camera-to-world rotation  (R.T = world-to-camera)
      - t is camera position in world frame
      → P_cam = R.T @ (P_world − t)

    Args:
        h, w:           image dimensions
        K:              3×3 camera intrinsic matrix
        R_ref, t_ref:   ref-frame camera pose (cam2world R, world position t)
        R_curr, t_curr: curr-frame camera pose
        depth:          per-pixel depth in metres (H, W) float32, or None
        assumed_depth:  fallback depth for invalid/missing depth pixels

    Returns predicted_flow  (H, W, 2)  float64
    """
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    # Pixel grid (source positions in ref frame)
    u, v = np.meshgrid(np.arange(w, dtype=np.float64),
                       np.arange(h, dtype=np.float64))

    # Per-pixel depth — fall back to assumed_depth where invalid
    if depth is not None:
        z = depth.astype(np.float64)
        z[z < 0.01] = assumed_depth
    else:
        z = np.full((h, w), assumed_depth, dtype=np.float64)

    # Unproject: 3D points in ref camera frame
    #   P_ref_cam = [(u−cx)*z/fx, (v−cy)*z/fy, z]
    X = (u - cx) * z / fx
    Y = (v - cy) * z / fy
    Z = z
    pts_ref = np.stack([X.ravel(), Y.ravel(), Z.ravel()])   # (3, H*W)

    # Transform to curr camera frame:
    #   P_world  = R_ref @ P_ref_cam + t_ref          (ref cam → world)
    #   P_curr   = R_curr.T @ (P_world − t_curr)      (world → curr cam)
    # Combined:
    #   P_curr   = (R_curr.T @ R_ref) @ P_ref_cam  +  R_curr.T @ (t_ref − t_curr)
    R_ref_to_curr = R_curr.T @ R_ref
    t_ref_to_curr = R_curr.T @ (t_ref - t_curr)

    pts_curr = R_ref_to_curr @ pts_ref + t_ref_to_curr[:, None]   # (3, H*W)

    # Reproject into curr image
    pz = pts_curr[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u_curr = np.where(pz > 0.01, fx * pts_curr[0] / pz + cx, u.ravel())
        v_curr = np.where(pz > 0.01, fy * pts_curr[1] / pz + cy, v.ravel())

    pred_flow = np.stack([u_curr - u.ravel(),
                          v_curr - v.ravel()], axis=1).reshape(h, w, 2)
    return pred_flow


# ── Shared background subtraction ────────────────────────────────────────────

def _subtract_background(
    flow: np.ndarray,
    h: int, w: int,
    cfg: dict,
    K: np.ndarray | None,
    R_ref: np.ndarray | None, t_ref: np.ndarray | None,
    R_curr: np.ndarray | None, t_curr: np.ndarray | None,
    depth: np.ndarray | None,
    subtraction_mode: str,
) -> np.ndarray:
    """Apply background subtraction to a flow field; return float32 residual (H, W)."""
    assumed_depth = cfg.get("raft", {}).get("assumed_depth_m", 1.5)
    if subtraction_mode == "absolute":
        res_u, res_v = flow[..., 0], flow[..., 1]
    elif (subtraction_mode == "pose"
          and K is not None and R_ref is not None and R_curr is not None):
        pred  = _pose_predicted_flow(h, w, K, R_ref, t_ref, R_curr, t_curr,
                                     depth, assumed_depth)
        res_u = flow[..., 0] - pred[..., 0]
        res_v = flow[..., 1] - pred[..., 1]
    else:
        res_u = flow[..., 0] - float(np.median(flow[..., 0]))
        res_v = flow[..., 1] - float(np.median(flow[..., 1]))
    return np.sqrt(res_u ** 2 + res_v ** 2).astype(np.float32)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_residual(img_ref: np.ndarray,
                     img_curr: np.ndarray,
                     model: torch.nn.Module,
                     device: torch.device,
                     cfg: dict,
                     K: np.ndarray | None = None,
                     R_ref: np.ndarray | None = None,
                     t_ref: np.ndarray | None = None,
                     R_curr: np.ndarray | None = None,
                     t_curr: np.ndarray | None = None,
                     depth: np.ndarray | None = None,
                     subtraction_mode: str = "pose") -> tuple[np.ndarray, float]:
    """
    Compute per-pixel motion residual magnitude.

    subtraction_mode:
        "absolute"  — raw flow magnitude; no background subtraction
        "pose"      — subtract per-pixel background predicted from ARCore pose + depth
                      (falls back to "consensus" when pose is unavailable)
        "consensus" — subtract per-frame median flow (robust estimate of camera translation)

    Returns:
        residual  float32 (H, W) — raw residual in pixels (not thresholded / normalised)
        max_raw   float           — peak residual value across the frame
    """
    iters = cfg.get("raft", {}).get("iters", 20)
    h, w  = img_ref.shape[:2]
    flow  = compute_flow(img_ref, img_curr, model, device, iters=iters)
    residual = _subtract_background(
        flow, h, w, cfg, K, R_ref, t_ref, R_curr, t_curr, depth, subtraction_mode)
    return residual, float(residual.max())


@torch.no_grad()
def compute_residual_and_flow_small(
    img_ref: np.ndarray,
    img_curr: np.ndarray,
    model: torch.nn.Module,
    device: torch.device,
    cfg: dict,
    K: np.ndarray | None = None,
    R_ref: np.ndarray | None = None,
    t_ref: np.ndarray | None = None,
    R_curr: np.ndarray | None = None,
    t_curr: np.ndarray | None = None,
    depth: np.ndarray | None = None,
    subtraction_mode: str = "pose",
) -> tuple[np.ndarray, float, np.ndarray]:
    """
    Like compute_residual but also returns the raw flow downsampled to
    feature-map resolution for use as centroid motion predictors.

    Returns:
        residual    float32 (H, W)      — background-subtracted magnitude
        max_raw     float               — peak residual value
        flow_small  float16 (H//8, W//8, 2) — raw RAFT flow (dx, dy) at 1/8 scale
    """
    iters = cfg.get("raft", {}).get("iters", 20)
    h, w  = img_ref.shape[:2]
    flow  = compute_flow(img_ref, img_curr, model, device, iters=iters)

    # Downsample raw flow to feature-map resolution
    fh, fw = h // 8, w // 8
    flow_u = cv2.resize(flow[..., 0].astype(np.float32), (fw, fh),
                        interpolation=cv2.INTER_AREA)
    flow_v = cv2.resize(flow[..., 1].astype(np.float32), (fw, fh),
                        interpolation=cv2.INTER_AREA)
    flow_small = np.stack([flow_u, flow_v], axis=-1).astype(np.float16)

    residual = _subtract_background(
        flow, h, w, cfg, K, R_ref, t_ref, R_curr, t_curr, depth, subtraction_mode)
    return residual, float(residual.max()), flow_small
