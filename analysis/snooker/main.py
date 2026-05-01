#!/usr/bin/env python3
"""Interactive top-view snooker editor for Pongtown protobuf files.

Usage:
    cd server
    uv run python ../analysis/snooker/main.py ../recordings/<name>/<name>.pongtown.pb
"""

from __future__ import annotations

import argparse
import sys
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Rectangle
from matplotlib.widgets import Button, RadioButtons, Slider

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from analysis.snooker.editor import (
    BALL_RADIUS_MM,
    BallObservationSeed,
    COLOR_BY_NAME,
    DEFAULT_MATCH_RADIUS_MM,
    SNOOKER_COLORS,
    SnookerPongtownEditor,
    color_for_track,
    load_pongtown,
)
from proto import perceiver_pb2, segmentation_pb2
from streamlog.protoio import ProtoIO

_FRAME_IO = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_SEG_IO = ProtoIO(segmentation_pb2.SegmentationResponse)

_SEG_COLORS = (
    np.array([230, 45, 60], dtype=np.float32),
    np.array([40, 185, 95], dtype=np.float32),
    np.array([55, 120, 230], dtype=np.float32),
    np.array([240, 205, 55], dtype=np.float32),
    np.array([205, 75, 210], dtype=np.float32),
    np.array([45, 200, 210], dtype=np.float32),
    np.array([245, 135, 45], dtype=np.float32),
    np.array([180, 220, 70], dtype=np.float32),
)

_COLOR_RGB = {
    "white": np.array([245, 242, 232], dtype=np.float32),
    "red": np.array([216, 32, 39], dtype=np.float32),
    "yellow": np.array([242, 210, 60], dtype=np.float32),
    "green": np.array([31, 141, 77], dtype=np.float32),
    "brown": np.array([122, 72, 44], dtype=np.float32),
    "blue": np.array([38, 114, 217], dtype=np.float32),
    "pink": np.array([240, 138, 183], dtype=np.float32),
    "black": np.array([17, 17, 17], dtype=np.float32),
}

DEFAULT_OCCLUSION_THRESHOLD = 0.10
DEFAULT_OCCLUSION_DILATE_PX = 15


def _decode_frame_rgb(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray | None:
    img = frame.rgb_frame
    if not img or not img.data:
        return None
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if img.format == ImageFormat.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        if img.width and img.height and raw.size >= img.width * img.height * 3:
            return raw[: img.width * img.height * 3].reshape(int(img.height), int(img.width), 3)
        total = raw.size // 3
        side = int(total ** 0.5)
        if side * side * 3 <= raw.size:
            return raw[: side * side * 3].reshape((side, side, 3))
    return None


def _decode_mask(data: bytes) -> np.ndarray:
    h, w = struct.unpack("<II", data[:8])
    packed = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8)
    return np.unpackbits(packed)[: h * w].reshape(h, w).astype(bool)


def _mask_centroid(mask: np.ndarray) -> tuple[float, float, int] | None:
    if mask is None or not mask.any():
        return None
    n, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = int(np.argmax(areas)) + 1
    area = int(stats[best, cv2.CC_STAT_AREA])
    if area < 2:
        return None
    cx, cy = centroids[best]
    return float(cx), float(cy), area


def _snooker_track_id_from_rgb(rgb: np.ndarray | None, mask: np.ndarray) -> int:
    if rgb is None or rgb.shape[:2] != mask.shape or not mask.any():
        return COLOR_BY_NAME["red"].track_id
    mean = rgb[mask].astype(np.float32).mean(axis=0)
    # The table-green background often leaks into tiny ball masks. Penalise
    # green unless it is a strong match, so unknown balls default to red.
    best_name = "red"
    best_dist = float("inf")
    for name, ref in _COLOR_RGB.items():
        dist = float(np.linalg.norm(mean - ref))
        if name == "green":
            dist *= 1.25
        if dist < best_dist:
            best_name = name
            best_dist = dist
    return COLOR_BY_NAME[best_name].track_id


