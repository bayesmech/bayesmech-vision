"""
video_layers.py — Abstraction for rendering video frames for InsightVideo.

Each VideoLayer takes frames from a .vis.pb recording and produces JPEG-encoded
images.  The layer determines *what* visual information is overlaid:
  - RawVideoLayer        → original RGB frames, no processing
  - MotioncapVideoLayer  → RGB + heatmap + track overlay from .motion.pb/.motion.mp4

To add a new understanding layer in the future, subclass VideoLayer and register
it in LAYER_REGISTRY.
"""

from __future__ import annotations

import struct
import zlib
from abc import ABC, abstractmethod
from pathlib import Path

import cv2
import numpy as np

# ── Proto imports ─────────────────────────────────────────────────────────────

import sys

_file = Path(__file__).resolve()
_server_root = _file.parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2, motioncap_pb2, insightgen_pb2
from streamlog.protoio import ProtoIO

_vis_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)

# ── Track colours (BGR, matching motioncap/main.py) ───────────────────────────

_TRACK_COLORS = [
    (0, 165, 255),    # orange
    (0, 255, 0),      # green
    (255, 0, 0),      # blue
    (203, 192, 255),  # pink
    (255, 255, 0),    # cyan
    (114, 128, 250),  # salmon
    (154, 250, 0),    # yellow-green
    (238, 130, 238),  # violet
    (245, 206, 135),  # sky-blue
    (0, 215, 255),    # gold
]


def _color_for(track_id: int):
    return _TRACK_COLORS[track_id % len(_TRACK_COLORS)]


# ── Frame helpers ─────────────────────────────────────────────────────────────

