#!/usr/bin/env python3
"""
Interactive homography + optical-flow analysis tool.

Shows two frames side-by-side (reference frame and frame N frames later).
Hover mouse on either image to see the corresponding mapped point
(large red dot) in the other image.

Press F (or click the "Compute Flow" button) to run RAFT on the current frame
pair.  Once computed, hovering anywhere draws a yellow flow arrow at the cursor
and shows the dx / dy values in the HUD.

Usage:
    cd server
    uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb

Controls:
    Trackbar "Frame"    — reference frame index
    n / b               — cycle interval forward / backward
    1–6                 — select method (auto/depth/plane/points/pose/flow)
    f / click "Flow"    — compute RAFT optical flow for this pair
    ESC / q             — quit
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

_here        = Path(__file__).resolve()
_server_root = _here.parent.parent.parent / "server"
_project_root = _server_root.parent
sys.path.insert(0, str(_server_root))
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))

from proto import perceiver_pb2
from streamlog.protoio import ProtoIO
from motioncap.geometry import (
    decode_frame_rgb, decode_depth, pose_components, pixel_map,
)
from motioncap.raft_motion import _pose_predicted_flow

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

_cfg_path = _server_root / "motioncap" / "motioncap_config.yaml"
with open(_cfg_path) as _f:
    _CFG = yaml.safe_load(_f)

METHODS    = ["auto", "depth", "plane", "points", "pose", "flow"]
FLOW_MODES = ["absolute", "pose", "consensus"]   # m key cycles these
WIN        = "Homography Analysis  (ESC/q to quit)"

DOT_RADIUS    = 12
DOT_COLOR     = (0, 0, 255)    # BGR red
ARROW_SCALE   = 8              # pixels of arrow per pixel of actual flow
ARROW_COLOR   = (0, 220, 255)  # yellow
ARROW_OUTLINE = (0, 0, 0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _shadow_text(img, text, pos, scale=0.6, thickness=2, fg=(255, 255, 255)):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale,
                (0, 0, 0), thickness + 1, cv2.LINE_AA)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale,
                fg, thickness, cv2.LINE_AA)


# ── Main App ──────────────────────────────────────────────────────────────────

class App:
    def __init__(self, frames, intervals, K):
        self.frames    = frames
        self.intervals = intervals
        self.K         = K
        self.n         = len(frames)

        # Trackbar / method state
        self.frame_idx    = 0
        self.interval_idx = 0
        self.method_idx   = 0

        # Cached render state
        self._ref_idx  = -1
        self._cur_idx  = -1
        self._method   = ""
        self._base     = None      # (H, W*2, 3) BGR side-by-side, no overlays
        self._H_frame  = 0
        self._W_frame  = 0

        # Homography hover dots
        self._dot_left  = None    # (x, y) on left panel
        self._dot_right = None    # (x, y) on right panel

        # Flow state
        self._raft_model  = None
        self._raft_device = None
        self._flow        = None     # np.ndarray (H, W, 2) float32 or None
        self._flow_pair   = (-1, -1) # (ref_idx, curr_idx) for cached flow
        self._pred_pose   = None     # (H, W, 2) pose-predicted background flow
        self._pred_cons   = None     # (H, W, 2) consensus (median) background flow
        self._flow_mode   = 0        # index into FLOW_MODES
        self._cursor_px   = None     # (px, py) panel-local integer coords
        self._cursor_pan  = None     # "left" or "right"

        # Flow button rect — populated during render, used for click detection
        self._flow_btn    = None     # (x1, y1, x2, y2)

    # ── Frame pair ────────────────────────────────────────────────────────────

    @property
    def ref_idx(self):
        return self.frame_idx

    @property
    def curr_idx(self):
        delta = self.intervals[self.interval_idx][1]
        return min(self.frame_idx + delta, self.n - 1)

    @property
    def method(self):
        return METHODS[self.method_idx]

    # ── Flow computation ──────────────────────────────────────────────────────

    def _compute_flow(self):
        ri, ci = self.ref_idx, self.curr_idx
        if ri == ci:
            return

        # Show a blocking "computing" banner before RAFT runs
        if self._base is not None:
            banner = self._base.copy()
            H, W   = banner.shape[:2]
            cv2.rectangle(banner, (0, H // 2 - 35), (W, H // 2 + 35), (20, 20, 20), -1)
            _shadow_text(banner, "Computing RAFT optical flow  …",
                         (W // 2 - 240, H // 2 + 10), scale=0.9, fg=(0, 200, 255))
            cv2.imshow(WIN, banner)
            cv2.waitKey(1)

        # Load model lazily (once per session)
        if self._raft_model is None:
            print("Loading RAFT model …")
            from motioncap.raft_motion import load_raft
            variant = _CFG.get("raft", {}).get("variant", "large")
            self._raft_model, self._raft_device = load_raft(variant=variant)

        from motioncap.raft_motion import compute_flow
        iters    = _CFG.get("raft", {}).get("iters", 20)
        assumed_d = _CFG.get("raft", {}).get("assumed_depth_m", 1.5)
        rgb_ref  = decode_frame_rgb(self.frames[ri])
        rgb_curr = decode_frame_rgb(self.frames[ci])

        print(f"Running RAFT on frames {ri} → {ci} …")
        self._flow      = compute_flow(rgb_ref, rgb_curr,
                                       self._raft_model, self._raft_device,
                                       iters=iters)
        self._flow_pair = (ri, ci)
        self._cursor_px = None

        # Precompute background flows for the two subtraction modes
        h, w = rgb_ref.shape[:2]

        # Consensus: per-frame median (robust camera-translation estimate)
        med_u = float(np.median(self._flow[..., 0]))
        med_v = float(np.median(self._flow[..., 1]))
        self._pred_cons = np.stack(
            [np.full((h, w), med_u), np.full((h, w), med_v)], axis=-1
        ).astype(np.float32)

        # Pose: use ARCore pose + depth if available
        R_ref, t_ref   = pose_components(self.frames[ri])
        R_curr, t_curr = pose_components(self.frames[ci])
        if R_ref is not None and R_curr is not None and self.K is not None:
            depth = decode_depth(self.frames[ci], h, w)
            self._pred_pose = _pose_predicted_flow(
                h, w, self.K,
                R_ref, t_ref, R_curr, t_curr,
                depth, assumed_d,
            ).astype(np.float32)
            print("Pose background computed.")
        else:
            self._pred_pose = self._pred_cons.copy()
            print("No pose — pose mode will use consensus fallback.")

        print("Flow ready.")
        self.show()

    def _flow_valid(self):
        """True when cached flow matches the currently displayed frame pair."""
        return (self._flow is not None
                and self._flow_pair == (self.ref_idx, self.curr_idx))

    def _display_flow(self, px: int, py: int) -> tuple[float, float]:
        """Return (dx, dy) at pixel (px, py) for the currently active flow mode."""
        dx = float(self._flow[py, px, 0])
        dy = float(self._flow[py, px, 1])
        mode = FLOW_MODES[self._flow_mode]
        if mode == "pose" and self._pred_pose is not None:
            dx -= float(self._pred_pose[py, px, 0])
            dy -= float(self._pred_pose[py, px, 1])
        elif mode == "consensus" and self._pred_cons is not None:
            dx -= float(self._pred_cons[py, px, 0])
            dy -= float(self._pred_cons[py, px, 1])
        # "absolute" → no subtraction
        return dx, dy

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _rebuild_base(self):
        ri, ci   = self.ref_idx, self.curr_idx
        method   = self.method
        if ri == self._ref_idx and ci == self._cur_idx and method == self._method:
            return

        rgb_ref  = decode_frame_rgb(self.frames[ri])
        rgb_curr = decode_frame_rgb(self.frames[ci])
        self._H_frame, self._W_frame = rgb_ref.shape[:2]

        bgr_ref  = cv2.cvtColor(rgb_ref,  cv2.COLOR_RGB2BGR)
        bgr_curr = cv2.cvtColor(rgb_curr, cv2.COLOR_RGB2BGR)
        self._base = np.concatenate([bgr_ref, bgr_curr], axis=1)

        self._ref_idx = ri
        self._cur_idx = ci
        self._method  = method
        self._dot_left = self._dot_right = None
        # Invalidate precomputed backgrounds for the old pair
        self._pred_pose = None
        self._pred_cons = None

    def _render(self):
        img = self._base.copy()
        H, W = img.shape[:2]
        Wf   = self._W_frame

        # ── Homography dots ───────────────────────────────────────────────────
        if self._dot_left is not None:
            cv2.circle(img, self._dot_left, DOT_RADIUS, DOT_COLOR, -1)
        if self._dot_right is not None:
            x, y = self._dot_right
            cv2.circle(img, (x + Wf, y), DOT_RADIUS, DOT_COLOR, -1)

        # ── Flow arrow at cursor ──────────────────────────────────────────────
        flow_text = None
        if self._flow_valid() and self._cursor_px is not None:
            px, py = self._cursor_px
            px = int(np.clip(px, 0, Wf - 1))
            py = int(np.clip(py, 0, self._H_frame - 1))

            dx, dy = self._display_flow(px, py)
            mag = float(np.sqrt(dx * dx + dy * dy))

            x_off = 0 if self._cursor_pan == "left" else Wf
            pt1   = (px + x_off,                        py)
            pt2   = (int(px + dx * ARROW_SCALE) + x_off, int(py + dy * ARROW_SCALE))

            # Outline then coloured arrow
            cv2.arrowedLine(img, pt1, pt2, ARROW_OUTLINE, 4, cv2.LINE_AA, tipLength=0.3)
            cv2.arrowedLine(img, pt1, pt2, ARROW_COLOR,   2, cv2.LINE_AA, tipLength=0.3)

            flow_text = (f"[{FLOW_MODES[self._flow_mode]}]  "
                         f"dx={dx:+.1f}  dy={dy:+.1f}  |v|={mag:.1f} px")

        # ── Top-left status ───────────────────────────────────────────────────
        label = (f"ref={self.ref_idx}  curr={self.curr_idx}  "
                 f"interval={self.intervals[self.interval_idx][0]}")
        if self._flow_valid():
            label += f"  flow={FLOW_MODES[self._flow_mode]}"
        _shadow_text(img, label, (10, 24))
        if flow_text:
            _shadow_text(img, flow_text, (10, 52), fg=ARROW_COLOR)

        # ── Bottom bar ────────────────────────────────────────────────────────
        BAR_H  = 32
        font   = cv2.FONT_HERSHEY_SIMPLEX
        fsc    = 0.55
        fth    = 1
        pad_x  = 10

        cv2.rectangle(img, (0, H - BAR_H), (W, H), (30, 30, 30), -1)

        # Method buttons
        x_cur = pad_x
        for idx, name in enumerate(METHODS):
            text = f"{idx+1}:{name}"
            (tw, th), _ = cv2.getTextSize(text, font, fsc, fth)
            active = (idx == self.method_idx)
            if active:
                cv2.rectangle(img,
                              (x_cur - 4, H - BAR_H + 4),
                              (x_cur + tw + 4, H - 6),
                              (220, 180, 0), -1)
                fg = (0, 0, 0)
            else:
                fg = (180, 180, 180)
            cv2.putText(img, text, (x_cur, H - BAR_H + th + 4),
                        font, fsc, fg, fth, cv2.LINE_AA)
            x_cur += tw + 18

        # "Compute Flow" button — right-aligned, colour reflects state
        btn_text  = "F: Compute Flow"
        (bw, bh), _ = cv2.getTextSize(btn_text, font, fsc, fth)
        btn_x1    = W - bw - pad_x * 2 - 8
        btn_y1    = H - BAR_H + 4
        btn_x2    = W - pad_x
        btn_y2    = H - 6

        if self._flow_valid():
            btn_bg, btn_fg = (0, 140, 0), (220, 255, 220)   # green = ready
        elif self._flow is not None:
            btn_bg, btn_fg = (0, 80, 130), (200, 200, 255)  # blue = stale pair
        else:
            btn_bg, btn_fg = (60, 60, 60), (200, 200, 200)  # grey = not computed

        cv2.rectangle(img, (btn_x1, btn_y1), (btn_x2, btn_y2), btn_bg, -1)
        cv2.putText(img, btn_text,
                    (btn_x1 + 4, H - BAR_H + bh + 4),
                    font, fsc, btn_fg, fth, cv2.LINE_AA)

        self._flow_btn = (btn_x1, btn_y1, btn_x2, btn_y2)

        # Hint text
        if self._flow_valid():
            hint = "hover for flow  |  m mode  |  n/b interval  |  1-6 method"
        else:
            hint = "n/b interval  |  1-6 method"
        (hw, _), _ = cv2.getTextSize(hint, font, 0.42, 1)
        hint_x = max(x_cur + 10, btn_x1 - hw - 20)
        cv2.putText(img, hint, (hint_x, H - 10),
                    font, 0.42, (100, 100, 100), 1, cv2.LINE_AA)

        return img

    def show(self):
        self._rebuild_base()
        cv2.imshow(WIN, self._render())

    # ── Mouse callback ────────────────────────────────────────────────────────

    def on_mouse(self, event, x, y, flags, param):
        if self._base is None:
            return

        Wf = self._W_frame
        on_left = x < Wf
        pan = "left" if on_left else "right"
        px  = int(x if on_left else x - Wf)
        py  = int(y)

        redraw = False

        # ── Left-click on the Flow button ─────────────────────────────────────
        if event == cv2.EVENT_LBUTTONDOWN:
            if self._flow_btn:
                x1, y1, x2, y2 = self._flow_btn
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self._compute_flow()
                    return

        # ── Hover: homography dot + flow arrow ────────────────────────────────
        if event in (cv2.EVENT_MOUSEMOVE, cv2.EVENT_LBUTTONDOWN):
            # Flow arrow: update cursor position
            if self._flow_valid():
                if (self._cursor_px != (px, py) or self._cursor_pan != pan):
                    self._cursor_px  = (px, py)
                    self._cursor_pan = pan
                    redraw = True

            # Homography dot
            ri, ci = self.ref_idx, self.curr_idx
            frame_ref  = self.frames[ri]
            frame_curr = self.frames[ci]
            R_ref,  t_ref  = pose_components(frame_ref)
            R_curr, t_curr = pose_components(frame_curr)

            if R_ref is not None and R_curr is not None:
                if on_left:
                    mapped = pixel_map(float(px), float(py),
                                       frame_ref, frame_curr,
                                       R_ref, t_ref, R_curr, t_curr,
                                       self.K, _CFG, self.method)
                    self._dot_left  = (px, py)
                    self._dot_right = ((int(round(mapped[0])), int(round(mapped[1])))
                                       if mapped else None)
                else:
                    mapped = pixel_map(float(px), float(py),
                                       frame_curr, frame_ref,
                                       R_curr, t_curr, R_ref, t_ref,
                                       self.K, _CFG, self.method)
                    self._dot_right = (px, py)
                    self._dot_left  = ((int(round(mapped[0])), int(round(mapped[1])))
                                       if mapped else None)
                redraw = True

            if redraw:
                cv2.imshow(WIN, self._render())

    # ── Trackbar callback ─────────────────────────────────────────────────────

    def on_frame(self, val):
        self.frame_idx = val
        self.show()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WIN, self.on_mouse)
        cv2.createTrackbar("Frame", WIN, 0, self.n - 1, self.on_frame)

        self.show()

        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (27, ord('q')):
                break
            elif key == ord('f'):
                self._compute_flow()
            elif key == ord('m'):
                if self._flow_valid():
                    self._flow_mode = (self._flow_mode + 1) % len(FLOW_MODES)
                    self.show()
            elif key == ord('n'):
                self.interval_idx = (self.interval_idx + 1) % len(self.intervals)
                self.show()
            elif key == ord('b'):
                self.interval_idx = (self.interval_idx - 1) % len(self.intervals)
                self.show()
            elif ord('1') <= key <= ord('6'):
                idx = key - ord('1')
                if idx < len(METHODS):
                    self.method_idx = idx
                    self.show()

        cv2.destroyAllWindows()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <recording.vis.pb>")
        sys.exit(1)

    rec_path = Path(sys.argv[1]).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    print(f"Loading {rec_path.name} …")
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames found")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames")

    K_cache: list = []
    for f in frames:
        intr = f.camera_intrinsics
        if intr and intr.fx > 0:
            K_cache.append(np.array([
                [intr.fx, 0,       intr.cx],
                [0,       intr.fy, intr.cy],
                [0,       0,       1      ],
            ], dtype=np.float64))
            break
    if not K_cache:
        print("No camera intrinsics found in recording")
        sys.exit(1)

    intervals = compute_intervals(frames)
    print(f"Native FPS ≈ {intervals[1][1] / 0.5:.1f}")
    print(f"Intervals: {[i[0] for i in intervals]}")
    print("Controls: drag 'Frame' | n/b interval | 1-6 method | f/click Flow | ESC quit")

    App(frames, intervals, K_cache[0]).run()


def compute_intervals(frames):
    if len(frames) < 2:
        fps = 30.0
    else:
        dur = (frames[-1].frame_identifier.timestamp_ns -
               frames[0].frame_identifier.timestamp_ns) / 1e9
        fps = (len(frames) - 1) / dur if dur > 0 else 30.0
    return [
        ("1 frame", 1),
        ("0.5 s",   max(1, round(0.5 * fps))),
        ("1.0 s",   max(1, round(1.0 * fps))),
        ("2.0 s",   max(1, round(2.0 * fps))),
    ]


if __name__ == "__main__":
    main()
