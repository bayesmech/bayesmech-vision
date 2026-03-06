#!/usr/bin/env python3
"""
Interactive homography analysis tool.

Shows two frames side-by-side (reference frame and frame N frames later).
Hover mouse on either image to see the corresponding mapped point
(large red dot) in the other image.

Usage:
    cd server
    uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb

Controls:
    Trackbar "Frame"    — reference frame index
    n / b               — cycle interval forward / backward
    1–6                 — select method (auto/depth/plane/points/pose/flow)
    ESC / q             — quit
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

# Allow importing from server/
_here = Path(__file__).resolve()
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

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

_cfg_path = _server_root / "motioncap" / "motioncap_config.yaml"
with open(_cfg_path) as _f:
    _CFG = yaml.safe_load(_f)

# Method labels shown in window title
METHODS = ["auto", "depth", "plane", "points", "pose", "flow"]

WIN = "Homography Analysis  (ESC/q to quit)"
DOT_RADIUS = 12
DOT_COLOR = (0, 0, 255)   # BGR red
DOT_THICKNESS = -1         # filled


def compute_intervals(frames):
    """Return list of (label, delta_frames) tuples based on native FPS."""
    if len(frames) < 2:
        fps = 30.0
    else:
        dur = (frames[-1].frame_identifier.timestamp_ns -
               frames[0].frame_identifier.timestamp_ns) / 1e9
        fps = (len(frames) - 1) / dur if dur > 0 else 30.0

    return [
        ("1 frame",  1),
        ("0.5 s",    max(1, round(0.5  * fps))),
        ("1.0 s",    max(1, round(1.0  * fps))),
        ("2.0 s",    max(1, round(2.0  * fps))),
    ]


class App:
    def __init__(self, frames, intervals, K):
        self.frames = frames
        self.intervals = intervals
        self.K = K
        self.n = len(frames)

        # Trackbar state
        self.frame_idx = 0
        self.interval_idx = 0
        self.method_idx = 0

        # Cached render state
        self._ref_idx = -1
        self._cur_idx = -1
        self._method = ""
        self._base = None    # (H, W*2, 3) BGR side-by-side, no dot
        self._H_frame = 0
        self._W_frame = 0

        # Current hover dot positions
        self._dot_left  = None   # (x, y) dot on left panel, or None
        self._dot_right = None   # (x, y) dot on right panel, or None

    # ── Frame pair ────────────────────────────────────────────────────────

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

    # ── Rendering ─────────────────────────────────────────────────────────

    def _rebuild_base(self):
        """Decode and cache the side-by-side BGR base image."""
        ri, ci = self.ref_idx, self.curr_idx
        method = self.method
        if ri == self._ref_idx and ci == self._cur_idx and method == self._method:
            return  # already up to date

        rgb_ref  = decode_frame_rgb(self.frames[ri])
        rgb_curr = decode_frame_rgb(self.frames[ci])
        self._H_frame, self._W_frame = rgb_ref.shape[:2]

        bgr_ref  = cv2.cvtColor(rgb_ref,  cv2.COLOR_RGB2BGR)
        bgr_curr = cv2.cvtColor(rgb_curr, cv2.COLOR_RGB2BGR)
        self._base = np.concatenate([bgr_ref, bgr_curr], axis=1)

        self._ref_idx  = ri
        self._cur_idx  = ci
        self._method   = method
        self._dot_left = self._dot_right = None

    def _render(self):
        """Return a copy of the base image with dots and HUD drawn."""
        img = self._base.copy()
        if self._dot_left is not None:
            cv2.circle(img, self._dot_left,  DOT_RADIUS, DOT_COLOR, DOT_THICKNESS)
        if self._dot_right is not None:
            x, y = self._dot_right
            cv2.circle(img, (x + self._W_frame, y), DOT_RADIUS, DOT_COLOR, DOT_THICKNESS)

        # ── top-left status line ───────────────────────────────────────────
        label = (f"ref={self.ref_idx}  curr={self.curr_idx}  "
                 f"interval={self.intervals[self.interval_idx][0]}")
        cv2.putText(img, label, (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, label, (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0),       1, cv2.LINE_AA)

        # ── method "dropdown" bar at bottom ───────────────────────────────
        H, W = img.shape[:2]
        BAR_H = 32
        pad_x = 10
        font = cv2.FONT_HERSHEY_SIMPLEX
        fscale, fthick = 0.55, 1

        # dark bar background
        cv2.rectangle(img, (0, H - BAR_H), (W, H), (30, 30, 30), -1)

        # draw each method label; highlight the active one
        x_cursor = pad_x
        for idx, name in enumerate(METHODS):
            text = f"{idx+1}:{name}"
            (tw, th), _ = cv2.getTextSize(text, font, fscale, fthick)
            active = (idx == self.method_idx)

            if active:
                # filled highlight pill
                cv2.rectangle(img,
                              (x_cursor - 4, H - BAR_H + 4),
                              (x_cursor + tw + 4, H - 6),
                              (220, 180, 0), -1)
                fg = (0, 0, 0)
            else:
                fg = (180, 180, 180)

            cv2.putText(img, text,
                        (x_cursor, H - BAR_H + th + 4),
                        font, fscale, fg, fthick, cv2.LINE_AA)
            x_cursor += tw + 18

        # hint text on right side of bar
        hint = "n/b interval  1-6 method"
        (hw, _), _ = cv2.getTextSize(hint, font, 0.45, 1)
        cv2.putText(img, hint, (W - hw - pad_x, H - 10),
                    font, 0.45, (110, 110, 110), 1, cv2.LINE_AA)

        return img

    def show(self):
        self._rebuild_base()
        cv2.imshow(WIN, self._render())

    # ── Mouse callback ─────────────────────────────────────────────────────

    def on_mouse(self, event, x, y, flags, param):
        if event not in (cv2.EVENT_MOUSEMOVE, cv2.EVENT_LBUTTONDOWN):
            return
        if self._base is None:
            return

        W = self._W_frame
        ri, ci = self.ref_idx, self.curr_idx
        frame_ref  = self.frames[ri]
        frame_curr = self.frames[ci]
        R_ref, t_ref = pose_components(frame_ref)
        R_curr, t_curr = pose_components(frame_curr)

        on_left = x < W
        px = float(x if on_left else x - W)
        py = float(y)

        if R_ref is None or R_curr is None:
            return

        if on_left:
            # hover on ref → find in curr
            mapped = pixel_map(px, py, frame_ref, frame_curr,
                               R_ref, t_ref, R_curr, t_curr,
                               self.K, _CFG, self.method)
            self._dot_left  = (int(round(px)), int(round(py)))
            self._dot_right = (int(round(mapped[0])), int(round(mapped[1]))) if mapped else None
        else:
            # hover on curr → find in ref
            mapped = pixel_map(px, py, frame_curr, frame_ref,
                               R_curr, t_curr, R_ref, t_ref,
                               self.K, _CFG, self.method)
            self._dot_right = (int(round(px)), int(round(py)))
            self._dot_left  = (int(round(mapped[0])), int(round(mapped[1]))) if mapped else None

        cv2.imshow(WIN, self._render())

    # ── Trackbar callback ──────────────────────────────────────────────────

    def on_frame(self, val):
        self.frame_idx = val
        self.show()

    # ── Main loop ──────────────────────────────────────────────────────────

    def run(self):
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WIN, self.on_mouse)

        # Only Frame uses a trackbar (large range → easy to drag).
        # Interval and Method are keyboard-driven.
        cv2.createTrackbar("Frame", WIN, 0, self.n - 1, self.on_frame)

        self.show()

        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (27, ord('q')):
                break

            # n / b — cycle interval forward / backward
            elif key == ord('n'):
                self.interval_idx = (self.interval_idx + 1) % len(self.intervals)
                self.show()
            elif key == ord('b'):
                self.interval_idx = (self.interval_idx - 1) % len(self.intervals)
                self.show()

            # 1–6 — select method directly
            elif ord('1') <= key <= ord('6'):
                idx = key - ord('1')
                if idx < len(METHODS):
                    self.method_idx = idx
                    self.show()

        cv2.destroyAllWindows()


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <recording.vis.pb>")
        sys.exit(1)

    rec_path = Path(sys.argv[1]).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    print(f"Loading {rec_path.name}...")
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
                [intr.fx, 0, intr.cx],
                [0, intr.fy, intr.cy],
                [0, 0, 1],
            ], dtype=np.float64))
            break
    if not K_cache:
        print("No camera intrinsics in recording")
        sys.exit(1)

    intervals = compute_intervals(frames)
    print(f"Native FPS: {intervals[1][1] / 0.5:.1f}")
    print(f"Intervals: {[i[0] for i in intervals]}")
    print("Controls: drag 'Frame' trackbar | n/b interval | 1-6 method | ESC/q quit")

    App(frames, intervals, K_cache[0]).run()


if __name__ == "__main__":
    main()
