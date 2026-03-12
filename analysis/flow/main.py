#!/usr/bin/env python3
"""
Interactive RAFT optical-flow + feature-correlation debugger.

For a chosen ref/curr frame pair this tool shows:

  Left panel  — reference frame
    • Yellow arrow  : RAFT optical flow at the hovered pixel (actual displacement)

  Right panel — current frame
    • Heatmap overlay : cosine-similarity between the hovered pixel's RAFT feature
                        descriptor and every position in the current-frame feature map
    • Blue   dot      : argmax of the cosine-similarity map (best feature match)
    • Green  dot      : where RAFT says the pixel lands  (flow destination)

  HUD  (top-left)
    dx / dy / |v|  from RAFT flow
    feat-match pos and cosine score

Press F or ENTER to (re-)compute RAFT + extract feature maps.
Use --scale to shrink images and avoid GPU OOM.

Usage:
    cd server
    uv run python ../analysis/flow/main.py ../recordings/<name>.vis.pb \\
        --ref 45 --curr 57 --scale 0.5
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

_here        = Path(__file__).resolve()
_server_root = _here.parent.parent.parent / "server"
_project_root = _server_root.parent
sys.path.insert(0, str(_server_root))
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))

from proto import perceiver_pb2
from streamlog.protoio import ProtoIO
from motioncap.geometry import decode_frame_rgb

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

WIN = "RAFT Flow Debugger  (F/Enter=compute  ESC/q=quit)"

ARROW_SCALE   = 10          # visual magnification of flow arrows
ARROW_COLOR   = (0, 220, 255)   # yellow
ARROW_OUTLINE = (0, 0, 0)
DOT_RADIUS    = 8
HEATMAP_ALPHA = 0.55        # blend weight for correlation heatmap on right panel


# ── Helpers ────────────────────────────────────────────────────────────────────

def _shadow_text(img, text, pos, scale=0.6, thickness=2, fg=(255, 255, 255)):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), thickness + 1, cv2.LINE_AA)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale,
                fg, thickness, cv2.LINE_AA)


def _to_tensor(img: np.ndarray, device) -> tuple[torch.Tensor, int, int]:
    """uint8 RGB (H,W,3) → (1,3,H_pad,W_pad) float [0,1]; returns (t, orig_h, orig_w)."""
    h, w = img.shape[:2]
    pad_h = (8 - h % 8) % 8
    pad_w = (8 - w % 8) % 8
    t = torch.from_numpy(img).permute(2, 0, 1).float().div(255.0)
    if pad_h > 0 or pad_w > 0:
        t = F.pad(t, [0, pad_w, 0, pad_h])
    return t.unsqueeze(0).to(device), h, w


# ── RAFT + feature extraction ──────────────────────────────────────────────────

@torch.no_grad()
def compute_flow_and_features(
    img_ref: np.ndarray,
    img_curr: np.ndarray,
    model: torch.nn.Module,
    device: torch.device,
    iters: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Run RAFT and extract feature encoder maps.

    Returns:
        flow     (H, W, 2)  float32 — optical flow from ref to curr
        fmap1    (C, fH, fW) float32 — feature map for ref   (fH = H//8)
        fmap2    (C, fH, fW) float32 — feature map for curr
    """
    t1, orig_h, orig_w = _to_tensor(img_ref,  device)
    t2, _,      _      = _to_tensor(img_curr, device)

    # ── Feature maps via feature_encoder ──────────────────────────────────────
    # torchvision RAFT processes both images together for efficiency.
    # We extract fmap1 / fmap2 by calling the encoder on each separately.
    fmap1 = model.feature_encoder(t1)   # (1, C, H//8, W//8)
    fmap2 = model.feature_encoder(t2)

    # ── Full RAFT flow ─────────────────────────────────────────────────────────
    flow_preds = model(t1, t2, num_flow_updates=iters)
    flow = flow_preds[-1]                        # (1, 2, H_pad, W_pad)
    flow = flow[:, :, :orig_h, :orig_w]         # strip padding
    flow_np = flow.squeeze(0).permute(1, 2, 0).cpu().numpy()   # (H, W, 2)

    fmap1_np = fmap1.squeeze(0).cpu().numpy()   # (C, fH, fW)
    fmap2_np = fmap2.squeeze(0).cpu().numpy()

    return flow_np, fmap1_np, fmap2_np


