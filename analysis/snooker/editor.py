"""Data model for editing snooker balls in Pongtown protobuf files."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVER_ROOT = _PROJECT_ROOT / "server"
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "proto"))
sys.path.insert(0, str(_SERVER_ROOT))

from proto import pongtown_pb2
from streamlog.protoio import ProtoIO

PongtownResponse = pongtown_pb2.PongtownResponse
BallPosition = pongtown_pb2.PongtownResponse.BallPosition
SnookerBallPosition = pongtown_pb2.PongtownResponse.SnookerBallPosition

DEFAULT_TABLE_WIDTH_MM = 3569.0
DEFAULT_TABLE_HEIGHT_MM = 1778.0
DEFAULT_MATCH_RADIUS_MM = 180.0
BALL_RADIUS_MM = 26.25


@dataclass(frozen=True)
class SnookerColor:
    track_id: int
    name: str
    hex_color: str
    text_color: str = "#111111"


SNOOKER_COLORS: tuple[SnookerColor, ...] = (
    SnookerColor(1, "white", "#f5f2e8", "#111111"),
    SnookerColor(2, "red", "#d82027", "#ffffff"),
    SnookerColor(3, "yellow", "#f2d23c", "#111111"),
    SnookerColor(4, "green", "#1f8d4d", "#ffffff"),
    SnookerColor(5, "brown", "#7a482c", "#ffffff"),
    SnookerColor(6, "blue", "#2672d9", "#ffffff"),
    SnookerColor(7, "pink", "#f08ab7", "#111111"),
    SnookerColor(8, "black", "#111111", "#ffffff"),
)

COLOR_BY_ID = {c.track_id: c for c in SNOOKER_COLORS}
COLOR_BY_NAME = {c.name: c for c in SNOOKER_COLORS}

_PONG_IO = ProtoIO(PongtownResponse)


@dataclass(frozen=True)
class MatchResult:
    frame_index: int
    ball_index: int
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class BallObservationSeed:
    u_img: float
    v_img: float
    area_px: int
    confidence: float
    track_id: int


def color_for_track(track_id: int) -> SnookerColor:
    return COLOR_BY_ID.get(int(track_id), SnookerColor(int(track_id), f"id {track_id}", "#aaaaaa"))


def _snooker_label_for_track(track_id: int) -> str:
    return f"{color_for_track(track_id).name} ball"


def _track_for_snooker_label(label: str, object_id: int = 0) -> int:
    normalized = label.lower()
    for color in SNOOKER_COLORS:
        if color.name in normalized:
            return color.track_id
    if int(object_id) in COLOR_BY_ID:
        return int(object_id)
    return COLOR_BY_NAME["red"].track_id


def _clear_repeated(field) -> None:
    del field[:]


def _table_xy(ball: BallPosition) -> tuple[float, float] | None:
    if not ball.has_table_position or len(ball.table_xyz_mm) < 2:
        return None
    return float(ball.table_xyz_mm[0]), float(ball.table_xyz_mm[1])


def _frame_idx(record: PongtownResponse, fallback: int) -> int:
    if record.pnp_frame_debug:
        return int(record.pnp_frame_debug[0].frame_idx)
    if record.frame_output.has_pose or record.frame_output.off_screen:
        return int(record.frame_output.frame_idx)
    if record.ball_positions:
        return int(record.ball_positions[0].frame_idx)
    if record.snooker_tracking.ball_positions:
        return int(record.snooker_tracking.ball_positions[0].frame_idx)
    return int(fallback)


def _frame_number(record: PongtownResponse, fallback: int) -> int:
    if record.frame_identifier.frame_number:
        return int(record.frame_identifier.frame_number)
    if record.ball_positions:
        return int(record.ball_positions[0].frame_number)
    if record.snooker_tracking.ball_positions:
        return int(record.snooker_tracking.ball_positions[0].frame_number)
    return int(fallback)


def _timestamp_ns(record: PongtownResponse) -> int:
    if record.frame_identifier.timestamp_ns:
        return int(record.frame_identifier.timestamp_ns)
    if record.ball_positions:
        return int(record.ball_positions[0].timestamp_ns)
    if record.snooker_tracking.ball_positions:
        return int(record.snooker_tracking.ball_positions[0].timestamp_ns)
    return 0


def _copy_generic_to_snooker(src: BallPosition, dst: SnookerBallPosition, object_id: int) -> None:
    dst.object_id = int(object_id)
    dst.label = _snooker_label_for_track(src.track_id)
    dst.frame_idx = int(src.frame_idx)
    dst.frame_number = int(src.frame_number)
    dst.timestamp_ns = int(src.timestamp_ns)
    dst.u_img = float(src.u_img)
    dst.v_img = float(src.v_img)
    dst.area_px = int(src.area_px)
    dst.confidence = float(src.confidence)
    dst.has_table_position = bool(src.has_table_position)
    _clear_repeated(dst.cam_xyz_mm)
    dst.cam_xyz_mm.extend(float(v) for v in src.cam_xyz_mm)
    _clear_repeated(dst.table_xyz_mm)
    dst.table_xyz_mm.extend(float(v) for v in src.table_xyz_mm)
    dst.inside_table = bool(src.inside_table)


def _copy_snooker_to_generic(src: SnookerBallPosition, dst: BallPosition, observation_idx: int) -> None:
    dst.track_id = _track_for_snooker_label(src.label, src.object_id)
    dst.observation_idx = int(observation_idx)
    dst.frame_idx = int(src.frame_idx)
    dst.frame_number = int(src.frame_number)
    dst.timestamp_ns = int(src.timestamp_ns)
    dst.u_img = float(src.u_img)
    dst.v_img = float(src.v_img)
    dst.area_px = int(src.area_px)
    dst.confidence = float(src.confidence)
    dst.interpolated = False
    dst.has_table_position = bool(src.has_table_position)
    _clear_repeated(dst.cam_xyz_mm)
    dst.cam_xyz_mm.extend(float(v) for v in src.cam_xyz_mm)
    _clear_repeated(dst.table_xyz_mm)
    dst.table_xyz_mm.extend(float(v) for v in src.table_xyz_mm)
    dst.inside_table = bool(src.inside_table)


def _matrix4(values: Iterable[float]) -> np.ndarray | None:
    vals = list(values)
    if len(vals) != 16:
        return None
    return np.array(vals, dtype=np.float64).reshape(4, 4)


def _matrix3(values: Iterable[float]) -> np.ndarray | None:
    vals = list(values)
    if len(vals) != 9:
        return None
    return np.array(vals, dtype=np.float64).reshape(3, 3)


def _frame_transform(record: PongtownResponse) -> np.ndarray | None:
    mat = _matrix4(record.frame_output.T_table_to_camera)
    if mat is not None:
        return mat
    mat = _matrix4(record.table_pose.T_table_to_camera)
    if mat is not None:
        return mat
    if record.pnp_frame_debug:
        return _matrix4(record.pnp_frame_debug[0].pnp_T_table_to_camera)
    return None


def _frame_intrinsics(record: PongtownResponse) -> np.ndarray | None:
    if record.pnp_frame_debug:
        dbg = record.pnp_frame_debug[0]
        mat = _matrix3(dbg.camera_matrix)
        if mat is not None:
            return mat
        intr = dbg.camera_intrinsics
        if intr.fx > 0 and intr.fy > 0:
            return np.array(
                [[intr.fx, 0.0, intr.cx], [0.0, intr.fy, intr.cy], [0.0, 0.0, 1.0]],
                dtype=np.float64,
            )
    return None


class SnookerPongtownEditor:
    """Mutable editor facade over a length-delimited PongtownResponse stream."""

    def __init__(
        self,
        records: list[PongtownResponse],
        *,
        path: Path | None = None,
        match_radius_mm: float = DEFAULT_MATCH_RADIUS_MM,
    ) -> None:
        if len(records) < 2:
            raise ValueError("Pongtown file must contain per-frame records plus a summary record")
        self.path = path
        self.records = records
        self.frames = records[:-1]
        self.summary = records[-1]
        self.match_radius_mm = float(match_radius_mm)
        self.table_width_mm = float(self.summary.table_width_mm or DEFAULT_TABLE_WIDTH_MM)
        self.table_height_mm = float(self.summary.table_height_mm or DEFAULT_TABLE_HEIGHT_MM)
        self.dirty = False
        self._hydrate_frames_from_snooker_tracking()
        self._hydrate_frames_from_summary()

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def frame_meta(self, frame_index: int) -> tuple[int, int, int]:
        record = self.frames[frame_index]
        idx = _frame_idx(record, frame_index)
        return idx, _frame_number(record, idx), _timestamp_ns(record)

    def balls(self, frame_index: int):
        return self.frames[frame_index].ball_positions

    def first_frame_with_balls(self) -> int:
        for idx, record in enumerate(self.frames):
            if record.ball_positions:
                return idx
        return 0

    def total_ball_positions(self) -> int:
        return sum(len(record.ball_positions) for record in self.frames)

    def ball_xy(self, frame_index: int, ball_index: int) -> tuple[float, float] | None:
        return _table_xy(self.frames[frame_index].ball_positions[ball_index])

    def nearest_ball(
        self,
        frame_index: int,
        x_mm: float,
        y_mm: float,
        *,
        radius_mm: float | None = None,
        track_id: int | None = None,
    ) -> tuple[int, float] | None:
        radius = self.match_radius_mm if radius_mm is None else float(radius_mm)
        best: tuple[int, float] | None = None
        for idx, ball in enumerate(self.frames[frame_index].ball_positions):
            if track_id is not None and int(ball.track_id) != int(track_id):
                continue
            xy = _table_xy(ball)
            if xy is None:
                continue
            dist = float(np.hypot(xy[0] - x_mm, xy[1] - y_mm))
            if dist <= radius and (best is None or dist < best[1]):
                best = (idx, dist)
        return best

    def add_ball(self, frame_index: int, track_id: int, x_mm: float, y_mm: float) -> int:
        record = self.frames[frame_index]
        ball = record.ball_positions.add()
        ball.track_id = int(track_id)
        ball.area_px = 0
        ball.confidence = 1.0
        ball.interpolated = True
        idx = len(record.ball_positions) - 1
        self.set_ball_position(frame_index, idx, x_mm, y_mm, interpolated=True)
        self.dirty = True
        return idx

    def ensure_ball_positions_from_image_observations(
        self,
        observations_by_frame_number: dict[int, list[BallObservationSeed]],
        *,
        replace_existing: bool = False,
    ) -> int:
        """Populate per-frame BallPosition records from image-space detections.

        Returns the number of added positions. Existing per-frame positions are
        preserved by default; set replace_existing to rebuild from detections.
        """
        added = 0
        for fi, record in enumerate(self.frames):
            _frame_idx, frame_number, _timestamp_ns = self.frame_meta(fi)
            seeds = observations_by_frame_number.get(int(frame_number), [])
            if not seeds:
                continue
            if replace_existing:
                record.ClearField("ball_positions")
            elif record.ball_positions:
                continue

            for seed in seeds:
                ball = record.ball_positions.add()
                ball.track_id = int(seed.track_id)
                ball.u_img = float(seed.u_img)
                ball.v_img = float(seed.v_img)
                ball.area_px = int(seed.area_px)
                ball.confidence = float(seed.confidence)
                ball.interpolated = False
                self._fill_ball_frame_metadata(fi, ball)

                table_xyz, cam_xyz = self._image_to_table_position(record, seed.u_img, seed.v_img)
                if table_xyz is not None and cam_xyz is not None:
                    ball.has_table_position = True
                    ball.table_xyz_mm.extend([float(v) for v in table_xyz])
                    ball.cam_xyz_mm.extend([float(v) for v in cam_xyz])
                    self._classify_ball_side(ball, float(table_xyz[0]), float(table_xyz[1]))
                added += 1

        if added:
            self.dirty = True
            self.rebuild_summary()
        return added

    def copy_ball_positions_from_previous_frames(self, frame_indices: Iterable[int]) -> int:
        """Replace each listed frame's balls with the previous frame's balls.

        The copied records keep the previous frame's ball position payloads but
        receive the destination frame metadata. Summary observation indices are
        rebuilt on save/rebuild.
        """
        changed = 0
        for fi in sorted(set(int(i) for i in frame_indices)):
            if fi <= 0 or fi >= self.frame_count:
                continue
            prev_positions = list(self.frames[fi - 1].ball_positions)
            if not prev_positions:
                continue
            curr = self.frames[fi].ball_positions
            _clear_repeated(curr)
            for src in prev_positions:
                dst = curr.add()
                dst.CopyFrom(src)
                dst.interpolated = True
                self._fill_ball_frame_metadata(fi, dst)
            changed += 1

        if changed:
            self.dirty = True
            self.rebuild_summary()
        return changed

    def set_ball_position(
        self,
        frame_index: int,
        ball_index: int,
        x_mm: float,
        y_mm: float,
        *,
        interpolated: bool = True,
    ) -> None:
        record = self.frames[frame_index]
        ball = record.ball_positions[ball_index]
        z_mm = float(ball.table_xyz_mm[2]) if len(ball.table_xyz_mm) >= 3 else 0.0

        ball.has_table_position = True
        _clear_repeated(ball.table_xyz_mm)
        ball.table_xyz_mm.extend([float(x_mm), float(y_mm), z_mm])
        if interpolated:
            ball.interpolated = True

        self._classify_ball_side(ball, float(x_mm), float(y_mm))

        self._fill_ball_frame_metadata(frame_index, ball)
        self._project_ball(record, ball)
        self.dirty = True

    def delete_future(self, frame_index: int, ball_index: int) -> int:
        matches = self.match_future(frame_index, ball_index)
        by_frame: dict[int, list[int]] = {}
        for match in matches:
            by_frame.setdefault(match.frame_index, []).append(match.ball_index)

        removed = 0
        for fi, indices in by_frame.items():
            balls = self.frames[fi].ball_positions
            for idx in sorted(indices, reverse=True):
                del balls[idx]
                removed += 1
        if removed:
            self.dirty = True
        return removed

    def change_color_future(self, frame_index: int, ball_index: int, track_id: int) -> int:
        matches = self.match_future(frame_index, ball_index)
        for match in matches:
            self.frames[match.frame_index].ball_positions[match.ball_index].track_id = int(track_id)
        if matches:
            self.dirty = True
        return len(matches)

    def move_future(self, frame_index: int, ball_index: int, x_mm: float, y_mm: float) -> int:
        matches = self.match_future(frame_index, ball_index)
        for match in matches:
            self.set_ball_position(match.frame_index, match.ball_index, x_mm, y_mm)
        return len(matches)

    def interpolate(
        self,
        selected_frame_index: int,
        selected_ball_index: int,
        start_frame_index: int,
        end_frame_index: int,
        start_xy: tuple[float, float],
        end_xy: tuple[float, float],
    ) -> int:
        if not (0 <= selected_frame_index < self.frame_count):
            raise IndexError("selected frame is outside the loaded frame range")
        selected = self.frames[selected_frame_index].ball_positions[selected_ball_index]
        track_id = int(selected.track_id)
        start = int(min(start_frame_index, end_frame_index))
        end = int(max(start_frame_index, end_frame_index))
        start = max(0, min(start, self.frame_count - 1))
        end = max(0, min(end, self.frame_count - 1))

        changed = 0
        denom = max(1, end - start)
        for fi in range(start, end + 1):
            t = (fi - start) / denom
            x_mm = (1.0 - t) * float(start_xy[0]) + t * float(end_xy[0])
            y_mm = (1.0 - t) * float(start_xy[1]) + t * float(end_xy[1])
            nearest = self.nearest_ball(fi, x_mm, y_mm, track_id=track_id)
            if nearest is None:
                idx = self.add_ball(fi, track_id, x_mm, y_mm)
            else:
                idx = nearest[0]
                self.set_ball_position(fi, idx, x_mm, y_mm, interpolated=True)
            self.frames[fi].ball_positions[idx].interpolated = True
            changed += 1
        self.dirty = True
        return changed

    def match_future(self, frame_index: int, ball_index: int) -> list[MatchResult]:
        if not (0 <= frame_index < self.frame_count):
            return []
        balls = self.frames[frame_index].ball_positions
        if not (0 <= ball_index < len(balls)):
            return []
        selected = balls[ball_index]
        selected_xy = _table_xy(selected)
        if selected_xy is None:
            return []

        track_id = int(selected.track_id)
        last_x, last_y = selected_xy
        matches = [MatchResult(frame_index, ball_index, last_x, last_y)]
        for fi in range(frame_index + 1, self.frame_count):
            nearest = self.nearest_ball(fi, last_x, last_y, track_id=track_id)
            if nearest is None:
                continue
            idx = nearest[0]
            xy = _table_xy(self.frames[fi].ball_positions[idx])
            if xy is None:
                continue
            last_x, last_y = xy
            matches.append(MatchResult(fi, idx, last_x, last_y))
        return matches

    def rebuild_summary(self) -> None:
        traj = self.summary.ball_trajectory
        traj.ClearField("positions")
        traj.ClearField("segments")
        traj.ClearField("bounces")

        all_positions: list[BallPosition] = []
        track_ids: set[int] = set()
        observed_frames = 0
        for fi, record in enumerate(self.frames):
            record.sport_mode = PongtownResponse.SNOOKER
            snooker_tracking = record.snooker_tracking
            snooker_tracking.SetInParent()
            snooker_tracking.ClearField("ball_positions")
            snooker_tracking.observed_frames = 0
            snooker_tracking.total_observations = 0

            frame_idx, frame_number, timestamp_ns = self.frame_meta(fi)
            positions = list(record.ball_positions)
            record.ClearField("ball_positions")
            for src in positions:
                dst = record.ball_positions.add()
                dst.CopyFrom(src)
                dst.frame_idx = int(frame_idx)
                dst.frame_number = int(frame_number)
                dst.timestamp_ns = int(timestamp_ns)
                dst.observation_idx = len(all_positions)
                all_positions.append(dst)
                track_ids.add(int(dst.track_id))

                snooker_dst = snooker_tracking.ball_positions.add()
                _copy_generic_to_snooker(dst, snooker_dst, object_id=int(dst.observation_idx) + 1)
            if record.ball_positions:
                observed_frames += 1

        traj.track_id = next(iter(track_ids)) if len(track_ids) == 1 else 0
        traj.observed_frames = len(all_positions)
        if all_positions:
            traj.first_frame_idx = int(all_positions[0].frame_idx)
            traj.last_frame_idx = int(all_positions[-1].frame_idx)
            traj.first_timestamp_ns = int(all_positions[0].timestamp_ns)
            traj.last_timestamp_ns = int(all_positions[-1].timestamp_ns)
        else:
            traj.first_frame_idx = 0
            traj.last_frame_idx = 0
            traj.first_timestamp_ns = 0
            traj.last_timestamp_ns = 0

        for pos in all_positions:
            copied = traj.positions.add()
            copied.CopyFrom(pos)

        self.summary.sport_mode = PongtownResponse.SNOOKER
        summary_snooker_tracking = self.summary.snooker_tracking
        summary_snooker_tracking.SetInParent()
        summary_snooker_tracking.ClearField("ball_positions")
        summary_snooker_tracking.observed_frames = int(observed_frames)
        summary_snooker_tracking.total_observations = len(all_positions)

    def save(
        self,
        output_path: Path | None = None,
        *,
        backup: bool = True,
    ) -> Path:
        if output_path is None and self.path is None:
            raise ValueError("No output path was supplied")
        out_path = Path(output_path or self.path)

        self.rebuild_summary()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if backup and self.path is not None and out_path.resolve() == self.path.resolve() and out_path.exists():
            backup_path = out_path.with_suffix(out_path.suffix + ".bak")
            shutil.copy2(out_path, backup_path)

        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_bytes(_PONG_IO.encode(self.records))
        tmp_path.replace(out_path)
        self.dirty = False
        return out_path

    def _hydrate_frames_from_snooker_tracking(self) -> None:
        observation_idx = 0
        for fi, record in enumerate(self.frames):
            if record.ball_positions or not record.snooker_tracking.ball_positions:
                observation_idx += len(record.ball_positions)
                continue

            for src in record.snooker_tracking.ball_positions:
                dst = record.ball_positions.add()
                _copy_snooker_to_generic(src, dst, observation_idx)
                observation_idx += 1
                self._fill_ball_frame_metadata(fi, dst)
                xy = _table_xy(dst)
                if xy is not None:
                    self._classify_ball_side(dst, xy[0], xy[1])

    def _hydrate_frames_from_summary(self) -> None:
        if not self.summary.ball_trajectory.positions:
            return

        by_frame_idx = {_frame_idx(record, i): i for i, record in enumerate(self.frames)}
        seen_observations: set[tuple[int, int]] = set()
        for fi, record in enumerate(self.frames):
            frame_idx = _frame_idx(record, fi)
            for pos in record.ball_positions:
                seen_observations.add((frame_idx, int(pos.observation_idx)))

        for src in self.summary.ball_trajectory.positions:
            fi = by_frame_idx.get(int(src.frame_idx))
            if fi is None:
                continue
            key = (int(src.frame_idx), int(src.observation_idx))
            if key in seen_observations:
                continue
            dst = self.frames[fi].ball_positions.add()
            dst.CopyFrom(src)
            seen_observations.add(key)

    def _fill_ball_frame_metadata(self, frame_index: int, ball: BallPosition) -> None:
        frame_idx, frame_number, timestamp_ns = self.frame_meta(frame_index)
        ball.frame_idx = int(frame_idx)
        ball.frame_number = int(frame_number)
        ball.timestamp_ns = int(timestamp_ns)

    def _project_ball(self, record: PongtownResponse, ball: BallPosition) -> None:
        if len(ball.table_xyz_mm) < 3:
            return
        T = _frame_transform(record)
        K = _frame_intrinsics(record)
        if T is None or K is None:
            return

        p_table = np.array(
            [ball.table_xyz_mm[0], ball.table_xyz_mm[1], ball.table_xyz_mm[2], 1.0],
            dtype=np.float64,
        )
        p_cam = T @ p_table
        _clear_repeated(ball.cam_xyz_mm)
        ball.cam_xyz_mm.extend([float(p_cam[0]), float(p_cam[1]), float(p_cam[2])])
        if p_cam[2] > 1e-6:
            ball.u_img = float(K[0, 0] * p_cam[0] / p_cam[2] + K[0, 2])
            ball.v_img = float(K[1, 1] * p_cam[1] / p_cam[2] + K[1, 2])

    def _image_to_table_position(
        self,
        record: PongtownResponse,
        u_img: float,
        v_img: float,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        T = _frame_transform(record)
        K = _frame_intrinsics(record)
        if T is None or K is None:
            return None, None

        try:
            d_cam = np.linalg.inv(K) @ np.array([float(u_img), float(v_img), 1.0], dtype=np.float64)
        except np.linalg.LinAlgError:
            return None, None
        norm = float(np.linalg.norm(d_cam))
        if norm <= 1e-9:
            return None, None
        d_cam /= norm

        R_t2c = T[:3, :3]
        t_t2c = T[:3, 3]
        n_cam = R_t2c[:, 2]
        denom = float(n_cam @ d_cam)
        if abs(denom) < 1e-9:
            return None, None
        s = float(n_cam @ t_t2c) / denom
        if s <= 0:
            return None, None

        p_cam = s * d_cam
        p_table_h = np.linalg.inv(T) @ np.array([p_cam[0], p_cam[1], p_cam[2], 1.0], dtype=np.float64)
        return p_table_h[:3], p_cam

    def _classify_ball_side(self, ball: BallPosition, x_mm: float, y_mm: float) -> None:
        inside = (
            abs(float(x_mm)) <= self.table_width_mm / 2.0 + 50.0
            and abs(float(y_mm)) <= self.table_height_mm / 2.0 + 50.0
        )
        ball.inside_table = bool(inside)
        if not inside:
            ball.side = PongtownResponse.OFF_TABLE
        elif abs(float(x_mm)) <= 1e-6:
            ball.side = PongtownResponse.NET
        elif float(x_mm) > 0:
            ball.side = PongtownResponse.NEAR
        else:
            ball.side = PongtownResponse.FAR


def load_pongtown(path: Path, *, match_radius_mm: float = DEFAULT_MATCH_RADIUS_MM) -> SnookerPongtownEditor:
    records = _PONG_IO.read_file(Path(path))
    return SnookerPongtownEditor(records, path=Path(path), match_radius_mm=match_radius_mm)