def _decode_rgb(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    """Return (H, W, 3) uint8 RGB array from a PerceiverDataFrame."""
    img = frame.rgb_frame
    fmt = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == fmt.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if img.format == fmt.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        total = len(raw) // 3
        h = w = int(total ** 0.5)
        return raw[: h * w * 3].reshape(h, w, 3)
    raise ValueError(f"Unsupported image format: {img.format}")


def _resize_bgr(bgr: np.ndarray, max_width: int) -> np.ndarray:
    """Downscale a BGR frame if its width exceeds max_width. No-op if max_width <= 0."""
    if max_width <= 0 or bgr.shape[1] <= max_width:
        return bgr
    scale = max_width / bgr.shape[1]
    new_w = max_width
    new_h = int(bgr.shape[0] * scale)
    return cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _frame_to_jpeg(
    frame: perceiver_pb2.PerceiverDataFrame,
    quality: int,
    max_width: int = 0,
) -> bytes:
    """Return JPEG bytes for a PerceiverDataFrame, resizing if needed."""
    img = frame.rgb_frame
    # Fast passthrough only when no resize is needed
    if img.format == perceiver_pb2.ImageFrame.ImageFormat.JPEG and max_width <= 0:
        return bytes(img.data)
    rgb = _decode_rgb(frame)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr = _resize_bgr(bgr, max_width)
    _, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def _bgr_to_jpeg(bgr: np.ndarray, quality: int, max_width: int = 0) -> bytes:
    """Encode a BGR ndarray to JPEG bytes, resizing if needed."""
    bgr = _resize_bgr(bgr, max_width)
    _, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def _decode_heatmap(data: bytes) -> np.ndarray:
    """Decode a MotionHeatmap.data blob → (H, W) uint8 array."""
    h, w = struct.unpack("<II", data[:8])
    raw = zlib.decompress(data[8:])
    return np.frombuffer(raw, dtype=np.uint8)[: h * w].reshape(h, w)


# ── Abstract base ─────────────────────────────────────────────────────────────

class VideoLayer(ABC):
    """
    Produces JPEG-encoded frames from a .vis.pb recording.

    Parameters
    ----------
    vis_path : Path
        Path to the .vis.pb recording file.
    frame_indices : list[int] | None
        Indices into vis_path frames to render.  None means all frames.
    jpeg_quality : int
        JPEG encoding quality (0–100).
    max_width : int
        Downscale frames to this pixel width before encoding (0 = no limit).

    Returns
    -------
    list of (timestamp_ns: int, jpeg_bytes: bytes)
    """

    @abstractmethod
    def render_frames(
        self,
        vis_path: Path,
        frame_indices: list[int] | None,
        jpeg_quality: int = 75,
        max_width: int = 0,
    ) -> list[tuple[int, bytes]]:
        ...


# ── Raw layer ─────────────────────────────────────────────────────────────────

class RawVideoLayer(VideoLayer):
    """Serves the original RGB frames with no overlay."""

    def render_frames(self, vis_path, frame_indices=None, jpeg_quality=75, max_width=0):
        frames = _vis_io.read_file(vis_path)
        if frame_indices is None:
            frame_indices = list(range(len(frames)))
        result = []
        for idx in frame_indices:
            if idx >= len(frames):
                continue
            frame = frames[idx]
            ts = frame.frame_identifier.timestamp_ns
            jpeg = _frame_to_jpeg(frame, jpeg_quality, max_width)
            result.append((ts, jpeg))
        return result


# ── Motioncap overlay layer ───────────────────────────────────────────────────

class MotioncapVideoLayer(VideoLayer):
    """
    Returns the understanding video — original RGB with the motioncap heatmap
    and track overlay that motioncap/main.py --output-video produces.

    Priority:
      1. Read frames directly from the pre-rendered .motion.mp4 (zero extra work,
         pixel-identical to the offline render).
      2. If .motion.mp4 is absent, render on-demand from .motion.pb + .vis.pb.
      3. If neither exists, fall back to raw frames.
    """

    def __init__(self, overlay_alpha: float = 0.5, tail_length: int = 30):
        self.overlay_alpha = overlay_alpha
        self.tail_length = tail_length

    def render_frames(self, vis_path, frame_indices=None, jpeg_quality=75, max_width=0):
        base = vis_path.name.removesuffix(".vis.pb")
        mp4_path   = vis_path.parent / f"{base}.motion.mp4"
        motion_path = vis_path.parent / f"{base}.motion.pb"

        if mp4_path.exists():
            return self._render_from_mp4(vis_path, mp4_path, motion_path, frame_indices, jpeg_quality, max_width)
        elif motion_path.exists():
            return self._render_from_pb(vis_path, motion_path, frame_indices, jpeg_quality, max_width)
        else:
            return RawVideoLayer().render_frames(vis_path, frame_indices, jpeg_quality, max_width)

    # ── Source: pre-rendered .motion.mp4 ──────────────────────────────────────

    def _render_from_mp4(self, vis_path, mp4_path, motion_path, frame_indices, jpeg_quality, max_width):
        """
        Read frames from the pre-rendered .motion.mp4.

        The mp4 frame sequence corresponds 1:1 with the heatmap records in .motion.pb.
        We map vis.pb frame timestamps → motion.pb heatmap indices to find the right
        mp4 frame for each requested vis frame.
        """
        vis_frames = _vis_io.read_file(vis_path)
        if frame_indices is None:
            frame_indices = list(range(len(vis_frames)))

        # Build vis timestamp → mp4 frame index mapping via motion.pb timestamps
        hm_by_ts: dict[int, int] = {}
        if motion_path.exists():
            for i, rec in enumerate(_motion_io.read_file(motion_path)):
                if not rec.tracks and rec.HasField("frame_identifier"):
                    hm_by_ts[rec.frame_identifier.timestamp_ns] = i

        # Build the ordered list of (vis_idx, mp4_frame_idx) to read
        tasks: list[tuple[int, int | None]] = []
        for vis_idx in frame_indices:
            if vis_idx >= len(vis_frames):
                continue
            ts = vis_frames[vis_idx].frame_identifier.timestamp_ns
            tasks.append((vis_idx, hm_by_ts.get(ts)))

        # Read mp4 frames in ascending order (sequential reads are fast)
        mp4_needed = sorted({mp4_idx for _, mp4_idx in tasks if mp4_idx is not None})
        mp4_cache: dict[int, np.ndarray] = {}

        cap = cv2.VideoCapture(str(mp4_path))
        try:
            pos = 0
            for mp4_idx in mp4_needed:
                if mp4_idx != pos:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, mp4_idx)
                    pos = mp4_idx
                ret, bgr = cap.read()
                if ret:
                    mp4_cache[mp4_idx] = bgr
                pos += 1
        finally:
            cap.release()

        result = []
        for vis_idx, mp4_idx in tasks:
            ts = vis_frames[vis_idx].frame_identifier.timestamp_ns
            if mp4_idx is not None and mp4_idx in mp4_cache:
                jpeg = _bgr_to_jpeg(mp4_cache[mp4_idx], jpeg_quality, max_width)
                result.append((ts, jpeg))
            else:
                # No mp4 frame for this vis frame — use raw
                result.append((ts, _frame_to_jpeg(vis_frames[vis_idx], jpeg_quality, max_width)))

        return result

    # ── Source: on-demand render from .motion.pb ───────────────────────────────

    def _render_from_pb(self, vis_path, motion_path, frame_indices, jpeg_quality, max_width):
        """Render overlay on-demand when no mp4 is available."""
        from collections import defaultdict

        vis_frames = _vis_io.read_file(vis_path)
        if frame_indices is None:
            frame_indices = list(range(len(vis_frames)))

        heatmap_records, tracks = [], []
        for rec in _motion_io.read_file(motion_path):
            if rec.tracks:
                tracks = list(rec.tracks)
            else:
                heatmap_records.append(rec)

        hm_by_ts: dict[int, int] = {}
        for i, rec in enumerate(heatmap_records):
            if rec.HasField("frame_identifier"):
                hm_by_ts[rec.frame_identifier.timestamp_ns] = i

        positions_by_hm_idx: dict[int, list] = defaultdict(list)
        for track in tracks:
            for pos in track.positions:
                positions_by_hm_idx[pos.frame_idx].append(
                    (track.track_id, pos.cx, pos.cy, pos.interpolated)
                )

        result = []
        for idx in frame_indices:
            if idx >= len(vis_frames):
                continue
            frame = vis_frames[idx]
            ts = frame.frame_identifier.timestamp_ns
            hm_idx = hm_by_ts.get(ts)

            if hm_idx is None:
                result.append((ts, _frame_to_jpeg(frame, jpeg_quality, max_width)))
                continue

            rgb = _decode_rgb(frame)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            hm_rec = heatmap_records[hm_idx]
            hm = _decode_heatmap(hm_rec.heatmap.heatmap_data)

            if hm.shape[:2] != bgr.shape[:2]:
                hm = cv2.resize(hm, (bgr.shape[1], bgr.shape[0]))

            hm_color = cv2.applyColorMap(hm, cv2.COLORMAP_JET)
            canvas = cv2.addWeighted(bgr, 1.0 - self.overlay_alpha, hm_color, self.overlay_alpha, 0)
            self._draw_tracks(canvas, hm_idx, tracks, positions_by_hm_idx)

            result.append((ts, _bgr_to_jpeg(canvas, jpeg_quality, max_width)))

        return result

    def _draw_tracks(self, canvas, current_hm_idx, tracks, positions_by_hm_idx):
        for track in tracks:
            color = _color_for(track.track_id)
            tail_start = max(0, current_hm_idx - self.tail_length)
            tail_pts = []
            for fi in range(tail_start, current_hm_idx + 1):
                for (tid, cx, cy, _interp) in positions_by_hm_idx.get(fi, []):
                    if tid == track.track_id:
                        tail_pts.append((int(round(cx)), int(round(cy))))
            for i in range(1, len(tail_pts)):
                fade = (i / len(tail_pts)) ** 1.5
                c = tuple(int(v * fade) for v in color)
                cv2.line(canvas, tail_pts[i - 1], tail_pts[i], c, 1, cv2.LINE_AA)

        for (tid, cx, cy, interp) in positions_by_hm_idx.get(current_hm_idx, []):
            color = _color_for(tid)
            center = (int(round(cx)), int(round(cy)))
            if interp:
                cv2.drawMarker(canvas, center, color, cv2.MARKER_CROSS, 10, 1, cv2.LINE_AA)
            else:
                cv2.circle(canvas, center, 6, color, 2, cv2.LINE_AA)
            cv2.putText(
                canvas, f"T{tid}", (center[0] + 8, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
            )


# ── Registry ──────────────────────────────────────────────────────────────────

#: Maps layer name → VideoLayer class.
#: To add a new understanding layer in the future, update "understanding" here.
LAYER_REGISTRY: dict[str, type[VideoLayer]] = {
    "raw": RawVideoLayer,
    "understanding": MotioncapVideoLayer,
}