def cosine_similarity_map(
    fmap1: np.ndarray,
    fmap2: np.ndarray,
    px: int, py: int,
) -> tuple[np.ndarray, tuple[int, int], float]:
    """
    Compute cosine similarity between fmap1[:,fy,fx] and every position in fmap2.

    fmap1/fmap2: (C, fH, fW)  float32
    px, py: pixel coordinate in the *image* space (will be divided by 8)

    Returns:
        sim_map   (fH, fW)  float32  cosine similarity in [-1, 1]
        best_pos  (img_x, img_y)     argmax position back in image coords
        best_score float
    """
    C, fH, fW = fmap1.shape
    fx = min(px // 8, fW - 1)
    fy = min(py // 8, fH - 1)

    query = fmap1[:, fy, fx]              # (C,)
    q_norm = np.linalg.norm(query)
    if q_norm < 1e-8:
        sim_map = np.zeros((fH, fW), dtype=np.float32)
        return sim_map, (px, py), 0.0

    # Reshape fmap2 to (C, fH*fW) and dot with query
    flat = fmap2.reshape(C, -1)           # (C, fH*fW)
    norms = np.linalg.norm(flat, axis=0)  # (fH*fW,)
    norms = np.where(norms < 1e-8, 1e-8, norms)

    sims = (query @ flat) / (q_norm * norms)   # (fH*fW,)
    sim_map = sims.reshape(fH, fW).astype(np.float32)

    best_flat = int(np.argmax(sim_map))
    best_fy, best_fx = divmod(best_flat, fW)
    best_score = float(sim_map[best_fy, best_fx])
    # Map back to image centre coords (×8 + 4)
    best_pos = (best_fx * 8 + 4, best_fy * 8 + 4)

    return sim_map, best_pos, best_score


def _sim_to_colormap(sim_map: np.ndarray, img_h: int, img_w: int) -> np.ndarray:
    """Upscale sim_map to (img_h, img_w), map to COLORMAP_JET, return BGR uint8."""
    # Normalise [-1,1] → [0,255]
    norm = ((sim_map + 1.0) / 2.0 * 255.0).clip(0, 255).astype(np.uint8)
    up = cv2.resize(norm, (img_w, img_h), interpolation=cv2.INTER_NEAREST)
    return cv2.applyColorMap(up, cv2.COLORMAP_JET)


# ── Main App ───────────────────────────────────────────────────────────────────

class App:
    def __init__(self, img_ref: np.ndarray, img_curr: np.ndarray,
                 ref_idx: int, curr_idx: int):
        self.img_ref  = img_ref
        self.img_curr = img_curr
        self.ref_idx  = ref_idx
        self.curr_idx = curr_idx

        self.H, self.W = img_ref.shape[:2]

        self._bgr_ref  = cv2.cvtColor(img_ref,  cv2.COLOR_RGB2BGR)
        self._bgr_curr = cv2.cvtColor(img_curr, cv2.COLOR_RGB2BGR)
        self._base     = np.concatenate([self._bgr_ref, self._bgr_curr], axis=1)

        # Computed data
        self._flow   = None    # (H, W, 2) float32
        self._fmap1  = None    # (C, fH, fW) float32
        self._fmap2  = None
        self._ready  = False

        # Interaction
        self._cursor = None    # (px, py) in image coords (left panel)
        self._model  = None
        self._device = None

    # ── Model / compute ────────────────────────────────────────────────────────

    def _ensure_model(self):
        if self._model is not None:
            return
        print("Loading RAFT model …")
        from motioncap.raft_motion import load_raft
        self._model, self._device = load_raft(variant="large")

    def compute(self):
        # Banner
        banner = self._base.copy()
        H, W = banner.shape[:2]
        cv2.rectangle(banner, (0, H // 2 - 40), (W, H // 2 + 40), (20, 20, 20), -1)
        _shadow_text(banner, "Computing RAFT flow + feature maps …",
                     (W // 2 - 280, H // 2 + 10), scale=0.9, fg=(0, 200, 255))
        cv2.imshow(WIN, banner)
        cv2.waitKey(1)

        self._ensure_model()

        print(f"Running RAFT: ref={self.ref_idx}  curr={self.curr_idx}  "
              f"image={self.W}×{self.H} …")
        try:
            self._flow, self._fmap1, self._fmap2 = compute_flow_and_features(
                self.img_ref, self.img_curr, self._model, self._device)
            fH, fW = self._fmap1.shape[1], self._fmap1.shape[2]
            print(f"Flow range: {self._flow[...,0].min():.2f}…{self._flow[...,0].max():.2f} dx, "
                  f"{self._flow[...,1].min():.2f}…{self._flow[...,1].max():.2f} dy")
            print(f"Feature maps: {self._fmap1.shape[0]}ch @ {fW}×{fH}  "
                  f"(image at {self.W}×{self.H}, 1/8 scale)")
            self._ready = True
        except torch.cuda.OutOfMemoryError as e:
            print(f"\nGPU OOM: {e}")
            print("Re-run with  --scale 0.5  to halve resolution.")
            return

        self._cursor = None
        self.render()

    # ── Rendering ──────────────────────────────────────────────────────────────

    def render(self):
        H, W_total = self._base.shape[:2]
        W = self.W

        # Right panel: curr frame (possibly with correlation heatmap)
        right = self._bgr_curr.copy()

        hud_lines = [
            f"ref={self.ref_idx}  curr={self.curr_idx}  "
            f"{'ready — hover for flow + features' if self._ready else 'press F to compute'}"
        ]

        if self._ready and self._cursor is not None:
            px, py = self._cursor
            px = int(np.clip(px, 0, W - 1))
            py = int(np.clip(py, 0, H - 1))

            # ── Flow arrow (left panel, drawn below) ──────────────────────────
            dx = float(self._flow[py, px, 0])
            dy = float(self._flow[py, px, 1])
            mag = float(np.sqrt(dx * dx + dy * dy))

            # ── Feature correlation on right panel ────────────────────────────
            sim_map, best_pos, best_score = cosine_similarity_map(
                self._fmap1, self._fmap2, px, py)

            heatmap_bgr = _sim_to_colormap(sim_map, H, W)
            right = cv2.addWeighted(right, 1 - HEATMAP_ALPHA, heatmap_bgr, HEATMAP_ALPHA, 0)

            # Green dot: RAFT flow destination
            fx_raft = int(round(px + dx))
            fy_raft = int(round(py + dy))
            if 0 <= fx_raft < W and 0 <= fy_raft < H:
                cv2.circle(right, (fx_raft, fy_raft), DOT_RADIUS + 2,
                           (0, 0, 0), -1)
                cv2.circle(right, (fx_raft, fy_raft), DOT_RADIUS,
                           (0, 255, 0), -1)

            # Blue dot: argmax feature match
            bx, by = best_pos
            if 0 <= bx < W and 0 <= by < H:
                cv2.circle(right, (bx, by), DOT_RADIUS + 2, (0, 0, 0), -1)
                cv2.circle(right, (bx, by), DOT_RADIUS,     (255, 80, 0), -1)

            hud_lines = [
                f"ref={self.ref_idx}  curr={self.curr_idx}  px=({px},{py})",
                f"RAFT flow   dx={dx:+.2f}  dy={dy:+.2f}  |v|={mag:.2f} px",
                f"Feat-match  ({bx},{by})  cos={best_score:.3f}  "
                f"(green=RAFT dest  blue=feat argmax)",
            ]

        # Composite: left panel with arrow
        left = self._bgr_ref.copy()
        if self._ready and self._cursor is not None:
            px, py = self._cursor
            px = int(np.clip(px, 0, W - 1))
            py = int(np.clip(py, 0, H - 1))
            dx = float(self._flow[py, px, 0])
            dy = float(self._flow[py, px, 1])

            pt1 = (px, py)
            pt2 = (int(px + dx * ARROW_SCALE), int(py + dy * ARROW_SCALE))
            cv2.arrowedLine(left, pt1, pt2, ARROW_OUTLINE, 4, cv2.LINE_AA, tipLength=0.25)
            cv2.arrowedLine(left, pt1, pt2, ARROW_COLOR,   2, cv2.LINE_AA, tipLength=0.25)
            cv2.circle(left, (px, py), 4, ARROW_OUTLINE, -1)
            cv2.circle(left, (px, py), 3, ARROW_COLOR,   -1)

        img = np.concatenate([left, right], axis=1)

        # ── HUD ───────────────────────────────────────────────────────────────
        for i, line in enumerate(hud_lines):
            _shadow_text(img, line, (10, 24 + i * 28))

        # ── Bottom status bar ─────────────────────────────────────────────────
        BAR_H = 30
        cv2.rectangle(img, (0, H - BAR_H), (W * 2, H), (30, 30, 30), -1)
        if not self._ready:
            status = "F / Enter — compute RAFT flow + feature maps"
            fg = (0, 200, 255)
        else:
            status = "hover left panel — F recompute — ESC/q quit"
            fg = (100, 200, 100)
        cv2.putText(img, status, (10, H - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, fg, 1, cv2.LINE_AA)

        cv2.imshow(WIN, img)

    # ── Mouse callback ─────────────────────────────────────────────────────────

    def on_mouse(self, event, x, y, flags, param):
        if not self._ready:
            return
        if x >= self.W:   # right panel: ignore
            return
        if event == cv2.EVENT_MOUSEMOVE:
            self._cursor = (x, y)
            self.render()

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WIN, self.on_mouse)
        self.render()

        print("Press F or Enter to compute.  Hover left panel once ready.")

        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (27, ord('q')):
                break
            elif key in (ord('f'), 13):   # f or Enter
                self.compute()

        cv2.destroyAllWindows()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RAFT flow + feature correlation debugger")
    parser.add_argument("recording",       help="Path to .vis.pb recording")
    parser.add_argument("--ref",  type=int, default=45,  help="Reference frame index")
    parser.add_argument("--curr", type=int, default=57,  help="Current frame index")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Resize factor (e.g. 0.5) to reduce GPU memory")
    args = parser.parse_args()

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    print(f"Loading {rec_path.name} …")
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames found")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames")

    n = len(frames)
    ref_idx  = max(0, min(args.ref,  n - 1))
    curr_idx = max(0, min(args.curr, n - 1))
    if ref_idx == curr_idx:
        print("ref and curr must differ")
        sys.exit(1)

    print(f"Frames: ref={ref_idx}  curr={curr_idx}")

    img_ref  = decode_frame_rgb(frames[ref_idx])
    img_curr = decode_frame_rgb(frames[curr_idx])

    if args.scale != 1.0:
        s = args.scale
        h, w = img_ref.shape[:2]
        new_w = int(w * s)
        new_h = int(h * s)
        img_ref  = cv2.resize(img_ref,  (new_w, new_h), interpolation=cv2.INTER_AREA)
        img_curr = cv2.resize(img_curr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        print(f"Scaled to {new_w}×{new_h}")

    App(img_ref, img_curr, ref_idx, curr_idx).run()


if __name__ == "__main__":
    main()
