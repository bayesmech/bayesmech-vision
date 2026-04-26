#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from idoslam.common import (
    decode_rgb,
    sift_debug_video_output_path,
    triangulated_correspondences_csv_path,
    triangulated_pair_logs_path,
)


@dataclass
class PairLog:
    frame_index: int
    paired_frame_index: int
    status: str
    good_match_count: int
    inlier_count: int
    triangulated_left: int
    triangulated_right: int
    on_road_count: int


@dataclass
class Correspondence:
    source_x: float
    source_y: float
    target_x: float
    target_y: float
    on_road: bool


class IndexedFrameReader:
    def __init__(self, recording: Path, needed_indices: set[int]) -> None:
        self.recording = recording
        self.offsets = self._build_offsets(recording, needed_indices)
        self._fh = recording.open("rb")
        self._cache: dict[int, np.ndarray] = {}

    def _build_offsets(self, recording: Path, needed_indices: set[int]) -> dict[int, tuple[int, int]]:
        offsets: dict[int, tuple[int, int]] = {}
        pending = set(needed_indices)
        with recording.open("rb") as f:
            frame_index = 0
            while pending:
                header = f.read(4)
                if len(header) < 4:
                    break
                (length,) = struct.unpack(">I", header)
                if length <= 0:
                    break
                data_offset = f.tell()
                if frame_index in pending:
                    offsets[frame_index] = (data_offset, length)
                    pending.remove(frame_index)
                f.seek(length, 1)
                frame_index += 1
        if pending:
            missing = ", ".join(str(idx) for idx in sorted(pending)[:10])
            raise RuntimeError(f"Missing {len(pending)} frame(s) in recording index: {missing}")
        return offsets

    def read_bgr(self, frame_index: int) -> np.ndarray:
        cached = self._cache.get(frame_index)
        if cached is not None:
            return cached.copy()
        data_offset, length = self.offsets[frame_index]
        self._fh.seek(data_offset)
        data = self._fh.read(length)
        if len(data) != length:
            raise RuntimeError(f"Failed to read frame {frame_index}")
        frame = perceiver_pb2.PerceiverDataFrame()
        frame.ParseFromString(data)
        bgr = decode_rgb(frame)
        self._cache[frame_index] = bgr
        return bgr.copy()

    def close(self) -> None:
        self._fh.close()
        self._cache.clear()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render SIFT correspondence debug video")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--fps", type=float, default=12.0)
    p.add_argument("--codec", type=str, default="mp4v")
    p.add_argument("--max-correspondences", type=int, default=160)
    p.add_argument("--trail-threshold-px", type=float, default=2.0)
    return p.parse_args()


def default_correspondences_csv(recording: Path) -> Path:
    return triangulated_correspondences_csv_path(recording)


def default_pair_logs_path(recording: Path) -> Path:
    return triangulated_pair_logs_path(recording)


def default_output_path(recording: Path) -> Path:
    return sift_debug_video_output_path(recording)


def load_pair_logs(path: Path) -> list[PairLog]:
    rows = json.loads(path.read_text())
    return [
        PairLog(
            frame_index=int(row["frame_index"]),
            paired_frame_index=int(row["paired_frame_index"]),
            status=str(row.get("status", "")),
            good_match_count=int(row.get("good_match_count", 0)),
            inlier_count=int(row.get("inlier_count", 0)),
            triangulated_left=int(row.get("triangulated_left", 0)),
            triangulated_right=int(row.get("triangulated_right", 0)),
            on_road_count=int(row.get("on_road_count", 0)),
        )
        for row in rows
    ]


def load_correspondences(path: Path) -> dict[tuple[int, int], list[Correspondence]]:
    grouped: dict[tuple[int, int], list[Correspondence]] = defaultdict(list)
    with path.open() as f:
        for row in csv.DictReader(f):
            key = (int(row["frame_index"]), int(row["paired_frame_index"]))
            on_road_raw = str(row.get("on_road", "")).strip().lower()
            on_road = on_road_raw in ("1", "true", "yes")
            if not on_road_raw and str(row.get("side", "")).strip():
                on_road = True
            grouped[key].append(
                Correspondence(
                    source_x=float(row["source_x"]),
                    source_y=float(row["source_y"]),
                    target_x=float(row["target_x"]),
                    target_y=float(row["target_y"]),
                    on_road=on_road,
                )
            )
    return grouped


def match_color(on_road: bool) -> tuple[int, int, int]:
    if on_road:
        return (80, 80, 255)
    return (255, 255, 255)


def sample_correspondences(correspondences: list[Correspondence], limit: int) -> list[Correspondence]:
    if limit <= 0 or len(correspondences) <= limit:
        return correspondences
    idx = np.linspace(0, len(correspondences) - 1, num=limit, dtype=np.int32)
    return [correspondences[int(i)] for i in idx]


