#!/usr/bin/env python3
"""Render a pendulum period visualization from segmentation masks.

The renderer reads a length-delimited SegmentationResponse file, extracts the
centroid of a labeled mask over time, detects/uses extrema in x(t), and writes a
video with a downward time-axis trace synchronized to the source video.

Example:
    cd server
    uv run python ../tools/render_period_visualization.py \
      --scene-dir "/Users/.../Scene3 Pendulum" \
      --source-video "/Users/.../Scene3 Pendulum/IMG_0858.MOV" \
      --seg "/Users/.../Scene3 Pendulum/20260429_200000.seg.pb" \
      --output "/Users/.../Scene3 Pendulum/final-cut-2/period visualization.mp4" \
      --start-frame 256 --frames 330 --period-frames 513,544,575
"""

from __future__ import annotations

import argparse
import csv
import math
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import find_peaks, savgol_filter


@dataclass(frozen=True)
class PeriodSelection:
    start_min: int
    mid_max: int
    end_min: int

    @property
    def frames(self) -> tuple[int, int, int]:
        return (self.start_min, self.mid_max, self.end_min)


@dataclass(frozen=True)
class TrackData:
    frames: np.ndarray
    x_raw: np.ndarray
    y_raw: np.ndarray
    x_smooth: np.ndarray
    y_smooth: np.ndarray
    missing_frames: list[int]
    mask_width: int
    mask_height: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a period visualization from a labeled segmentation mask track.",
    )
    parser.add_argument("--scene-dir", type=Path, default=None, help="Scene directory used for default paths")
    parser.add_argument("--source-video", type=Path, required=True, help="Source video to draw under the visualization")
    parser.add_argument("--seg", type=Path, required=True, help="Length-delimited SegmentationResponse protobuf")
    parser.add_argument("--output", type=Path, required=True, help="Output MP4 path")
    parser.add_argument("--debug-csv", type=Path, default=None, help="Optional debug CSV path")
    parser.add_argument("--debug-png", type=Path, default=None, help="Optional debug plot path")
    parser.add_argument("--label", default="steel ball", help="Segmentation label to track")
    parser.add_argument("--start-frame", type=int, required=True, help="Full-source start frame for the output window")
    parser.add_argument("--frames", type=int, required=True, help="Number of output frames")
    parser.add_argument("--fps", type=float, default=30.0, help="Output frame rate")
    parser.add_argument(
        "--period-frames",
        default=None,
        help="Comma-separated min,max,min frame numbers. If omitted, the last clean min-max-min in the window is used.",
    )
    parser.add_argument("--width", type=int, default=1920, help="Output video width")
    parser.add_argument("--height", type=int, default=1080, help="Output video height")
    parser.add_argument("--overlay-alpha", type=float, default=0.28, help="Dark wash alpha over source video")
    parser.add_argument("--crf", type=int, default=18, help="libx264 CRF")
    parser.add_argument("--preset", default="slow", help="libx264 preset")
    return parser.parse_args()


