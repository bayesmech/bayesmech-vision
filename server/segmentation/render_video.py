#!/usr/bin/env python3
"""Render label-stable segmentation mask and overlay videos.

This script turns a .vis.pb + .seg.pb recording pair into two MP4 artifacts:

- <stem>.segmentation_masks.mp4   black background with solid label colors
- <stem>.segmentation_overlay.mp4 translucent label colors over RGB video

Colors are derived from normalized labels, not object IDs, so the same label
keeps the same color across frames even if the segmentation model changes IDs.
Unlabeled masks fall back to object_id.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np


PALETTE_RGB = np.array(
    [
        [255, 0, 0],
        [0, 255, 0],
        [0, 0, 255],
        [255, 255, 0],
        [255, 0, 255],
        [0, 255, 255],
        [255, 128, 0],
        [128, 0, 255],
        [0, 160, 255],
        [160, 255, 0],
        [255, 0, 128],
        [128, 255, 128],
    ],
    dtype=np.uint8,
)

PROTO_IMPORT_HINT = (
    "Generated Python protobuf modules are missing. "
    "Run `cd proto && bash generate_proto.sh` from the repo root, then retry."
)


def ensure_repo_imports() -> None:
    file_path = Path(__file__).resolve()
    server_root = file_path.parent.parent
    project_root = server_root.parent
    for path in (project_root, project_root / "proto", server_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render label-stable segmentation mask and overlay videos.",
    )
    parser.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    parser.add_argument("--seg", type=Path, default=None, help="Path to .seg.pb/.segmentation.pb")
    parser.add_argument(
        "--source-video",
        type=Path,
        default=None,
        help="Optional source video for RGB/audio. Defaults to RGB frames from .vis.pb.",
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory")
    parser.add_argument("--mask-output", type=Path, default=None, help="Mask-only MP4 output path")
    parser.add_argument("--overlay-output", type=Path, default=None, help="Overlay MP4 output path")
    parser.add_argument("--start-frame", type=int, default=0, help="First frame number/index to render")
    parser.add_argument("--max-frames", type=int, default=0, help="Maximum frames to render, 0 = all")
    parser.add_argument("--fps", type=float, default=0.0, help="Override output fps")
    parser.add_argument("--alpha", type=float, default=0.5, help="Overlay alpha for masks")
    parser.add_argument("--crf", type=int, default=18, help="libx264 CRF")
    parser.add_argument("--preset", default="slow", help="libx264 preset")
    return parser.parse_args()


def recording_stem(recording: Path) -> str:
    return recording.name.removesuffix(".vis.pb") if recording.name.endswith(".vis.pb") else recording.stem


def default_seg_path(recording: Path) -> Path:
    stem = recording_stem(recording)
    candidates = [
        recording.parent / f"{stem}.seg.pb",
        recording.parent / f"{stem}.segmentation.pb",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def decode_mask(mask_data: bytes) -> np.ndarray:
    height, width = struct.unpack("<II", mask_data[:8])
    packed = zlib.decompress(mask_data[8:])
    bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[: height * width]
    return bits.reshape(height, width).astype(bool)


def segmentation_color_key(mask) -> str:
    label = (mask.label or "").strip().lower()
    return label or f"object:{int(mask.object_id)}"


def color_index_for_key(key: str) -> int:
    hash_value = 2166136261
    for byte in key.encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return hash_value % len(PALETTE_RGB)


def color_for_mask(mask) -> np.ndarray:
    return PALETTE_RGB[color_index_for_key(segmentation_color_key(mask))]


def load_segmentations(seg_path: Path):
    ensure_repo_imports()
    try:
        from proto import segmentation_pb2  # noqa: PLC0415
        from streamlog.protoio import ProtoIO  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(PROTO_IMPORT_HINT) from exc

    return ProtoIO(segmentation_pb2.SegmentationResponse).read_file(seg_path)


def load_vis_frames(recording: Path):
    ensure_repo_imports()
    try:
        from proto import perceiver_pb2  # noqa: PLC0415
        from streamlog.protoio import ProtoIO  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(PROTO_IMPORT_HINT) from exc

    return ProtoIO(perceiver_pb2.PerceiverDataFrame).read_file(recording)


def decode_frame_bgr(frame) -> np.ndarray:
    ensure_repo_imports()
    try:
        from proto import perceiver_pb2  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(PROTO_IMPORT_HINT) from exc

    img = frame.rgb_frame
    fmt = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == fmt.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("OpenCV failed to decode JPEG frame")
        return bgr
    if img.format == fmt.BITMAP_RGB:
        width = int(getattr(img, "width", 0) or 0)
        height = int(getattr(img, "height", 0) or 0)
        raw = np.frombuffer(img.data, dtype=np.uint8)
        if width > 0 and height > 0 and raw.size >= width * height * 3:
            rgb = raw[: width * height * 3].reshape(height, width, 3)
        else:
            total = len(raw) // 3
            side = int(total ** 0.5)
            if side * side != total:
                raise ValueError("Raw RGB frame is missing dimensions and is not square")
            rgb = raw.reshape(side, side, 3)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    raise ValueError(f"Unsupported RGB frame format: {img.format}")


def fps_from_timestamps(frames: list) -> float:
    if len(frames) >= 2:
        first = frames[0].frame_identifier.timestamp_ns
        last = frames[-1].frame_identifier.timestamp_ns
        if last > first:
            return (len(frames) - 1) / ((last - first) / 1e9)
    return 30.0


def build_color_map(resp, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    color_rgb = np.zeros((height, width, 3), dtype=np.uint8)
    present = np.zeros((height, width), dtype=bool)

    for mask_msg in resp.masks:
        if not mask_msg.mask_data:
            continue
        mask = decode_mask(mask_msg.mask_data)
        if mask.shape != (height, width):
            mask = cv2.resize(mask.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
        color_rgb[mask] = color_for_mask(mask_msg)
        present[mask] = True

    return color_rgb, present


def start_ffmpeg(
    output_path: Path,
    width: int,
    height: int,
    fps: float,
    *,
    crf: int,
    preset: str,
    audio_source: Path | None = None,
    audio_start_s: float = 0.0,
    duration_s: float | None = None,
) -> subprocess.Popen:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:g}",
        "-i",
        "-",
    ]
    if audio_source is not None:
        cmd.extend(["-ss", f"{audio_start_s:.6f}"])
        if duration_s is not None:
            cmd.extend(["-t", f"{duration_s:.6f}"])
        cmd.extend(["-i", str(audio_source), "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k", "-shortest"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def frame_iter_from_source_video(source_video: Path, start_frame: int, max_frames: int):
    cap = cv2.VideoCapture(str(source_video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open source video: {source_video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    yielded = 0
    try:
        while max_frames <= 0 or yielded < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            yield start_frame + yielded, frame
            yielded += 1
    finally:
        cap.release()


def frame_iter_from_vis(recording: Path, start_frame: int, max_frames: int):
    frames = load_vis_frames(recording)
    selected = [
        frame
        for frame in frames
        if int(frame.frame_identifier.frame_number) >= start_frame
    ]
    if max_frames > 0:
        selected = selected[:max_frames]
    for frame in selected:
        yield int(frame.frame_identifier.frame_number), decode_frame_bgr(frame)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    seg_path = (args.seg or default_seg_path(recording)).resolve()
    output_dir = (args.output_dir or recording.parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = recording_stem(recording)
    mask_output = args.mask_output or output_dir / f"{stem}.segmentation_masks.mp4"
    overlay_output = args.overlay_output or output_dir / f"{stem}.segmentation_overlay.mp4"

    if not recording.exists():
        raise FileNotFoundError(recording)
    if not seg_path.exists():
        raise FileNotFoundError(seg_path)

    segmentations = load_segmentations(seg_path)
    seg_by_frame = {
        int(resp.frame_identifier.frame_number): resp
        for resp in segmentations
    }
    if not seg_by_frame:
        raise RuntimeError(f"No segmentation responses found in {seg_path}")

    if args.source_video is not None:
        source_video = args.source_video.resolve()
        cap = cv2.VideoCapture(str(source_video))
        if not cap.isOpened():
            raise RuntimeError(f"Could not open source video: {source_video}")
        fps = args.fps or cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        frame_iter = frame_iter_from_source_video(source_video, args.start_frame, args.max_frames)
        audio_source = source_video
    else:
        vis_frames = load_vis_frames(recording)
        if not vis_frames:
            raise RuntimeError(f"No frames found in {recording}")
        first_bgr = decode_frame_bgr(vis_frames[0])
        height, width = first_bgr.shape[:2]
        fps = args.fps or fps_from_timestamps(vis_frames)
        frame_iter = frame_iter_from_vis(recording, args.start_frame, args.max_frames)
        audio_source = None

    frame_count_hint = args.max_frames if args.max_frames > 0 else None
    duration_s = (frame_count_hint / fps) if frame_count_hint else None
    audio_start_s = args.start_frame / fps

    mask_proc = start_ffmpeg(mask_output, width, height, fps, crf=args.crf, preset=args.preset)
    overlay_proc = start_ffmpeg(
        overlay_output,
        width,
        height,
        fps,
        crf=args.crf,
        preset=args.preset,
        audio_source=audio_source,
        audio_start_s=audio_start_s,
        duration_s=duration_s,
    )
    assert mask_proc.stdin is not None
    assert overlay_proc.stdin is not None

    rendered = 0
    try:
        for frame_number, bgr in frame_iter:
            resp = seg_by_frame.get(frame_number)
            if resp is None:
                color_rgb = np.zeros((height, width, 3), dtype=np.uint8)
                present = np.zeros((height, width), dtype=bool)
            else:
                color_rgb, present = build_color_map(resp, width, height)

            color_bgr = color_rgb[..., ::-1]
            overlay = bgr.copy()
            if present.any():
                blended = (
                    overlay[present].astype(np.float32) * (1.0 - args.alpha)
                    + color_bgr[present].astype(np.float32) * args.alpha
                )
                overlay[present] = np.clip(blended, 0, 255).astype(np.uint8)

            mask_proc.stdin.write(color_bgr.tobytes())
            overlay_proc.stdin.write(overlay.tobytes())
            rendered += 1
    finally:
        for proc in (mask_proc, overlay_proc):
            if proc.stdin:
                proc.stdin.close()
        mask_rc = mask_proc.wait()
        overlay_rc = overlay_proc.wait()

    if mask_rc != 0:
        raise RuntimeError(f"ffmpeg failed for mask video with exit code {mask_rc}")
    if overlay_rc != 0:
        raise RuntimeError(f"ffmpeg failed for overlay video with exit code {overlay_rc}")

    print(f"Rendered {rendered} frame(s) at {fps:g} fps")
    print(f"mask:    {mask_output}")
    print(f"overlay: {overlay_output}")


if __name__ == "__main__":
    main()