def draw_text_block(
    image: np.ndarray,
    lines: list[str],
    origin: tuple[int, int] = (18, 30),
) -> None:
    if not lines:
        return
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.65
    thickness = 2
    line_height = 28
    x0, y0 = origin
    widths = [cv2.getTextSize(line, font, scale, thickness)[0][0] for line in lines]
    block_w = max(widths) + 20
    block_h = 16 + line_height * len(lines)
    overlay = image.copy()
    cv2.rectangle(overlay, (x0 - 10, y0 - 22), (x0 - 10 + block_w, y0 - 22 + block_h), (12, 12, 12), -1)
    image[:] = cv2.addWeighted(overlay, 0.52, image, 0.48, 0.0)
    for idx, line in enumerate(lines):
        cv2.putText(
            image,
            line,
            (x0, y0 + idx * line_height),
            font,
            scale,
            (245, 245, 245),
            thickness,
            cv2.LINE_AA,
        )


def render_pair_frame(
    bgr: np.ndarray,
    pair_log: PairLog,
    correspondences: list[Correspondence],
    total_correspondences: int,
    line_threshold_px: float,
) -> np.ndarray:
    canvas = bgr.copy()
    draw_text_block(
        canvas,
        [
            f"SIFT debug  frame {pair_log.paired_frame_index}  pair {pair_log.frame_index}->{pair_log.paired_frame_index}",
            f"status={pair_log.status}  good={pair_log.good_match_count}  inliers={pair_log.inlier_count}",
            f"shown={len(correspondences)} / {total_correspondences}  on-road={pair_log.on_road_count}  triangulated={pair_log.triangulated_left + pair_log.triangulated_right}",
        ],
    )

    for corr in correspondences:
        color = match_color(corr.on_road)
        src = np.array([float(corr.source_x), float(corr.source_y)], dtype=np.float64)
        dst = np.array([float(corr.target_x), float(corr.target_y)], dtype=np.float64)
        pt_src = tuple(int(round(v)) for v in src)
        pt_dst = tuple(int(round(v)) for v in dst)
        displacement = float(np.linalg.norm(dst - src))
        if displacement > line_threshold_px:
            cv2.line(canvas, pt_src, pt_dst, color, 2, cv2.LINE_AA)
            cv2.circle(canvas, pt_src, 3, (16, 16, 16), -1, cv2.LINE_AA)
            cv2.circle(canvas, pt_src, 2, color, -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_dst, 6, (18, 18, 18), -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_dst, 4, color, -1, cv2.LINE_AA)

    return canvas


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    correspondences_csv = default_correspondences_csv(recording)
    pair_logs_path = default_pair_logs_path(recording)
    output_path = default_output_path(recording)

    if not recording.exists():
        raise FileNotFoundError(f"Recording not found: {recording}")
    if not correspondences_csv.exists():
        raise FileNotFoundError(f"Correspondence CSV not found: {correspondences_csv}")
    if not pair_logs_path.exists():
        raise FileNotFoundError(f"Pair log JSON not found: {pair_logs_path}")

    pair_logs = sorted(load_pair_logs(pair_logs_path), key=lambda row: (row.frame_index, row.paired_frame_index))
    if not pair_logs:
        raise RuntimeError("No pair logs found")
    grouped = load_correspondences(correspondences_csv)
    total_correspondence_count = sum(len(rows) for rows in grouped.values())
    if total_correspondence_count == 0:
        raise RuntimeError(
            f"No correspondences found in {correspondences_csv}. "
            "Rerun the triangulated stage to regenerate the SIFT debug data."
        )

    needed_indices = {row.frame_index for row in pair_logs} | {row.paired_frame_index for row in pair_logs}
    frame_reader = IndexedFrameReader(recording, needed_indices)
    writer: cv2.VideoWriter | None = None
    try:
        first_bgr = frame_reader.read_bgr(pair_logs[0].paired_frame_index)

        first_correspondences = sample_correspondences(
            grouped.get((pair_logs[0].frame_index, pair_logs[0].paired_frame_index), []),
            args.max_correspondences,
        )
        first_canvas = render_pair_frame(
            bgr=first_bgr,
            pair_log=pair_logs[0],
            correspondences=first_correspondences,
            total_correspondences=len(grouped.get((pair_logs[0].frame_index, pair_logs[0].paired_frame_index), [])),
            line_threshold_px=float(args.trail_threshold_px),
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*args.codec),
            float(args.fps),
            (first_canvas.shape[1], first_canvas.shape[0]),
        )
        if not writer.isOpened():
            raise RuntimeError(f"Failed to open video writer for {output_path}")

        writer.write(first_canvas)
        for pair_idx, pair_log in enumerate(pair_logs[1:], start=1):
            pair_key = (pair_log.frame_index, pair_log.paired_frame_index)
            correspondences = grouped.get(pair_key, [])
            canvas = render_pair_frame(
                bgr=frame_reader.read_bgr(pair_log.paired_frame_index),
                pair_log=pair_log,
                correspondences=sample_correspondences(correspondences, args.max_correspondences),
                total_correspondences=len(correspondences),
                line_threshold_px=float(args.trail_threshold_px),
            )
            writer.write(canvas)
            if pair_idx % 100 == 0:
                print(f"rendered {pair_idx + 1} / {len(pair_logs)} pairs")
    finally:
        if writer is not None:
            writer.release()
        frame_reader.close()

    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
