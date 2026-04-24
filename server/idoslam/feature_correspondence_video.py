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


@dataclass
class Correspondence:
    source_x: float
    source_y: float
    target_x: float
    target_y: float
    side: str


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
    p.add_argument("--correspondences-csv", type=Path, default=None, help="Path to point_correspondences.csv")
    p.add_argument("--pair-logs", type=Path, default=None, help="Path to pair_logs.json")
    p.add_argument("--output", type=Path, default=None, help="Output mp4 path")
    p.add_argument("--fps", type=float, default=12.0)
    p.add_argument("--codec", type=str, default="mp4v")
    p.add_argument("--max-correspondences", type=int, default=160)
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
        )
        for row in rows
    ]


def load_correspondences(path: Path) -> dict[tuple[int, int], list[Correspondence]]:
    grouped: dict[tuple[int, int], list[Correspondence]] = defaultdict(list)
    with path.open() as f:
        for row in csv.DictReader(f):
            key = (int(row["frame_index"]), int(row["paired_frame_index"]))
            grouped[key].append(
                Correspondence(
                    source_x=float(row["source_x"]),
                    source_y=float(row["source_y"]),
                    target_x=float(row["target_x"]),
                    target_y=float(row["target_y"]),
                    side=row.get("side", ""),
                )
            )
    return grouped


def side_color(side: str) -> tuple[int, int, int]:
    if side == "left":
        return (80, 220, 255)
    if side == "right":
        return (120, 255, 140)
    return (255, 255, 255)


def sample_correspondences(correspondences: list[Correspondence], limit: int) -> list[Correspondence]:
    if limit <= 0 or len(correspondences) <= limit:
        return correspondences
    idx = np.linspace(0, len(correspondences) - 1, num=limit, dtype=np.int32)
    return [correspondences[int(i)] for i in idx]


def render_pair_frame(
    left_bgr: np.ndarray,
    right_bgr: np.ndarray,
    pair_log: PairLog,
    correspondences: list[Correspondence],
    total_correspondences: int,
) -> np.ndarray:
    header_h = 108
    h, w = left_bgr.shape[:2]
    canvas = np.full((h + header_h, w * 2, 3), (18, 18, 18), dtype=np.uint8)
    canvas[header_h : header_h + h, :w] = left_bgr
    canvas[header_h : header_h + h, w:] = right_bgr

    cv2.putText(canvas, f"Frame {pair_log.frame_index}", (24, header_h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(canvas, f"Frame {pair_log.paired_frame_index}", (w + 24, header_h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(
        canvas,
        f"pair {pair_log.frame_index}->{pair_log.paired_frame_index}  status={pair_log.status}",
        (24, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.78,
        (240, 240, 240),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"good={pair_log.good_match_count}  inliers={pair_log.inlier_count}  triangulated L/R={pair_log.triangulated_left}/{pair_log.triangulated_right}",
        (24, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (215, 215, 215),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"rendered correspondences={len(correspondences)} / {total_correspondences}",
        (24, 92),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (185, 185, 185),
        2,
        cv2.LINE_AA,
    )

    for corr in correspondences:
        color = side_color(corr.side)
        pt_a = (int(round(corr.source_x)), header_h + int(round(corr.source_y)))
        pt_b = (w + int(round(corr.target_x)), header_h + int(round(corr.target_y)))
        cv2.line(canvas, pt_a, pt_b, color, 1, cv2.LINE_AA)
        cv2.circle(canvas, pt_a, 3, color, -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_b, 3, color, -1, cv2.LINE_AA)

    return canvas


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    correspondences_csv = (
        args.correspondences_csv.resolve() if args.correspondences_csv else default_correspondences_csv(recording)
    )
    pair_logs_path = args.pair_logs.resolve() if args.pair_logs else default_pair_logs_path(recording)
    output_path = args.output.resolve() if args.output else default_output_path(recording)

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

    needed_indices = {row.frame_index for row in pair_logs} | {row.paired_frame_index for row in pair_logs}
    frame_reader = IndexedFrameReader(recording, needed_indices)
    writer: cv2.VideoWriter | None = None
    try:
        first_left = frame_reader.read_bgr(pair_logs[0].frame_index)
        first_right = frame_reader.read_bgr(pair_logs[0].paired_frame_index)
        if first_left.shape[:2] != first_right.shape[:2]:
            raise RuntimeError("Correspondence video requires consistent frame dimensions")

        first_correspondences = sample_correspondences(
            grouped.get((pair_logs[0].frame_index, pair_logs[0].paired_frame_index), []),
            args.max_correspondences,
        )
        first_canvas = render_pair_frame(
            left_bgr=first_left,
            right_bgr=first_right,
            pair_log=pair_logs[0],
            correspondences=first_correspondences,
            total_correspondences=len(grouped.get((pair_logs[0].frame_index, pair_logs[0].paired_frame_index), [])),
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
                left_bgr=frame_reader.read_bgr(pair_log.frame_index),
                right_bgr=frame_reader.read_bgr(pair_log.paired_frame_index),
                pair_log=pair_log,
                correspondences=sample_correspondences(correspondences, args.max_correspondences),
                total_correspondences=len(correspondences),
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