def _snooker_track_id_from_point(rgb: np.ndarray | None, u_img: float, v_img: float) -> int:
    if rgb is None:
        return COLOR_BY_NAME["red"].track_id
    h, w = rgb.shape[:2]
    x = int(round(float(u_img)))
    y = int(round(float(v_img)))
    if x < 0 or x >= w or y < 0 or y >= h:
        return COLOR_BY_NAME["red"].track_id
    x0, x1 = max(0, x - 2), min(w, x + 3)
    y0, y1 = max(0, y - 2), min(h, y + 3)
    patch = rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return COLOR_BY_NAME["red"].track_id
    mean = patch.reshape(-1, 3).astype(np.float32).mean(axis=0)
    best_name = "red"
    best_dist = float("inf")
    for name, ref in _COLOR_RGB.items():
        dist = float(np.linalg.norm(mean - ref))
        if name == "green":
            dist *= 1.25
        if dist < best_dist:
            best_name = name
            best_dist = dist
    return COLOR_BY_NAME[best_name].track_id


def _fallback_image(label: str, shape: tuple[int, int] = (240, 360)) -> np.ndarray:
    h, w = shape
    canvas = np.full((h, w, 3), 28, dtype=np.uint8)
    cv2.putText(canvas, label, (18, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (210, 210, 210), 2)
    return canvas


@dataclass(frozen=True)
class OcclusionSignal:
    score: float
    is_red: bool
    table_area_px: int
    person_area_px: int
    person_table_px: int


@dataclass
class RecordingContext:
    vis_path: Path | None
    seg_path: Path | None
    frames_by_number: dict[int, perceiver_pb2.PerceiverDataFrame] = field(default_factory=dict)
    seg_by_number: dict[int, segmentation_pb2.SegmentationResponse] = field(default_factory=dict)
    rgb_cache: dict[int, np.ndarray] = field(default_factory=dict)
    seg_cache: dict[int, np.ndarray] = field(default_factory=dict)
    ball_seed_cache: dict[int, list[BallObservationSeed]] | None = None
    occlusion_cache: dict[tuple[int, int, float], OcclusionSignal] = field(default_factory=dict)

    @classmethod
    def load(cls, vis_path: Path | None, seg_path: Path | None) -> "RecordingContext":
        ctx = cls(vis_path=vis_path if vis_path and vis_path.exists() else None,
                  seg_path=seg_path if seg_path and seg_path.exists() else None)
        if ctx.vis_path is not None:
            for frame in _FRAME_IO.read_file(ctx.vis_path):
                ctx.frames_by_number[int(frame.frame_identifier.frame_number)] = frame
        if ctx.seg_path is not None:
            for resp in _SEG_IO.read_file(ctx.seg_path):
                ctx.seg_by_number[int(resp.frame_identifier.frame_number)] = resp
        return ctx

    def rgb_for(self, frame_number: int) -> np.ndarray:
        if self.vis_path is None:
            return _fallback_image("vis.pb not loaded")
        if frame_number not in self.rgb_cache:
            frame = self.frames_by_number.get(int(frame_number))
            rgb = _decode_frame_rgb(frame) if frame is not None else None
            self.rgb_cache[int(frame_number)] = rgb if rgb is not None else _fallback_image("frame not found")
        return self.rgb_cache[int(frame_number)]

    def seg_for(self, frame_number: int, base_rgb: np.ndarray | None = None) -> np.ndarray:
        if self.seg_path is None:
            return _fallback_image("segmentation.pb not loaded")
        if frame_number not in self.seg_cache:
            resp = self.seg_by_number.get(int(frame_number))
            if resp is None:
                self.seg_cache[int(frame_number)] = _fallback_image("seg frame not found")
            else:
                self.seg_cache[int(frame_number)] = self._render_segmentation(resp, base_rgb)
        return self.seg_cache[int(frame_number)]

    def _render_segmentation(
        self,
        resp: segmentation_pb2.SegmentationResponse,
        base_rgb: np.ndarray | None,
    ) -> np.ndarray:
        decoded: list[tuple[str, np.ndarray]] = []
        h = w = None
        for mask in resp.masks:
            try:
                arr = _decode_mask(mask.mask_data)
            except Exception:
                continue
            decoded.append((mask.label or f"id {mask.object_id}", arr))
            h, w = arr.shape
        if h is None or w is None:
            return _fallback_image("no masks")

        if base_rgb is not None:
            base = base_rgb.copy()
            if base.shape[:2] != (h, w):
                base = cv2.resize(base, (w, h), interpolation=cv2.INTER_AREA)
            canvas = (0.45 * base.astype(np.float32)).astype(np.uint8)
        else:
            canvas = np.full((h, w, 3), 24, dtype=np.uint8)

        out = canvas.astype(np.float32)
        for idx, (_label, arr) in enumerate(decoded):
            color = _SEG_COLORS[idx % len(_SEG_COLORS)]
            if arr.shape != (h, w):
                arr = cv2.resize(arr.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
            out[arr] = 0.45 * out[arr] + 0.55 * color
        return np.clip(out, 0, 255).astype(np.uint8)

    def ball_observation_seeds(self) -> dict[int, list[BallObservationSeed]]:
        if self.ball_seed_cache is not None:
            return self.ball_seed_cache
        seeds: dict[int, list[BallObservationSeed]] = {}
        if self.seg_path is None:
            self.ball_seed_cache = seeds
            return seeds

        for frame_number, resp in self.seg_by_number.items():
            rgb = None
            if self.vis_path is not None:
                rgb = self.rgb_for(frame_number)
            frame_seeds: list[BallObservationSeed] = []
            for mask_msg in resp.masks:
                if "ball" not in (mask_msg.label or "").lower():
                    continue
                try:
                    mask = _decode_mask(mask_msg.mask_data)
                except Exception:
                    continue
                cent = _mask_centroid(mask)
                if cent is None:
                    continue
                u_img, v_img, area_px = cent
                if rgb is not None and mask.shape != rgb.shape[:2]:
                    sx = rgb.shape[1] / max(1, mask.shape[1])
                    sy = rgb.shape[0] / max(1, mask.shape[0])
                    u_img *= sx
                    v_img *= sy
                    area_px = int(round(area_px * sx * sy))
                    track_id = _snooker_track_id_from_point(rgb, u_img, v_img)
                else:
                    track_id = _snooker_track_id_from_rgb(rgb, mask)
                frame_seeds.append(
                    BallObservationSeed(
                        u_img=u_img,
                        v_img=v_img,
                        area_px=area_px,
                        confidence=float(mask_msg.confidence),
                        track_id=track_id,
                    )
                )
            if frame_seeds:
                seeds[int(frame_number)] = frame_seeds
        self.ball_seed_cache = seeds
        return seeds

    def occlusion_signal(
        self,
        frame_number: int,
        *,
        dilate_px: int = DEFAULT_OCCLUSION_DILATE_PX,
        threshold: float = DEFAULT_OCCLUSION_THRESHOLD,
    ) -> OcclusionSignal:
        key = (int(frame_number), int(dilate_px), round(float(threshold), 6))
        if key in self.occlusion_cache:
            return self.occlusion_cache[key]

        resp = self.seg_by_number.get(int(frame_number))
        table = person = None
        if resp is not None:
            for mask_msg in resp.masks:
                label = (mask_msg.label or "").lower()
                try:
                    mask = _decode_mask(mask_msg.mask_data)
                except Exception:
                    continue
                if self._is_table_top_label(label):
                    table = mask if table is None else (table | mask)
                elif "person" in label:
                    person = mask if person is None else (person | mask)

        if table is None or person is None or not table.any():
            signal = OcclusionSignal(0.0, False, 0, int(person.sum()) if person is not None else 0, 0)
            self.occlusion_cache[key] = signal
            return signal

        if person.shape != table.shape:
            person = cv2.resize(
                person.astype(np.uint8),
                (table.shape[1], table.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)

        k = max(1, int(dilate_px))
        kernel = np.ones((k, k), dtype=np.uint8)
        table_region = cv2.dilate(table.astype(np.uint8), kernel, iterations=1).astype(bool)
        person_table_px = int((person & table_region).sum())
        table_area_px = int(table.sum())
        score = person_table_px / max(1, table_area_px)
        signal = OcclusionSignal(
            score=float(score),
            is_red=bool(score >= float(threshold)),
            table_area_px=table_area_px,
            person_area_px=int(person.sum()),
            person_table_px=person_table_px,
        )
        self.occlusion_cache[key] = signal
        return signal

    def occluded_frame_indices(
        self,
        model: SnookerPongtownEditor,
        *,
        dilate_px: int = DEFAULT_OCCLUSION_DILATE_PX,
        threshold: float = DEFAULT_OCCLUSION_THRESHOLD,
    ) -> list[int]:
        out: list[int] = []
        for fi in range(model.frame_count):
            _frame_idx, frame_number, _timestamp_ns = model.frame_meta(fi)
            if self.occlusion_signal(frame_number, dilate_px=dilate_px, threshold=threshold).is_red:
                out.append(fi)
        return out

    @staticmethod
    def _is_table_top_label(label: str) -> bool:
        label = label.lower()
        return "table" in label and ("top" in label or "snooker" in label or "surface" in label)


def _derive_context_paths(pongtown_path: Path) -> tuple[Path, Path]:
    stem = pongtown_path.name.removesuffix(".pongtown.pb")
    vis_path = pongtown_path.parent / f"{stem}.vis.pb"
    primary_seg = pongtown_path.parent / f"{stem}.segmentation.pb"
    legacy_seg = pongtown_path.parent / f"{stem}.seg.pb"
    seg_path = primary_seg if primary_seg.exists() or not legacy_seg.exists() else legacy_seg
    return vis_path, seg_path


class SnookerEditorApp:
    def __init__(
        self,
        model: SnookerPongtownEditor,
        *,
        context: RecordingContext | None = None,
        output_path: Path | None = None,
        backup: bool = True,
        initial_status: str | None = None,
        occlusion_threshold: float = DEFAULT_OCCLUSION_THRESHOLD,
        occlusion_dilate_px: int = DEFAULT_OCCLUSION_DILATE_PX,
    ) -> None:
        self.model = model
        self.context = context or RecordingContext(None, None)
        self.output_path = output_path
        self.backup = backup
        self.occlusion_threshold = float(occlusion_threshold)
        self.occlusion_dilate_px = int(occlusion_dilate_px)
        self.current_frame = model.first_frame_with_balls()
        self.selected: tuple[int, int] | None = None
        self.dragging = False
        self.pick_mode: str | None = None
        self.interp_start_xy: tuple[float, float] | None = None
        self.interp_end_xy: tuple[float, float] | None = None
        self.active_color_name = "white"
        self.status = initial_status or "Loaded"

        self.fig = plt.figure(figsize=(16, 9))
        self.fig.canvas.manager.set_window_title("Snooker Pongtown Editor")
        self.ax = self.fig.add_axes([0.04, 0.18, 0.50, 0.76])
        self.ax.set_aspect("equal", adjustable="box")
        self.ax_vis = self.fig.add_axes([0.57, 0.48, 0.40, 0.43])
        self.ax_seg = self.fig.add_axes([0.57, 0.26, 0.18, 0.18])
        for ax in (self.ax_vis, self.ax_seg):
            ax.axis("off")

        max_frame = max(1, self.model.frame_count - 1)
        self.frame_slider = Slider(
            self.fig.add_axes([0.095, 0.09, 0.42, 0.03]),
            "Frame",
            0,
            max_frame,
            valinit=self.current_frame,
            valstep=1,
            valfmt="%0.0f",
        )
        self.start_slider = Slider(
            self.fig.add_axes([0.08, 0.045, 0.22, 0.025]),
            "Start",
            0,
            max_frame,
            valinit=0,
            valstep=1,
            valfmt="%0.0f",
        )
        self.end_slider = Slider(
            self.fig.add_axes([0.36, 0.045, 0.22, 0.025]),
            "End",
            0,
            max_frame,
            valinit=max_frame,
            valstep=1,
            valfmt="%0.0f",
        )

        self.radio = RadioButtons(
            self.fig.add_axes([0.77, 0.23, 0.09, 0.22]),
            [c.name for c in SNOOKER_COLORS],
            active=0,
        )
        for label in self.radio.labels:
            label.set_fontsize(8)

        self.buttons: dict[str, Button] = {}
        self._add_button("prev_frame", "<", [0.055, 0.09, 0.028, 0.030], self.prev_frame)
        self._add_button("next_frame", ">", [0.525, 0.09, 0.028, 0.030], self.next_frame)
        self._add_button("add", "Add Ball", [0.88, 0.420, 0.09, 0.026], self.add_ball_mode)
        self._add_button("delete", "Delete Future", [0.88, 0.388, 0.09, 0.026], self.delete_future)
        self._add_button("color", "Color Future", [0.88, 0.356, 0.09, 0.026], self.color_future)
        self._add_button("move", "Move Future", [0.88, 0.324, 0.09, 0.026], self.move_future)
        self._add_button("pick_start", "Pick Start", [0.88, 0.292, 0.09, 0.026], self.pick_start)
        self._add_button("pick_end", "Pick End", [0.88, 0.260, 0.09, 0.026], self.pick_end)
        self._add_button("interp", "Interpolate", [0.88, 0.228, 0.09, 0.026], self.interpolate)
        self._add_button("save", "Save", [0.88, 0.196, 0.09, 0.026], self.save)

        self.info_text = self.fig.text(0.57, 0.21, "", fontsize=8, va="top", family="monospace")

        self.frame_slider.on_changed(self.on_frame_slider)
        self.radio.on_clicked(self.on_color)
        self.fig.canvas.mpl_connect("button_press_event", self.on_press)
        self.fig.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.fig.canvas.mpl_connect("button_release_event", self.on_release)
        self.fig.canvas.mpl_connect("key_press_event", self.on_key)
        self.redraw()

    def _add_button(self, key: str, label: str, rect: list[float], callback) -> None:
        button = Button(self.fig.add_axes(rect), label)
        button.label.set_fontsize(7.5)
        button.on_clicked(callback)
        self.buttons[key] = button

    def run(self) -> None:
        plt.show()

    def on_frame_slider(self, value: float) -> None:
        self.current_frame = max(0, min(int(value), self.model.frame_count - 1))
        if self.selected and self.selected[0] != self.current_frame:
            self.selected = None
        self.redraw()

    def on_color(self, label: str) -> None:
        self.active_color_name = label
        self.status = f"Active color: {label}"
        self.redraw()

    def prev_frame(self, _event) -> None:
        self._set_frame(self.current_frame - 1)

    def next_frame(self, _event) -> None:
        self._set_frame(self.current_frame + 1)

    def on_key(self, event) -> None:
        if event.key == "right":
            self.next_frame(None)
        elif event.key == "left":
            self.prev_frame(None)
        elif event.key == "s":
            self.save(None)
        elif event.key == "d":
            self.delete_future(None)
        elif event.key == "m":
            self.move_future(None)
        elif event.key == "i":
            self.interpolate(None)
        elif event.key == "a":
            self.add_ball_mode(None)
        elif event.key in [str(i) for i in range(1, 9)]:
            idx = int(event.key) - 1
            self.active_color_name = SNOOKER_COLORS[idx].name
            self.radio.set_active(idx)

    def on_press(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return

        x_mm, y_mm = float(event.xdata), float(event.ydata)
        if self.pick_mode == "add":
            color = COLOR_BY_NAME[self.active_color_name]
            idx = self.model.add_ball(self.current_frame, color.track_id, x_mm, y_mm)
            self.selected = (self.current_frame, idx)
            self.pick_mode = None
            self.status = f"Added {color.name} ball"
            self.redraw()
            return

        if self.pick_mode == "start":
            self.interp_start_xy = (x_mm, y_mm)
            self.pick_mode = None
            self.status = f"Interpolation start set: {x_mm:.0f}, {y_mm:.0f} mm"
            self.redraw()
            return

        if self.pick_mode == "end":
            self.interp_end_xy = (x_mm, y_mm)
            self.pick_mode = None
            self.status = f"Interpolation end set: {x_mm:.0f}, {y_mm:.0f} mm"
            self.redraw()
            return

        nearest = self.model.nearest_ball(
            self.current_frame,
            x_mm,
            y_mm,
            radius_mm=max(80.0, self.model.match_radius_mm),
        )
        if nearest is None:
            self.selected = None
            self.status = "No ball selected"
            self.redraw()
            return

        idx, _ = nearest
        self.selected = (self.current_frame, idx)
        self.dragging = True
        ball = self.model.balls(self.current_frame)[idx]
        self.status = f"Selected {color_for_track(ball.track_id).name}"
        self.redraw()

    def on_motion(self, event) -> None:
        if not self.dragging or self.selected is None:
            return
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return
        frame_index, ball_index = self.selected
        self.model.set_ball_position(frame_index, ball_index, float(event.xdata), float(event.ydata))
        self.status = "Moved selected ball in current frame"
        self.redraw()

    def on_release(self, _event) -> None:
        self.dragging = False

    def add_ball_mode(self, _event) -> None:
        self.pick_mode = "add"
        self.status = "Click the table to add a ball"
        self.redraw()

    def pick_start(self, _event) -> None:
        self.pick_mode = "start"
        self.status = "Click the table to set interpolation start position"
        self.redraw()

    def pick_end(self, _event) -> None:
        self.pick_mode = "end"
        self.status = "Click the table to set interpolation end position"
        self.redraw()

    def delete_future(self, _event) -> None:
        if self.selected is None:
            self.status = "Select a ball first"
            self.redraw()
            return
        frame_index, ball_index = self.selected
        removed = self.model.delete_future(frame_index, ball_index)
        self.selected = None
        self.status = f"Deleted {removed} matched future observation(s)"
        self.redraw()

    def color_future(self, _event) -> None:
        if self.selected is None:
            self.status = "Select a ball first"
            self.redraw()
            return
        color = COLOR_BY_NAME[self.active_color_name]
        frame_index, ball_index = self.selected
        changed = self.model.change_color_future(frame_index, ball_index, color.track_id)
        self.status = f"Changed {changed} matched future observation(s) to {color.name}"
        self.redraw()

    def move_future(self, _event) -> None:
        if self.selected is None:
            self.status = "Select a ball first"
            self.redraw()
            return
        frame_index, ball_index = self.selected
        xy = self.model.ball_xy(frame_index, ball_index)
        if xy is None:
            self.status = "Selected ball has no table position"
            self.redraw()
            return
        changed = self.model.move_future(frame_index, ball_index, xy[0], xy[1])
        self.status = f"Moved {changed} matched future observation(s)"
        self.redraw()

    def interpolate(self, _event) -> None:
        if self.selected is None:
            self.status = "Select a ball first"
            self.redraw()
            return
        if self.interp_start_xy is None or self.interp_end_xy is None:
            self.status = "Pick both interpolation positions first"
            self.redraw()
            return
        frame_index, ball_index = self.selected
        changed = self.model.interpolate(
            frame_index,
            ball_index,
            int(self.start_slider.val),
            int(self.end_slider.val),
            self.interp_start_xy,
            self.interp_end_xy,
        )
        self.status = f"Interpolated {changed} frame(s)"
        self.redraw()

    def save(self, _event) -> None:
        out = self.model.save(self.output_path, backup=self.backup)
        self.status = f"Saved {out}"
        self.redraw()

    def redraw(self) -> None:
        self.ax.clear()
        self.ax_vis.clear()
        self.ax_seg.clear()
        self._draw_table()
        self._draw_selected_track()
        self._draw_balls()
        self._draw_interpolation_markers()
        self._draw_context_panels()
        self._draw_labels()
        self.fig.canvas.draw_idle()

    def _draw_table(self) -> None:
        w = self.model.table_width_mm
        h = self.model.table_height_mm
        self.ax.set_facecolor("#123d2a")
        self.ax.add_patch(
            Rectangle(
                (-w / 2, -h / 2),
                w,
                h,
                facecolor="#1d6b45",
                edgecolor="#7a482c",
                linewidth=8,
                zorder=0,
            )
        )
        pocket_r = max(55.0, min(w, h) * 0.035)
        for x, y in [
            (-w / 2, -h / 2),
            (0.0, -h / 2),
            (w / 2, -h / 2),
            (-w / 2, h / 2),
            (0.0, h / 2),
            (w / 2, h / 2),
        ]:
            self.ax.add_patch(Circle((x, y), pocket_r, facecolor="#050505", edgecolor="#0a0a0a", zorder=1))
        self.ax.axvline(0.0, color="white", alpha=0.18, linewidth=1)
        margin = max(250.0, min(w, h) * 0.12)
        self.ax.set_xlim(-w / 2 - margin, w / 2 + margin)
        self.ax.set_ylim(-h / 2 - margin, h / 2 + margin)
        self.ax.set_xlabel("table x (mm)")
        self.ax.set_ylabel("table y (mm)")

    def _draw_balls(self) -> None:
        balls = self.model.balls(self.current_frame)
        for idx, ball in enumerate(balls):
            xy = self.model.ball_xy(self.current_frame, idx)
            if xy is None:
                continue
            color = color_for_track(ball.track_id)
            selected = self.selected == (self.current_frame, idx)
            radius = BALL_RADIUS_MM * (1.35 if selected else 1.0)
            edge = "#ffffff" if color.name == "black" else "#111111"
            self.ax.add_patch(
                Circle(
                    xy,
                    radius,
                    facecolor=color.hex_color,
                    edgecolor="#ffffff" if selected else edge,
                    linewidth=2.5 if selected else 1.0,
                    zorder=4,
                )
            )
            self.ax.text(
                xy[0],
                xy[1],
                color.name[:1].upper(),
                ha="center",
                va="center",
                color=color.text_color,
                fontsize=8,
                zorder=5,
            )

    def _draw_selected_track(self) -> None:
        if self.selected is None:
            return
        frame_index, ball_index = self.selected
        if frame_index >= self.model.frame_count or ball_index >= len(self.model.balls(frame_index)):
            return
        matches = self.model.match_future(frame_index, ball_index)
        if len(matches) < 2:
            return
        xs = [m.x_mm for m in matches]
        ys = [m.y_mm for m in matches]
        self.ax.plot(xs, ys, color="#ffffff", alpha=0.35, linewidth=1.4, zorder=2)

    def _draw_interpolation_markers(self) -> None:
        if self.interp_start_xy is not None:
            self.ax.scatter(
                [self.interp_start_xy[0]],
                [self.interp_start_xy[1]],
                marker="x",
                s=80,
                color="#ffffff",
                linewidths=2,
                zorder=6,
            )
        if self.interp_end_xy is not None:
            self.ax.scatter(
                [self.interp_end_xy[0]],
                [self.interp_end_xy[1]],
                marker="+",
                s=100,
                color="#ffffff",
                linewidths=2,
                zorder=6,
            )
        if self.interp_start_xy is not None and self.interp_end_xy is not None:
            self.ax.plot(
                [self.interp_start_xy[0], self.interp_end_xy[0]],
                [self.interp_start_xy[1], self.interp_end_xy[1]],
                color="#ffffff",
                linestyle="--",
                linewidth=1,
                alpha=0.45,
                zorder=2,
            )

    def _draw_context_panels(self) -> None:
        _frame_idx, frame_number, _timestamp_ns = self.model.frame_meta(self.current_frame)
        rgb = self.context.rgb_for(frame_number)
        seg = self.context.seg_for(frame_number, rgb)
        signal = self.context.occlusion_signal(
            frame_number,
            dilate_px=self.occlusion_dilate_px,
            threshold=self.occlusion_threshold,
        )

        for ax, img, title in (
            (self.ax_vis, rgb, "vis.pb RGB"),
            (self.ax_seg, seg, "segmentation.pb"),
        ):
            ax.clear()
            ax.imshow(img)
            self._draw_image_ball_markers(ax, img.shape[:2])
            ax.set_title(title, fontsize=9)
            ax.axis("off")
        self._draw_occlusion_signal(self.ax_vis, signal)

    def _draw_image_ball_markers(self, ax, image_shape: tuple[int, int]) -> None:
        rgb = self.context.rgb_for(self.model.frame_meta(self.current_frame)[1])
        src_h, src_w = rgb.shape[:2]
        dst_h, dst_w = image_shape
        sx = dst_w / max(1, src_w)
        sy = dst_h / max(1, src_h)
        for idx, ball in enumerate(self.model.balls(self.current_frame)):
            if ball.u_img <= 0 and ball.v_img <= 0:
                continue
            color = color_for_track(ball.track_id)
            selected = self.selected == (self.current_frame, idx)
            ax.scatter(
                [float(ball.u_img) * sx],
                [float(ball.v_img) * sy],
                s=70 if selected else 42,
                facecolors="none",
                edgecolors="#ffffff" if selected else color.hex_color,
                linewidths=2.0 if selected else 1.5,
            )

    def _draw_occlusion_signal(self, ax, signal: OcclusionSignal) -> None:
        color = "#d82027" if signal.is_red else "#1f8d4d"
        label = f"person/table {signal.score:.2f}"
        ax.add_patch(
            Circle(
                (0.045, 0.92),
                0.028,
                transform=ax.transAxes,
                facecolor=color,
                edgecolor="white",
                linewidth=1.0,
                zorder=10,
            )
        )
        ax.text(
            0.085,
            0.92,
            label,
            transform=ax.transAxes,
            color="white",
            fontsize=8,
            va="center",
            ha="left",
            zorder=11,
            bbox={"facecolor": "black", "alpha": 0.45, "edgecolor": "none", "pad": 1.5},
        )

    def _draw_labels(self) -> None:
        frame_idx, frame_number, _timestamp_ns = self.model.frame_meta(self.current_frame)
        dirty = "dirty" if self.model.dirty else "clean"
        selected = "none"
        if self.selected is not None:
            _, ball_index = self.selected
            if ball_index < len(self.model.balls(self.current_frame)):
                ball = self.model.balls(self.current_frame)[ball_index]
                xy = self.model.ball_xy(self.current_frame, ball_index)
                if xy is not None:
                    selected = f"{color_for_track(ball.track_id).name} at {xy[0]:.0f}, {xy[1]:.0f}"
        title = f"Frame {self.current_frame + 1}/{self.model.frame_count}  source frame {frame_idx}"
        if frame_number:
            title += f"  number {frame_number}"
        self.ax.set_title(title)

        self.info_text.set_text(
            "\n".join(
                [
                    f"State: {dirty}",
                    f"Selected: {selected}",
                    f"Active color: {self.active_color_name}",
                    f"Range: {int(self.start_slider.val)} -> {int(self.end_slider.val)}",
                    f"Occl: {self.context.occlusion_signal(frame_number, dilate_px=self.occlusion_dilate_px, threshold=self.occlusion_threshold).score:.2f}",
                    f"Mode: {self.pick_mode or 'select/drag'}",
                    "",
                    self.status,
                ]
            )
        )

    def _set_frame(self, frame_index: int) -> None:
        frame_index = max(0, min(frame_index, self.model.frame_count - 1))
        self.frame_slider.set_val(frame_index)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pongtown_file", type=Path, help="Path to the .pongtown.pb file to edit")
    parser.add_argument("--vis", type=Path, help="Path to the matching .vis.pb file")
    parser.add_argument("--seg", type=Path, help="Path to the matching .segmentation.pb file")
    parser.add_argument("--output", type=Path, help="Write edits to this path instead of overwriting input")
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Do not populate missing Pongtown ball positions from segmentation masks",
    )
    parser.add_argument(
        "--no-save-backfill",
        action="store_true",
        help="Backfill in memory only; do not immediately write the Pongtown file",
    )
    parser.add_argument(
        "--no-occlusion-fix",
        action="store_true",
        help="Do not replace red person/table-occluded frame ball positions from the previous frame",
    )
    parser.add_argument(
        "--no-save-occlusion-fix",
        action="store_true",
        help="Apply occlusion fixes in memory only; do not immediately write the Pongtown file",
    )
    parser.add_argument(
        "--person-table-occlusion-threshold",
        type=float,
        default=DEFAULT_OCCLUSION_THRESHOLD,
        help="Red signal threshold for person pixels intruding into the dilated tabletop region",
    )
    parser.add_argument(
        "--person-table-dilate-px",
        type=int,
        default=DEFAULT_OCCLUSION_DILATE_PX,
        help="Dilation kernel size in pixels for person/table occlusion detection",
    )
    parser.add_argument(
        "--match-radius-mm",
        type=float,
        default=DEFAULT_MATCH_RADIUS_MM,
        help="Maximum table-space distance used to follow a selected ball into future frames",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak when overwriting input")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    default_vis, default_seg = _derive_context_paths(args.pongtown_file)
    vis_path = args.vis or default_vis
    seg_path = args.seg or default_seg
    model = load_pongtown(args.pongtown_file, match_radius_mm=args.match_radius_mm)
    context = RecordingContext.load(vis_path, seg_path)
    status_messages: list[str] = []
    if not args.no_backfill and model.total_ball_positions() == 0:
        added = model.ensure_ball_positions_from_image_observations(context.ball_observation_seeds())
        if added:
            if not args.no_save_backfill:
                out = model.save(args.output, backup=not args.no_backup)
                status_messages.append(f"Backfilled and saved {added} ball position(s) to {out}")
            else:
                status_messages.append(f"Backfilled {added} ball position(s); press Save to persist")
        else:
            status_messages.append("No Pongtown ball positions and no segmentation ball seeds found")

    if not args.no_occlusion_fix:
        red_frames = context.occluded_frame_indices(
            model,
            dilate_px=args.person_table_dilate_px,
            threshold=args.person_table_occlusion_threshold,
        )
        fixed = model.copy_ball_positions_from_previous_frames(red_frames)
        if fixed:
            if not args.no_save_occlusion_fix:
                out = model.save(args.output, backup=not args.no_backup)
                status_messages.append(f"Occlusion-fixed and saved {fixed} red frame(s) to {out}")
            else:
                status_messages.append(f"Occlusion-fixed {fixed} red frame(s); press Save to persist")
        elif red_frames:
            status_messages.append(f"{len(red_frames)} red frame(s), none fixable from previous frame")

    initial_status = " | ".join(status_messages) if status_messages else "Loaded"
    app = SnookerEditorApp(
        model,
        context=context,
        output_path=args.output,
        backup=not args.no_backup,
        initial_status=initial_status,
        occlusion_threshold=args.person_table_occlusion_threshold,
        occlusion_dilate_px=args.person_table_dilate_px,
    )
    app.run()


if __name__ == "__main__":
    main()