def ensure_repo_imports() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    server_root = repo_root / "server"
    for path in (repo_root, repo_root / "proto", server_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def decode_mask(mask_data: bytes) -> np.ndarray:
    height, width = struct.unpack("<II", mask_data[:8])
    packed = zlib.decompress(mask_data[8:])
    bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[: height * width]
    return bits.reshape(height, width).astype(bool)


def iter_segmentation_messages(seg_path: Path):
    ensure_repo_imports()
    from proto import segmentation_pb2  # noqa: PLC0415

    with seg_path.open("rb") as handle:
        while True:
            header = handle.read(4)
            if len(header) < 4:
                break
            (length,) = struct.unpack(">I", header)
            data = handle.read(length)
            if len(data) < length:
                break
            msg = segmentation_pb2.SegmentationResponse()
            msg.ParseFromString(data)
            yield msg


def load_label_track(seg_path: Path, label: str) -> TrackData:
    xs: list[float] = []
    ys: list[float] = []
    frame_numbers: list[int] = []
    missing: list[int] = []
    mask_width = 0
    mask_height = 0

    for msg in iter_segmentation_messages(seg_path):
        frame_no = int(msg.frame_identifier.frame_number)
        frame_numbers.append(frame_no)

        target = next((mask for mask in msg.masks if mask.label == label), None)
        if target is None or not target.mask_data:
            xs.append(math.nan)
            ys.append(math.nan)
            missing.append(frame_no)
            continue

        mask = decode_mask(target.mask_data)
        mask_height, mask_width = mask.shape
        yy, xx = np.nonzero(mask)
        if len(xx) == 0:
            xs.append(math.nan)
            ys.append(math.nan)
            missing.append(frame_no)
        else:
            xs.append(float(xx.mean()))
            ys.append(float(yy.mean()))

    if not frame_numbers:
        raise RuntimeError(f"No segmentation frames found in {seg_path}")
    if mask_width == 0 or mask_height == 0:
        raise RuntimeError(f"No non-empty masks found for label {label!r}")

    frame_arr = np.array(frame_numbers, dtype=np.int32)
    x_raw = np.array(xs, dtype=np.float64)
    y_raw = np.array(ys, dtype=np.float64)
    x_interp = interp_nan(x_raw)
    y_interp = interp_nan(y_raw)

    window = min(15, len(x_interp) // 2 * 2 - 1)
    if window >= 7:
        x_smooth = savgol_filter(x_interp, window, 3)
        y_smooth = savgol_filter(y_interp, window, 3)
    else:
        x_smooth = x_interp
        y_smooth = y_interp

    return TrackData(
        frames=frame_arr,
        x_raw=x_raw,
        y_raw=y_raw,
        x_smooth=x_smooth,
        y_smooth=y_smooth,
        missing_frames=missing,
        mask_width=mask_width,
        mask_height=mask_height,
    )


def interp_nan(values: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(values)
    if not np.any(valid):
        raise RuntimeError("Cannot interpolate an all-missing track")
    idx = np.arange(len(values))
    return np.interp(idx, idx[valid], values[valid])


def extrema_for_signal(x_smooth: np.ndarray, fps: float) -> list[dict[str, float | int | str]]:
    min_distance = max(5, int(round(0.6 * fps)))
    peaks, _ = find_peaks(x_smooth, distance=min_distance, prominence=5)
    troughs, _ = find_peaks(-x_smooth, distance=min_distance, prominence=5)
    extrema = [
        {"frame": int(i), "kind": "max", "time_s": i / fps, "x_smooth": float(x_smooth[i])}
        for i in peaks
    ]
    extrema += [
        {"frame": int(i), "kind": "min", "time_s": i / fps, "x_smooth": float(x_smooth[i])}
        for i in troughs
    ]
    return sorted(extrema, key=lambda item: int(item["frame"]))


def parse_period_frames(raw: str | None, track: TrackData, start: int, end: int, fps: float) -> PeriodSelection:
    if raw:
        parts = [int(item.strip()) for item in raw.split(",") if item.strip()]
        if len(parts) != 3:
            raise ValueError("--period-frames must have exactly three frame numbers: min,max,min")
        return PeriodSelection(parts[0], parts[1], parts[2])

    extrema = extrema_for_signal(track.x_smooth, fps)
    candidates: list[PeriodSelection] = []
    for a, b, c in zip(extrema, extrema[1:], extrema[2:]):
        a_frame = int(a["frame"])
        b_frame = int(b["frame"])
        c_frame = int(c["frame"])
        if a_frame < start or c_frame > end:
            continue
        if a["kind"] != c["kind"] or a["kind"] == b["kind"]:
            continue
        period_s = (c_frame - a_frame) / fps
        if 1.5 <= period_s <= 2.8:
            if a["kind"] == "min":
                candidates.append(PeriodSelection(a_frame, b_frame, c_frame))
            else:
                candidates.append(PeriodSelection(a_frame, b_frame, c_frame))
    if not candidates:
        raise RuntimeError("Could not automatically find a clean extrema triplet; pass --period-frames")
    return candidates[-1]


def draw_text(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    scale: float = 0.8,
    color: tuple[int, int, int] = (245, 245, 245),
    thickness: int = 2,
) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_alpha_rect(
    img: np.ndarray,
    pt1: tuple[int, int],
    pt2: tuple[int, int],
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    overlay = img.copy()
    cv2.rectangle(overlay, pt1, pt2, color, -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


def plot_polyline(img: np.ndarray, pts: list[tuple[int, int]], color: tuple[int, int, int], width: int) -> None:
    if len(pts) >= 2:
        cv2.polylines(img, [np.array(pts, dtype=np.int32)], False, color, width, cv2.LINE_AA)


def graph_point(
    frame_no: int,
    x_value: float,
    x_center: float,
    x_amp: float,
    graph: tuple[int, int, int, int, int, int],
    start_frame: int,
    end_frame: int,
) -> tuple[int, int]:
    graph_x0, graph_y0, _graph_x1, graph_y1, graph_cx, amp_px = graph
    y_frac = (frame_no - start_frame) / max(1, end_frame - start_frame)
    gy = int(graph_y0 + y_frac * (graph_y1 - graph_y0))
    gx = int(graph_cx + ((x_value - x_center) / x_amp) * amp_px)
    return gx, gy


def write_debug_csv(path: Path, track: TrackData, start_frame: int, fps: float) -> None:
    extrema_by_frame = {
        int(item["frame"]): item["kind"]
        for item in extrema_for_signal(track.x_smooth, fps)
    }
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "clip_time_s", "x_raw", "x_smooth", "y_raw", "y_smooth", "missing", "extremum"])
        for idx in range(len(track.frames)):
            writer.writerow(
                [
                    idx,
                    f"{(idx - start_frame) / fps:.6f}",
                    "" if np.isnan(track.x_raw[idx]) else f"{track.x_raw[idx]:.3f}",
                    f"{track.x_smooth[idx]:.3f}",
                    "" if np.isnan(track.y_raw[idx]) else f"{track.y_raw[idx]:.3f}",
                    f"{track.y_smooth[idx]:.3f}",
                    int(idx in track.missing_frames),
                    extrema_by_frame.get(idx, ""),
                ]
            )


def write_debug_png(path: Path, track: TrackData, period: PeriodSelection, start: int, end: int, fps: float) -> None:
    period_s = (period.end_min - period.start_min) / fps
    canvas = np.full((720, 1280, 3), (248, 248, 244), dtype=np.uint8)
    left, top, right, bottom = 90, 70, 1210, 610
    cv2.rectangle(canvas, (left, top), (right, bottom), (40, 40, 40), 2)
    xs = np.arange(start, end + 1)
    x_min = float(np.min(track.x_smooth[start : end + 1]))
    x_max = float(np.max(track.x_smooth[start : end + 1]))
    pts = []
    for frame_no in xs:
        px = int(left + (frame_no - start) / (end - start) * (right - left))
        py = int(bottom - (track.x_smooth[frame_no] - x_min) / (x_max - x_min) * (bottom - top))
        pts.append((px, py))
    plot_polyline(canvas, pts, (45, 105, 210), 3)
    for frame_no, kind in zip(period.frames, ("min", "max", "min")):
        px = int(left + (frame_no - start) / (end - start) * (right - left))
        py = int(bottom - (track.x_smooth[frame_no] - x_min) / (x_max - x_min) * (bottom - top))
        color = (20, 145, 70) if kind == "max" else (210, 80, 45)
        cv2.circle(canvas, (px, py), 8, color, -1, cv2.LINE_AA)
        draw_text(canvas, f"{kind} f{frame_no}", (px + 10, py - 10), 0.45, color, 1)
    draw_text(canvas, f"Segmentation centroid x(t), T = {period_s:.2f}s", (90, 42), 0.8, (20, 20, 20), 2)
    cv2.imwrite(str(path), canvas)


def start_ffmpeg(args: argparse.Namespace, output_frames: int) -> subprocess.Popen:
    start_s = args.start_frame / args.fps
    duration_s = output_frames / args.fps
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
        f"{args.width}x{args.height}",
        "-r",
        f"{args.fps:g}",
        "-i",
        "-",
        "-ss",
        f"{start_s:.6f}",
        "-t",
        f"{duration_s:.6f}",
        "-i",
        str(args.source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        args.preset,
        "-crf",
        str(args.crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(args.output),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render_video(args: argparse.Namespace, track: TrackData, period: PeriodSelection) -> None:
    start = args.start_frame
    end = args.start_frame + args.frames - 1
    period_s = (period.end_min - period.start_min) / args.fps
    half_a_s = (period.mid_max - period.start_min) / args.fps
    half_b_s = (period.end_min - period.mid_max) / args.fps

    cap = cv2.VideoCapture(str(args.source_video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {args.source_video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    proc = start_ffmpeg(args, args.frames)
    assert proc.stdin is not None

    graph_x0, graph_y0 = int(args.width * 0.6875), int(args.height * 0.1065)
    graph_x1, graph_y1 = int(args.width * 0.9557), int(args.height * 0.8889)
    graph_cx = (graph_x0 + graph_x1) // 2
    amp_px = int(args.width * 0.1068)
    graph = (graph_x0, graph_y0, graph_x1, graph_y1, graph_cx, amp_px)

    x_min = float(np.min(track.x_smooth[start : end + 1]))
    x_max = float(np.max(track.x_smooth[start : end + 1]))
    x_center = (x_min + x_max) / 2
    x_amp = max((x_max - x_min) / 2, 1.0)

    preview = [
        graph_point(frame_no, track.x_smooth[frame_no], x_center, x_amp, graph, start, end)
        for frame_no in range(start, end + 1)
    ]
    period_kinds = {period.start_min: "min", period.mid_max: "max", period.end_min: "min"}
    period_colors = {"min": (255, 145, 86), "max": (74, 232, 126)}
    graph_points: list[tuple[int, int]] = []
    ball_points: list[tuple[int, int]] = []

    for out_i in range(args.frames):
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Could not read source frame {start + out_i}")
        frame = cv2.resize(frame, (args.width, args.height), interpolation=cv2.INTER_AREA)
        full_frame = start + out_i

        wash = np.zeros_like(frame)
        frame = cv2.addWeighted(frame, 1.0 - args.overlay_alpha, wash, args.overlay_alpha, 0)

        bx = int(round(track.x_smooth[full_frame] * args.width / track.mask_width))
        by = int(round(track.y_smooth[full_frame] * args.height / track.mask_height))
        ball_points.append((bx, by))
        if len(ball_points) > 45:
            ball_points.pop(0)
        plot_polyline(frame, ball_points, (70, 220, 255), 3)
        cv2.circle(frame, (bx, by), 17, (20, 20, 20), 4, cv2.LINE_AA)
        cv2.circle(frame, (bx, by), 13, (70, 220, 255), -1, cv2.LINE_AA)

        draw_alpha_rect(frame, (graph_x0 - 60, 55), (args.width - 45, args.height - 70), (12, 18, 25), 0.72)
        cv2.rectangle(frame, (graph_x0, graph_y0), (graph_x1, graph_y1), (210, 220, 225), 1, cv2.LINE_AA)
        cv2.line(frame, (graph_cx, graph_y0), (graph_cx, graph_y1), (92, 104, 112), 1, cv2.LINE_AA)
        draw_text(frame, f"{args.label} x(t)", (graph_x0 - 15, graph_y0 - 27), 0.72, (248, 248, 240), 2)
        draw_text(frame, "time", (graph_x1 - 90, graph_y1 + 32), 0.54, (205, 215, 220), 1)
        cv2.arrowedLine(frame, (graph_x1 - 30, graph_y1 - 18), (graph_x1 - 30, graph_y1 + 22), (205, 215, 220), 2, cv2.LINE_AA, tipLength=0.35)

        plot_polyline(frame, preview, (62, 68, 74), 2)
        current_pt = graph_point(full_frame, track.x_smooth[full_frame], x_center, x_amp, graph, start, end)
        graph_points.append(current_pt)
        plot_polyline(frame, graph_points, (76, 215, 255), 4)
        cv2.circle(frame, current_pt, 10, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, current_pt, 6, (76, 215, 255), -1, cv2.LINE_AA)

        for mark_frame, kind in period_kinds.items():
            mx, my = graph_point(mark_frame, track.x_smooth[mark_frame], x_center, x_amp, graph, start, end)
            color = period_colors[kind]
            cv2.circle(frame, (mx, my), 8, color, -1, cv2.LINE_AA)
            if full_frame >= mark_frame:
                draw_text(frame, kind, (mx + 14, my + 5), 0.48, color, 1)

        x_br = graph_x0 + 38
        _, y_min0 = graph_point(period.start_min, track.x_smooth[period.start_min], x_center, x_amp, graph, start, end)
        _, y_maxp = graph_point(period.mid_max, track.x_smooth[period.mid_max], x_center, x_amp, graph, start, end)
        _, y_min1 = graph_point(period.end_min, track.x_smooth[period.end_min], x_center, x_amp, graph, start, end)
        cv2.line(frame, (x_br, y_min0), (x_br, y_min1), (235, 235, 220), 2, cv2.LINE_AA)
        cv2.line(frame, (x_br - 10, y_min0), (x_br + 10, y_min0), (235, 235, 220), 2, cv2.LINE_AA)
        cv2.line(frame, (x_br - 10, y_min1), (x_br + 10, y_min1), (235, 235, 220), 2, cv2.LINE_AA)
        cv2.line(frame, (x_br + 25, y_min0), (x_br + 25, y_maxp), (160, 220, 255), 2, cv2.LINE_AA)
        cv2.line(frame, (x_br + 25, y_maxp), (x_br + 25, y_min1), (160, 220, 255), 2, cv2.LINE_AA)
        draw_text(frame, f"T = {period_s:.2f}s", (graph_x0 + 55, (y_min0 + y_min1) // 2 + 6), 0.68, (245, 245, 235), 2)
        draw_text(frame, f"{half_a_s:.2f}s", (graph_x0 + 82, (y_min0 + y_maxp) // 2), 0.47, (170, 225, 255), 1)
        draw_text(frame, f"{half_b_s:.2f}s", (graph_x0 + 82, (y_maxp + y_min1) // 2), 0.47, (170, 225, 255), 1)

        draw_alpha_rect(frame, (55, 55), (820, 165), (8, 12, 16), 0.48)
        draw_text(frame, "Pendulum period from segmentation", (82, 100), 0.86, (248, 248, 240), 2)
        draw_text(frame, f"frames {start}-{end}  |  T approx {period_s:.2f}s", (83, 140), 0.54, (210, 228, 232), 1)

        proc.stdin.write(frame.tobytes())

    cap.release()
    proc.stdin.close()
    rc = proc.wait()
    if rc:
        raise RuntimeError(f"ffmpeg failed with exit code {rc}")


def main() -> None:
    args = parse_args()
    start = args.start_frame
    end = args.start_frame + args.frames - 1

    debug_csv = args.debug_csv or args.output.with_name("period_debug.csv")
    debug_png = args.debug_png or args.output.with_name("period_debug.png")

    track = load_label_track(args.seg, args.label)
    period = parse_period_frames(args.period_frames, track, start, end, args.fps)

    write_debug_csv(debug_csv, track, start, args.fps)
    write_debug_png(debug_png, track, period, start, end, args.fps)
    render_video(args, track, period)

    period_s = (period.end_min - period.start_min) / args.fps
    half_a_s = (period.mid_max - period.start_min) / args.fps
    half_b_s = (period.end_min - period.mid_max) / args.fps
    print(f"wrote {args.output}")
    print(f"wrote {debug_csv}")
    print(f"wrote {debug_png}")
    print(f"missing {args.label!r} frames: {track.missing_frames}")
    print(f"period_s={period_s:.6f}, half_a_s={half_a_s:.6f}, half_b_s={half_b_s:.6f}")


if __name__ == "__main__":
    main()
