#!/usr/bin/env python3
"""
MCP server exposing scene analysis tools for the GenSpark pipeline.

Run (from server/):
    uv run python genspark/server.py                                     # stdio, no recording context
    uv run python genspark/server.py ../recordings/NAME.vis.pb           # with recording for get_motioncap_tracks
    fastmcp dev genspark/server.py -- ../recordings/NAME.vis.pb          # interactive inspector
"""

import enum
import json
import math
import statistics
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

# ── Path setup ────────────────────────────────────────────────────────────────
_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))  # needed for primitives_pb2 absolute imports
sys.path.insert(0, str(_server_root))

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from proto import idoslam_pb2
from proto import motioncap_pb2
from proto import perceiver_pb2
from proto import pongtown_pb2
from proto import segmentation_pb2
from streamlog.protoio import ProtoIO

mcp = FastMCP(name="genspark")

_idoslam_io = ProtoIO(idoslam_pb2.IdoSlamResponse)
_idoslam_io.FRAME_SIZE_LIMIT = 512 * 1024 * 1024
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)
_pong_io = ProtoIO(pongtown_pb2.PongtownResponse)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)
_vis_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

# ── Recording context (set via CLI arg on startup) ────────────────────────────

_recording_path: Path | None = None


def set_recording(path: Path) -> None:
    global _recording_path
    _recording_path = path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _require_recording() -> Path:
    if _recording_path is None:
        raise ToolError(
            "No recording context loaded. Start the server with a recording path: "
            "uv run python genspark/server.py ../recordings/NAME.vis.pb"
        )
    return _recording_path


def _recording_base() -> str:
    rec_path = _require_recording()
    name = rec_path.name
    return name[: -len(".vis.pb")] if name.endswith(".vis.pb") else rec_path.stem


def _analysis_path(suffix: str, aliases: tuple[str, ...] = ()) -> Path:
    rec_path = _require_recording()
    base = _recording_base()
    candidates = [rec_path.parent / f"{base}.{suffix}"]
    candidates.extend(rec_path.parent / f"{base}.{alias}" for alias in aliases)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _missing(analysis: str, path: Path) -> str:
    return _json({
        "available": False,
        "analysis": analysis,
        "error": f"No {analysis} output found at {path.name}. Run {analysis} first.",
    })


def _has_frame_id(message: Any) -> bool:
    try:
        return message.HasField("frame_identifier")
    except Exception:
        return False


def _frame_idx_time_index(records: list[Any]) -> dict[int, float]:
    frame_records = [record for record in records if _has_frame_id(record)]
    if not frame_records:
        return {}
    t0_ns = int(frame_records[0].frame_identifier.timestamp_ns)
    return {
        idx: (int(record.frame_identifier.timestamp_ns) - t0_ns) / 1e9
        for idx, record in enumerate(frame_records)
    }


def _nearest_by_time(items: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    if not items:
        return None
    return min(items, key=lambda item: abs(float(item.get("time_s", 0.0)) - t))


def _filter_time(items: list[dict[str, Any]], from_time: float | None, to_time: float | None) -> list[dict[str, Any]]:
    return [
        item for item in items
        if (from_time is None or float(item.get("time_s", 0.0)) >= from_time)
        and (to_time is None or float(item.get("time_s", 0.0)) <= to_time)
    ]


def _decode_mask_stats(mask_data: bytes) -> dict[str, Any]:
    if len(mask_data) < 8:
        raise ValueError("Compressed mask payload too short")

    height, width = struct.unpack("<II", mask_data[:8])
    packed = zlib.decompress(mask_data[8:])
    total = int(height) * int(width)
    count = 0
    sum_x = 0
    sum_y = 0
    min_x = int(width)
    min_y = int(height)
    max_x = -1
    max_y = -1

    for i in range(total):
        byte = packed[i >> 3]
        bit = 7 - (i & 7)
        if ((byte >> bit) & 1) == 0:
            continue
        y, x = divmod(i, int(width))
        count += 1
        sum_x += x
        sum_y += y
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

    if count == 0:
        return {
            "width": int(width),
            "height": int(height),
            "pixel_count": 0,
            "centroid": None,
            "bbox": None,
        }

    return {
        "width": int(width),
        "height": int(height),
        "pixel_count": count,
        "centroid": {"x": sum_x / count, "y": sum_y / count},
        "bbox": {"x0": min_x, "y0": min_y, "x1": max_x, "y1": max_y},
    }


def _load_segmentation_observations() -> tuple[Path, list[dict[str, Any]]]:
    seg_path = _analysis_path("segmentation.pb", aliases=("seg.pb",))
    if not seg_path.exists():
        return seg_path, []

    records = _seg_io.read_file(seg_path)
    if not records:
        return seg_path, []

    t0_ns = int(records[0].frame_identifier.timestamp_ns)
    observations: list[dict[str, Any]] = []
    for record in records:
        time_s = (int(record.frame_identifier.timestamp_ns) - t0_ns) / 1e9
        for mask in record.masks:
            stats: dict[str, Any]
            try:
                stats = _decode_mask_stats(mask.mask_data)
            except Exception:
                stats = {
                    "width": None,
                    "height": None,
                    "pixel_count": int(mask.pixel_count),
                    "centroid": None,
                    "bbox": None,
                }
            observations.append({
                "object_id": int(mask.object_id),
                "label": mask.label or "",
                "confidence": float(mask.confidence),
                "frame_number": int(record.frame_identifier.frame_number),
                "timestamp_ns": int(record.frame_identifier.timestamp_ns),
                "time_s": time_s,
                "pixel_count": int(mask.pixel_count) or stats.get("pixel_count", 0),
                "centroid": stats.get("centroid"),
                "bbox": stats.get("bbox"),
                "mask_width": stats.get("width"),
                "mask_height": stats.get("height"),
            })
    return seg_path, observations


def _resolve_object(observations: list[dict[str, Any]], value: str | int) -> int | None:
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    lowered = text.casefold()
    candidates = [
        obs["object_id"] for obs in observations
        if str(obs.get("label", "")).casefold() == lowered
    ]
    if candidates:
        return max(set(candidates), key=candidates.count)
    return None


def _segmentation_tracks() -> dict[int, dict[str, Any]]:
    _, observations = _load_segmentation_observations()
    tracks: dict[int, dict[str, Any]] = {}
    for obs in observations:
        centroid = obs.get("centroid")
        if centroid is None:
            continue
        track = tracks.setdefault(
            obs["object_id"],
            {
                "track_id": obs["object_id"],
                "object_id": obs["object_id"],
                "source": "segmentation",
                "label": obs.get("label", ""),
                "positions": [],
            },
        )
        if not track["label"] and obs.get("label"):
            track["label"] = obs["label"]
        track["positions"].append({
            "frame_idx": int(obs["frame_number"]),
            "frame_number": int(obs["frame_number"]),
            "timestamp_ns": int(obs["timestamp_ns"]),
            "time_s": float(obs["time_s"]),
            "cx": float(centroid["x"]),
            "cy": float(centroid["y"]),
            "area": int(obs["pixel_count"]),
            "interpolated": False,
        })
    return tracks


def _load_motion_tracks(segmentation: bool) -> tuple[Path, dict[int, dict[str, Any]]]:
    if segmentation:
        seg_path, _ = _load_segmentation_observations()
        return seg_path, _segmentation_tracks()

    motion_path = _analysis_path("motioncap.pb", aliases=("motion.pb",))
    if not motion_path.exists():
        return motion_path, {}

    records = _motion_io.read_file(motion_path)
    frame_records = [record for record in records if not record.tracks]
    summary_records = [record for record in records if record.tracks]
    if not frame_records or not summary_records:
        return motion_path, {}

    idx_to_time = _frame_idx_time_index(frame_records)
    idx_to_meta = {
        idx: {
            "frame_number": int(record.frame_identifier.frame_number),
            "timestamp_ns": int(record.frame_identifier.timestamp_ns),
        }
        for idx, record in enumerate(frame_records)
    }
    tracks: dict[int, dict[str, Any]] = {}
    for track in summary_records[0].tracks:
        points = []
        for point in track.positions:
            frame_idx = int(point.frame_idx)
            if frame_idx not in idx_to_time:
                continue
            meta = idx_to_meta.get(frame_idx, {})
            points.append({
                "frame_idx": frame_idx,
                "frame_number": meta.get("frame_number"),
                "timestamp_ns": meta.get("timestamp_ns"),
                "time_s": idx_to_time[frame_idx],
                "cx": float(point.cx),
                "cy": float(point.cy),
                "area": int(point.area),
                "interpolated": bool(point.interpolated),
            })
        tracks[int(track.track_id)] = {
            "track_id": int(track.track_id),
            "source": "raft_motioncap",
            "detected_frames": int(track.detected_frames),
            "total_positions": int(track.total_positions),
            "presence_fraction": float(track.presence_fraction),
            "positions": points,
        }
    return motion_path, tracks


def _track_summary(track: dict[str, Any], from_time: float | None = None, to_time: float | None = None) -> dict[str, Any]:
    positions = _filter_time(track.get("positions", []), from_time, to_time)
    if not positions:
        return {
            "track_id": track.get("track_id"),
            "source": track.get("source"),
            "label": track.get("label"),
            "positions": 0,
        }

    first = positions[0]
    last = positions[-1]
    displacement = math.hypot(float(last["cx"]) - float(first["cx"]), float(last["cy"]) - float(first["cy"]))
    distance = 0.0
    max_speed = 0.0
    for prev, cur in zip(positions, positions[1:]):
        dt = float(cur["time_s"]) - float(prev["time_s"])
        step = math.hypot(float(cur["cx"]) - float(prev["cx"]), float(cur["cy"]) - float(prev["cy"]))
        distance += step
        if dt > 0:
            max_speed = max(max_speed, step / dt)
    duration = max(float(last["time_s"]) - float(first["time_s"]), 0.0)
    return {
        "track_id": track.get("track_id"),
        "object_id": track.get("object_id"),
        "source": track.get("source"),
        "label": track.get("label"),
        "positions": len(positions),
        "first_time": float(first["time_s"]),
        "last_time": float(last["time_s"]),
        "duration_s": duration,
        "start": {"cx": first["cx"], "cy": first["cy"]},
        "end": {"cx": last["cx"], "cy": last["cy"]},
        "displacement_px": displacement,
        "path_distance_px": distance,
        "avg_speed_px_s": distance / duration if duration > 0 else 0.0,
        "max_speed_px_s": max_speed,
    }


def _load_pong_records() -> tuple[Path, list[Any], Any | None]:
    pong_path = _analysis_path("pongtown.pb")
    if not pong_path.exists():
        return pong_path, [], None
    records = _pong_io.read_file(pong_path)
    summary = next((record for record in reversed(records) if not _has_frame_id(record)), None)
    frames = [record for record in records if _has_frame_id(record)]
    return pong_path, frames, summary


def _pong_side(value: int) -> str:
    try:
        name = pongtown_pb2.PongtownResponse.TableSide.Name(value)
    except Exception:
        return "unknown"
    return name.lower().replace("side_", "")


def _pov_side(side: str, responder_pov: str) -> str:
    pov = responder_pov.strip().lower()
    if side == "net":
        return "net"
    if side in ("off_table", "unknown"):
        return side
    if pov not in ("near", "far"):
        pov = "near"
    if side == pov:
        return "self"
    return "opponent"


def _table_xyz(values: Any, responder_pov: str = "near") -> dict[str, Any] | None:
    coords = [float(v) for v in values]
    if len(coords) < 2:
        return None
    x, y = coords[0], coords[1]
    z = coords[2] if len(coords) >= 3 else 0.0
    if responder_pov.strip().lower() == "far":
        x, y = -x, -y
    return {
        "x_mm": x,
        "y_mm": y,
        "z_mm": z,
        "raw_x_mm": coords[0],
        "raw_y_mm": coords[1],
        "raw_z_mm": z,
    }


def _load_slam_response() -> tuple[Path, idoslam_pb2.IdoSlamResponse | None]:
    slam_path = _analysis_path("idoslam.pb")
    if not slam_path.exists():
        return slam_path, None
    records = _idoslam_io.read_file(slam_path)
    if not records:
        return slam_path, None
    return slam_path, records[-1]


def _slam_pose_items(response: idoslam_pb2.IdoSlamResponse) -> list[dict[str, Any]]:
    poses = response.refined_frame_poses or response.frame_poses
    if not poses:
        return []
    t0_ns = int(poses[0].frame_id.timestamp_ns)
    items: list[dict[str, Any]] = []
    for pose in poses:
        position = pose.world_pose.position
        euler = pose.euler_degrees
        items.append({
            "frame_idx": int(pose.frame_index),
            "frame_number": int(pose.frame_id.frame_number),
            "timestamp_ns": int(pose.frame_id.timestamp_ns),
            "time_s": (int(pose.frame_id.timestamp_ns) - t0_ns) / 1e9,
            "x": float(position.x),
            "y": float(position.y),
            "z": float(position.z),
            "pitch_deg": float(euler.x),
            "roll_deg": float(euler.y),
            "yaw_deg": float(euler.z),
            "pose_source": "refined_frame_poses" if response.refined_frame_poses else "frame_poses",
        })
    return items


def _canonical_track_items(response: idoslam_pb2.IdoSlamResponse) -> list[dict[str, Any]]:
    tracks = list(response.canonical_frame_tracks)
    if not tracks:
        return []
    t0_ns = int(tracks[0].timestamp_ns)
    return [
        {
            "frame_idx": int(track.frame_index),
            "frame_number": int(track.frame_number),
            "timestamp_ns": int(track.timestamp_ns),
            "time_s": (int(track.timestamp_ns) - t0_ns) / 1e9,
            "lap_id": int(track.lap_id),
            "is_partial_lap": bool(track.is_partial_lap),
            "progress_m": float(track.progress_m),
            "progress_fraction": float(track.progress_fraction),
            "lateral_offset_m": float(track.lateral_offset_m),
            "width_m": float(track.width_m),
            "half_width_m": float(track.half_width_m),
            "has_image_lateral_m": bool(track.has_image_lateral_m),
            "image_lateral_m": float(track.image_lateral_m),
            "trajectory_lateral_m": float(track.trajectory_lateral_m),
            "canonical_x": float(track.canonical_x),
            "canonical_y": float(track.canonical_y),
        }
        for track in tracks
    ]


# ── Scene types ───────────────────────────────────────────────────────────────

class SceneType(str, enum.Enum):
    """Recognized scene categories for BayesMech Vision recordings."""
    SPORT_KARTING = "sport-karting"
    SPORT_RUNNING = "sport-running"
    SPORT_CHESS = "sport-chess"
    EXPERIMENT_PENDULUM = "experiment-pendulum"


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def scene_context(type: SceneType) -> str:
    """
    Tag this recording with a scene type classification and receive scene-specific
    analysis instructions. Always call this once after identifying the scene type.

    Args:
        type: The scene category that best describes this recording.

    Returns:
        Scene-specific follow-up instructions to carry out.
    """
    if type == SceneType.EXPERIMENT_PENDULUM:
        return (
            "Scene classified as experiment-pendulum. Motion tracking data is available for "
            "this recording. Please do the following:\n"
            "1. Call get_motioncap_tracks(0, <total_recording_duration>, segmentation=false) "
            "to retrieve RAFT/motion tracks. If semantic object tracks are needed, call it again "
            "with segmentation=true.\n"
            "2. Identify which track(s) represent the pendulum — look for oscillatory (back-and-forth) "
            "motion in the cx (horizontal) or cy (vertical) coordinate. Multiple tracks may represent "
            "the same pendulum if tracking was lost and later recovered.\n"
            "3. For each pendulum track, determine:\n"
            "   - Oscillation period: time between successive extrema of the same type (peak-to-peak or trough-to-trough)\n"
            "   - Exact timestamps when the pendulum is at an extremum (local maximum or minimum in cx or cy)\n"
            "   - Amplitude at each extremum (distance from the equilibrium position)\n"
            "   - Whether the amplitude is decaying (damped oscillation) and estimate the decay rate\n"
            "4. Call scene_emphasis() to mark a 2–5s window around each identified extremum.\n"
            "5. Provide a final numerical summary: period(s), extremum times, amplitudes, and decay rate."
        )
    return f"Scene classified as {type.value}. No additional analysis required."


@mcp.tool()
def scene_emphasis(start_time: float, end_time: float, description: str) -> str:
    """
    Mark a temporal highlight segment within the recording.

    Each segment should be between 2 and 10 seconds long. Provide a description
    explaining what is happening and why this moment is notable.

    Args:
        start_time:  Start of the highlight in seconds (>= 0.0).
        end_time:    End of the highlight in seconds (> start_time).
        description: What is happening in this segment and why it is notable.
    """
    duration = end_time - start_time
    if duration > 10.0:
        raise ToolError(
            f"Highlight segment must be 10.0 seconds or shorter, "
            f"but got {duration:.2f}s ({start_time}s – {end_time}s)."
        )
    return f"Marked emphasis [{start_time}s – {end_time}s]: {description}"


@mcp.tool()
def list_available_analyses() -> str:
    """
    List which analyzer outputs are available for the current recording.

    Use this before calling analysis-specific tools so you know whether
    segmentation, motioncap, pongtown, or SLAM data exists.
    """
    _require_recording()
    checks = {
        "segmentation": _analysis_path("segmentation.pb", aliases=("seg.pb",)),
        "motioncap": _analysis_path("motioncap.pb", aliases=("motion.pb",)),
        "pongtown": _analysis_path("pongtown.pb"),
        "slam": _analysis_path("idoslam.pb"),
    }
    return _json({
        "recording": str(_require_recording()),
        "analyses": {
            name: {"available": path.exists(), "path": str(path)}
            for name, path in checks.items()
        },
    })


@mcp.tool()
def get_recording_metadata() -> str:
    """
    Return basic recording timing and image metadata.

    Use this when you need the total duration, frame count, FPS estimate, or
    frame dimensions before selecting time windows for other tools.
    """
    rec_path = _require_recording()
    if not rec_path.exists():
        return _json({"available": False, "error": f"Recording not found: {rec_path}"})
    frames = _vis_io.read_file(rec_path)
    if not frames:
        return _json({"available": False, "error": "Recording has no frames."})
    first = frames[0]
    last = frames[-1]
    first_ts = int(first.frame_identifier.timestamp_ns)
    last_ts = int(last.frame_identifier.timestamp_ns)
    duration_s = max(0.0, (last_ts - first_ts) / 1e9)
    fps = ((len(frames) - 1) / duration_s) if duration_s > 0 and len(frames) > 1 else 0.0
    rgb = first.rgb_frame
    return _json({
        "available": True,
        "recording": str(rec_path),
        "frame_count": len(frames),
        "first_timestamp_ns": first_ts,
        "last_timestamp_ns": last_ts,
        "duration_s": duration_s,
        "fps_estimate": fps,
        "first_frame_number": int(first.frame_identifier.frame_number),
        "last_frame_number": int(last.frame_identifier.frame_number),
        "image_width": int(rgb.width),
        "image_height": int(rgb.height),
    })


@mcp.tool()
def segmentation_list_identified_objects() -> str:
    """
    List persistent segmented objects, labels, time span, and confidence.

    Use this when the question asks which objects are present or when you need
    object IDs for relative-position or object-track queries.
    """
    seg_path, observations = _load_segmentation_observations()
    if not observations:
        return _missing("segmentation", seg_path)

    grouped: dict[int, list[dict[str, Any]]] = {}
    for obs in observations:
        grouped.setdefault(obs["object_id"], []).append(obs)

    objects = []
    for object_id, obs_list in sorted(grouped.items()):
        labels = [str(obs.get("label", "")) for obs in obs_list if obs.get("label")]
        label = max(set(labels), key=labels.count) if labels else ""
        objects.append({
            "object_id": object_id,
            "label": label,
            "first_time": min(float(obs["time_s"]) for obs in obs_list),
            "last_time": max(float(obs["time_s"]) for obs in obs_list),
            "observed_frames": len({int(obs["frame_number"]) for obs in obs_list}),
            "avg_confidence": statistics.fmean(float(obs["confidence"]) for obs in obs_list),
            "avg_pixel_count": statistics.fmean(int(obs["pixel_count"]) for obs in obs_list),
        })

    return _json({"available": True, "objects": objects})


@mcp.tool()
def segmentation_get_objects_at_time(t: float) -> str:
    """
    Return segmented objects in the frame nearest time t.

    Use this for a spatial snapshot of semantic objects visible at a specific
    video moment.
    """
    seg_path, observations = _load_segmentation_observations()
    if not observations:
        return _missing("segmentation", seg_path)
    nearest_time = min({float(obs["time_s"]) for obs in observations}, key=lambda value: abs(value - t))
    frame_obs = [obs for obs in observations if abs(float(obs["time_s"]) - nearest_time) < 1e-9]
    return _json({
        "available": True,
        "requested_time_s": t,
        "nearest_time_s": nearest_time,
        "objects": frame_obs,
    })


@mcp.tool()
def segmentation_get_object_track(
    object_id: int,
    from_time: float | None = None,
    to_time: float | None = None,
    limit: int = 200,
) -> str:
    """
    Return a segmentation centroid track for one object.

    Use this when a semantic object's image-space path is needed. Coordinates
    are pixel centroids in the RGB frame.
    """
    tracks = _segmentation_tracks()
    track = tracks.get(int(object_id))
    if track is None:
        return _json({"available": False, "error": f"No segmentation track found for object_id={object_id}"})
    positions = _filter_time(track["positions"], from_time, to_time)
    if limit > 0:
        positions = positions[:limit]
    return _json({
        "available": True,
        "object_id": object_id,
        "label": track.get("label", ""),
        "positions": positions,
        "returned_positions": len(positions),
        "truncated": limit > 0 and len(_filter_time(track["positions"], from_time, to_time)) > len(positions),
    })


@mcp.tool()
def segmentation_get_relative_position(object_1: str, object_2: str, t: float | None = None) -> str:
    """
    Return relative image-space position between two segmented objects.

    object_1 and object_2 can be object IDs or exact labels. If t is omitted,
    the first frame where both objects have centroids is used.
    """
    _, observations = _load_segmentation_observations()
    if not observations:
        return _json({"available": False, "error": "No segmentation observations available."})

    id_1 = _resolve_object(observations, object_1)
    id_2 = _resolve_object(observations, object_2)
    if id_1 is None or id_2 is None:
        return _json({
            "available": False,
            "error": "Could not resolve one or both objects.",
            "object_1": object_1,
            "object_2": object_2,
        })

    obs_1 = [obs for obs in observations if obs["object_id"] == id_1 and obs.get("centroid")]
    obs_2 = [obs for obs in observations if obs["object_id"] == id_2 and obs.get("centroid")]
    if not obs_1 or not obs_2:
        return _json({"available": False, "error": "Both objects need centroid-bearing masks."})

    if t is not None:
        a = _nearest_by_time(obs_1, t)
        b = _nearest_by_time(obs_2, t)
    else:
        by_frame_2 = {int(obs["frame_number"]): obs for obs in obs_2}
        a = next((obs for obs in obs_1 if int(obs["frame_number"]) in by_frame_2), None)
        b = by_frame_2.get(int(a["frame_number"])) if a else None
    if a is None or b is None:
        return _json({"available": False, "error": "No comparable observations found."})

    c1 = a["centroid"]
    c2 = b["centroid"]
    dx = float(c2["x"]) - float(c1["x"])
    dy = float(c2["y"]) - float(c1["y"])
    relation_x = "right_of" if dx > 0 else "left_of" if dx < 0 else "same_x_as"
    relation_y = "below" if dy > 0 else "above" if dy < 0 else "same_y_as"
    return _json({
        "available": True,
        "object_1": {"query": object_1, "object_id": id_1, "centroid": c1},
        "object_2": {"query": object_2, "object_id": id_2, "centroid": c2},
        "time_s": a["time_s"],
        "frame_number": a["frame_number"],
        "dx_px": dx,
        "dy_px": dy,
        "distance_px": math.hypot(dx, dy),
        "relation": {
            "object_2_is_horizontally": relation_x,
            "object_2_is_vertically": relation_y,
        },
    })


@mcp.tool()
def motioncap_list_tracks(segmentation: bool = False) -> str:
    """
    List available motion tracks.

    Args:
        segmentation: false = RAFT/motioncap tracks; true = tracks derived from
            segmentation mask centroids.
    """
    path, tracks = _load_motion_tracks(segmentation)
    if not tracks:
        return _json({
            "available": False,
            "source": "segmentation" if segmentation else "raft_motioncap",
            "error": f"No tracks found in {path.name}.",
        })
    return _json({
        "available": True,
        "source": "segmentation" if segmentation else "raft_motioncap",
        "tracks": [
            {
                **_track_summary(track),
                "presence_fraction": track.get("presence_fraction"),
                "detected_frames": track.get("detected_frames"),
            }
            for track in tracks.values()
        ],
    })


@mcp.tool()
def motioncap_get_track_summary(
    track_id: int,
    segmentation: bool = False,
    from_time: float | None = None,
    to_time: float | None = None,
) -> str:
    """
    Summarize one motion track's displacement, distance, and speed.
    """
    _, tracks = _load_motion_tracks(segmentation)
    track = tracks.get(int(track_id))
    if track is None:
        return _json({"available": False, "error": f"No track_id={track_id} for segmentation={segmentation}."})
    return _json({"available": True, "summary": _track_summary(track, from_time, to_time)})


@mcp.tool()
def motioncap_get_moving_objects(
    from_time: float,
    to_time: float,
    min_distance_px: float = 20.0,
    segmentation: bool = False,
) -> str:
    """
    Return tracks that move at least min_distance_px in the time window.
    """
    _, tracks = _load_motion_tracks(segmentation)
    moving = []
    for track in tracks.values():
        summary = _track_summary(track, from_time, to_time)
        if float(summary.get("path_distance_px", 0.0)) >= min_distance_px:
            moving.append(summary)
    return _json({
        "available": bool(tracks),
        "source": "segmentation" if segmentation else "raft_motioncap",
        "from_time": from_time,
        "to_time": to_time,
        "min_distance_px": min_distance_px,
        "moving_tracks": moving,
    })


@mcp.tool()
def motioncap_find_extrema(
    track_id: int,
    axis: str = "x",
    from_time: float | None = None,
    to_time: float | None = None,
    segmentation: bool = False,
) -> str:
    """
    Find local extrema in a track's x or y centroid coordinate.

    Use this for pendulums or other oscillatory motion.
    """
    _, tracks = _load_motion_tracks(segmentation)
    track = tracks.get(int(track_id))
    if track is None:
        return _json({"available": False, "error": f"No track_id={track_id} for segmentation={segmentation}."})
    coord = "cy" if axis.lower().startswith("y") else "cx"
    positions = _filter_time(track["positions"], from_time, to_time)
    extrema = []
    for prev, cur, nxt in zip(positions, positions[1:], positions[2:]):
        y0 = float(prev[coord])
        y1 = float(cur[coord])
        y2 = float(nxt[coord])
        if y1 >= y0 and y1 >= y2:
            kind = "max"
        elif y1 <= y0 and y1 <= y2:
            kind = "min"
        else:
            continue
        extrema.append({
            "kind": kind,
            "time_s": cur["time_s"],
            "frame_number": cur.get("frame_number"),
            "value_px": y1,
            "cx": cur["cx"],
            "cy": cur["cy"],
        })
    return _json({
        "available": True,
        "track_id": track_id,
        "axis": "y" if coord == "cy" else "x",
        "extrema": extrema,
    })


@mcp.tool()
def motioncap_estimate_period(
    track_id: int,
    axis: str = "x",
    from_time: float | None = None,
    to_time: float | None = None,
    segmentation: bool = False,
) -> str:
    """
    Estimate oscillation period from same-kind extrema in one track.
    """
    extrema_payload = json.loads(motioncap_find_extrema(track_id, axis, from_time, to_time, segmentation))
    extrema = extrema_payload.get("extrema", [])
    periods = []
    for kind in ("max", "min"):
        times = [float(item["time_s"]) for item in extrema if item["kind"] == kind]
        periods.extend(b - a for a, b in zip(times, times[1:]) if b > a)
    return _json({
        "available": extrema_payload.get("available", False),
        "track_id": track_id,
        "axis": axis,
        "period_samples_s": periods,
        "estimated_period_s": statistics.fmean(periods) if periods else None,
        "sample_count": len(periods),
    })


@mcp.tool()
def get_motioncap_tracks(start_time: float, end_time: float, segmentation: bool = False, limit_per_track: int = 200) -> str:
    """
    Return motion tracking data for the time window [start_time, end_time] (in seconds).

    Track positions are (cx, cy) pixel coordinates in the video frame (origin top-left).
    Use this to analyze object motion: oscillation, speed, trajectory, etc. Set
    segmentation=true to use segmentation-mask centroid tracks instead of
    RAFT/motioncap tracks.

    Args:
        start_time: Start of the window in seconds (>= 0.0).
        end_time:   End of the window in seconds (> start_time).
        segmentation: false = RAFT/motioncap tracks; true = segmentation tracks.
        limit_per_track: Maximum returned positions per track.
    """
    path, tracks = _load_motion_tracks(segmentation)
    if not tracks:
        return _json({
            "available": False,
            "source": "segmentation" if segmentation else "raft_motioncap",
            "error": f"No tracks found in {path.name}.",
        })
    result = []
    for track in tracks.values():
        positions = _filter_time(track["positions"], start_time, end_time)
        result.append({
            "track_id": track["track_id"],
            "object_id": track.get("object_id"),
            "label": track.get("label"),
            "source": track["source"],
            "summary": _track_summary(track, start_time, end_time),
            "positions": positions[:limit_per_track] if limit_per_track > 0 else positions,
            "returned_positions": min(len(positions), limit_per_track) if limit_per_track > 0 else len(positions),
            "total_positions_in_window": len(positions),
            "truncated": limit_per_track > 0 and len(positions) > limit_per_track,
        })
    return _json({
        "available": True,
        "source": "segmentation" if segmentation else "raft_motioncap",
        "start_time": start_time,
        "end_time": end_time,
        "tracks": result,
    })


@mcp.tool()
def pongtown_get_table_pose_at_time(t: float) -> str:
    """
    Return ping-pong table and net pose/reprojection nearest time t.

    Use this when reasoning about table layout, ball-table projection quality, or
    whether the table was off-screen.
    """
    pong_path, frames, _ = _load_pong_records()
    if not frames:
        return _missing("pongtown", pong_path)
    t0_ns = int(frames[0].frame_identifier.timestamp_ns)
    items = []
    for record in frames:
        time_s = (int(record.frame_identifier.timestamp_ns) - t0_ns) / 1e9
        output = record.frame_output
        pose = record.table_pose
        items.append({
            "time_s": time_s,
            "frame_number": int(record.frame_identifier.frame_number),
            "timestamp_ns": int(record.frame_identifier.timestamp_ns),
            "table_pose_method": int(pose.method),
            "quad_quality": float(pose.quad_quality),
            "pnp_iou": float(pose.pnp_iou),
            "table_quad_img": [float(v) for v in output.table_quad_img or pose.quad_img],
            "net_quad_img": [float(v) for v in output.net_quad_img],
            "off_screen": bool(output.off_screen),
            "global_iou": float(output.global_iou),
            "has_pose": bool(output.has_pose),
            "has_net_pose": bool(output.has_net_pose),
        })
    nearest = _nearest_by_time(items, t)
    return _json({"available": True, "requested_time_s": t, "nearest": nearest})


@mcp.tool()
def pongtown_get_ball_trajectory(
    from_time: float | None = None,
    to_time: float | None = None,
    limit: int = 300,
    responder_pov: str = "near",
) -> str:
    """
    Return the ping-pong ball trajectory in image and table coordinates.

    responder_pov controls whether table coordinates are returned from the near
    or far player's point of view.
    """
    pong_path, _, summary = _load_pong_records()
    if summary is None or not summary.HasField("ball_trajectory"):
        return _missing("pongtown", pong_path)
    trajectory = summary.ball_trajectory
    if not trajectory.positions:
        return _json({"available": False, "error": "Pongtown ball trajectory is empty."})
    t0_ns = int(trajectory.first_timestamp_ns or trajectory.positions[0].timestamp_ns)
    positions = []
    for point in trajectory.positions:
        time_s = (int(point.timestamp_ns) - t0_ns) / 1e9
        if from_time is not None and time_s < from_time:
            continue
        if to_time is not None and time_s > to_time:
            continue
        positions.append({
            "time_s": time_s,
            "timestamp_ns": int(point.timestamp_ns),
            "frame_idx": int(point.frame_idx),
            "frame_number": int(point.frame_number),
            "u_img": float(point.u_img),
            "v_img": float(point.v_img),
            "area_px": int(point.area_px),
            "confidence": float(point.confidence),
            "interpolated": bool(point.interpolated),
            "side": _pong_side(int(point.side)),
            "inside_table": bool(point.inside_table),
            "table_position": _table_xyz(point.table_xyz_mm, responder_pov) if point.has_table_position else None,
        })
    returned = positions[:limit] if limit > 0 else positions
    return _json({
        "available": True,
        "track_id": int(trajectory.track_id),
        "from_time": from_time,
        "to_time": to_time,
        "responder_pov": responder_pov,
        "positions": returned,
        "returned_positions": len(returned),
        "total_positions_in_window": len(positions),
        "truncated": limit > 0 and len(positions) > len(returned),
    })


@mcp.tool()
def pongtown_get_table_bounces(
    from_time: float | None = None,
    to_time: float | None = None,
    responder_pov: str = "near",
) -> str:
    """
    Return detected table bounce candidates in a time window.

    side_for_responder maps NEAR/FAR table side to self/opponent from the
    requested responder_pov.
    """
    pong_path, _, summary = _load_pong_records()
    if summary is None or not summary.HasField("ball_trajectory"):
        return _missing("pongtown", pong_path)
    trajectory = summary.ball_trajectory
    if not trajectory.bounces:
        return _json({"available": True, "bounces": []})
    t0_ns = int(trajectory.first_timestamp_ns or trajectory.bounces[0].timestamp_ns)
    bounces = []
    for bounce in trajectory.bounces:
        time_s = (int(bounce.timestamp_ns) - t0_ns) / 1e9
        if from_time is not None and time_s < from_time:
            continue
        if to_time is not None and time_s > to_time:
            continue
        side = _pong_side(int(bounce.side))
        bounces.append({
            "bounce_idx": int(bounce.bounce_idx),
            "observation_idx": int(bounce.observation_idx),
            "time_s": time_s,
            "timestamp_ns": int(bounce.timestamp_ns),
            "frame_idx": int(bounce.frame_idx),
            "frame_number": int(bounce.frame_number),
            "u_img": float(bounce.u_img),
            "v_img": float(bounce.v_img),
            "prominence_px": float(bounce.prominence_px),
            "confidence": float(bounce.confidence),
            "side": side,
            "side_for_responder": _pov_side(side, responder_pov),
            "inside_table": bool(bounce.inside_table),
            "table_position": _table_xyz(bounce.table_xyz_mm, responder_pov) if bounce.has_table_position else None,
        })
    return _json({
        "available": True,
        "from_time": from_time,
        "to_time": to_time,
        "responder_pov": responder_pov,
        "bounces": bounces,
    })


@mcp.tool()
def pongtown_get_ball_speed(from_time: float, to_time: float, space: str = "table") -> str:
    """
    Return ping-pong ball speed stats over trajectory segments.

    space='table' uses table mm/s when available; space='image' uses px/s.
    """
    pong_path, _, summary = _load_pong_records()
    if summary is None or not summary.HasField("ball_trajectory"):
        return _missing("pongtown", pong_path)
    trajectory = summary.ball_trajectory
    t0_ns = int(trajectory.first_timestamp_ns or 0)
    speeds = []
    use_table = space.strip().lower() == "table"
    for segment in trajectory.segments:
        start_s = (int(segment.start_timestamp_ns) - t0_ns) / 1e9
        end_s = (int(segment.end_timestamp_ns) - t0_ns) / 1e9
        if end_s < from_time or start_s > to_time:
            continue
        speed = float(segment.table_speed_mm_s) if use_table and segment.has_table_displacement else float(segment.image_speed_px_s)
        speeds.append({
            "start_time_s": start_s,
            "end_time_s": end_s,
            "speed": speed,
            "unit": "mm/s" if use_table and segment.has_table_displacement else "px/s",
            "dt_s": float(segment.dt_s),
        })
    values = [item["speed"] for item in speeds]
    return _json({
        "available": True,
        "space": space,
        "segments": speeds,
        "avg_speed": statistics.fmean(values) if values else None,
        "max_speed": max(values) if values else None,
    })


@mcp.tool()
def slam_get_map(limit: int = 1000) -> str:
    """
    Return the SLAM camera trajectory map as world positions per frame.

    Uses refined poses when available, otherwise raw frame poses.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    items = _slam_pose_items(response)
    returned = items[:limit] if limit > 0 else items
    return _json({
        "available": True,
        "pose_count": len(items),
        "returned_count": len(returned),
        "truncated": limit > 0 and len(items) > len(returned),
        "map": returned,
    })


@mcp.tool()
def slam_get_pose_at_time(t: float) -> str:
    """
    Return camera pose nearest time t.

    Position is in SLAM world coordinates. Euler angles are degrees.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    pose = _nearest_by_time(_slam_pose_items(response), t)
    return _json({"available": pose is not None, "requested_time_s": t, "pose": pose})


@mcp.tool()
def slam_get_velocity_at_time(t: float, window_s: float = 0.5) -> str:
    """
    Estimate camera velocity around time t from neighboring SLAM poses.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    poses = _slam_pose_items(response)
    if len(poses) < 2:
        return _json({"available": False, "error": "Need at least two SLAM poses for velocity."})
    lo = t - max(window_s, 0.0) / 2
    hi = t + max(window_s, 0.0) / 2
    window = [pose for pose in poses if lo <= float(pose["time_s"]) <= hi]
    if len(window) < 2:
        nearest_idx = min(range(len(poses)), key=lambda idx: abs(float(poses[idx]["time_s"]) - t))
        start_idx = max(0, nearest_idx - 1)
        end_idx = min(len(poses) - 1, nearest_idx + 1)
        window = [poses[start_idx], poses[end_idx]]
    start = window[0]
    end = window[-1]
    dt = float(end["time_s"]) - float(start["time_s"])
    if dt <= 0:
        return _json({"available": False, "error": "Could not find a positive time interval."})
    vx = (float(end["x"]) - float(start["x"])) / dt
    vy = (float(end["y"]) - float(start["y"])) / dt
    vz = (float(end["z"]) - float(start["z"])) / dt
    return _json({
        "available": True,
        "requested_time_s": t,
        "window_s": window_s,
        "start_time_s": start["time_s"],
        "end_time_s": end["time_s"],
        "vx": vx,
        "vy": vy,
        "vz": vz,
        "speed_m_s": math.sqrt(vx * vx + vy * vy + vz * vz),
    })


@mcp.tool()
def slam_get_position_on_road(t: float) -> str:
    """
    Return lateral road position nearest time t as a 0..1 fraction from left edge.

    Uses canonical frame track data when available.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    item = _nearest_by_time(_canonical_track_items(response), t)
    if item is None or float(item["width_m"]) <= 0:
        return _json({"available": False, "error": "No canonical road position data available."})
    fraction = (float(item["lateral_offset_m"]) + float(item["half_width_m"])) / float(item["width_m"])
    return _json({
        "available": True,
        "requested_time_s": t,
        "nearest_time_s": item["time_s"],
        "frame_number": item["frame_number"],
        "fraction_from_left_edge": max(0.0, min(1.0, fraction)),
        "lateral_offset_m": item["lateral_offset_m"],
        "width_m": item["width_m"],
        "method": "canonical_frame_tracks",
    })


@mcp.tool()
def slam_get_road_width_at_time(t: float) -> str:
    """
    Return nearest road-width estimate at time t.

    Prefers triangulated estimates when available, otherwise plane estimates.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    estimates = list(response.triangulated_width_estimates or response.plane_width_estimates)
    if not estimates:
        return _json({"available": False, "error": "No road-width estimates available."})
    t0_ns = int(estimates[0].timestamp_ns)
    items = [
        {
            "frame_idx": int(est.frame_index),
            "frame_number": int(est.frame_number),
            "timestamp_ns": int(est.timestamp_ns),
            "time_s": (int(est.timestamp_ns) - t0_ns) / 1e9,
            "width_m": float(est.width_m),
            "left_offset_m": float(est.left_offset_m),
            "right_offset_m": float(est.right_offset_m),
            "bike_fraction": float(est.bike_fraction),
            "method": est.method,
        }
        for est in estimates
    ]
    return _json({
        "available": True,
        "requested_time_s": t,
        "estimate_source": "triangulated_width_estimates" if response.triangulated_width_estimates else "plane_width_estimates",
        "nearest": _nearest_by_time(items, t),
    })


@mcp.tool()
def slam_get_lap_progress(t: float) -> str:
    """
    Return canonical track/lap progress nearest time t.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    item = _nearest_by_time(_canonical_track_items(response), t)
    if item is None:
        return _json({"available": False, "error": "No canonical lap progress data available."})
    return _json({
        "available": True,
        "requested_time_s": t,
        "nearest_time_s": item["time_s"],
        "lap_id": item["lap_id"],
        "is_partial_lap": item["is_partial_lap"],
        "progress_m": item["progress_m"],
        "progress_fraction": item["progress_fraction"],
        "canonical_x": item["canonical_x"],
        "canonical_y": item["canonical_y"],
    })


@mcp.tool()
def slam_get_motion_between(from_time: float, to_time: float) -> str:
    """
    Summarize SLAM camera displacement and path distance between two times.
    """
    slam_path, response = _load_slam_response()
    if response is None:
        return _missing("slam", slam_path)
    poses = _slam_pose_items(response)
    window = [pose for pose in poses if from_time <= float(pose["time_s"]) <= to_time]
    if len(window) < 2:
        return _json({"available": False, "error": "Need at least two poses in the requested window."})
    first = window[0]
    last = window[-1]
    displacement = math.sqrt(
        (float(last["x"]) - float(first["x"])) ** 2
        + (float(last["y"]) - float(first["y"])) ** 2
        + (float(last["z"]) - float(first["z"])) ** 2
    )
    distance = 0.0
    for prev, cur in zip(window, window[1:]):
        distance += math.sqrt(
            (float(cur["x"]) - float(prev["x"])) ** 2
            + (float(cur["y"]) - float(prev["y"])) ** 2
            + (float(cur["z"]) - float(prev["z"])) ** 2
        )
    duration = float(last["time_s"]) - float(first["time_s"])
    return _json({
        "available": True,
        "from_time": from_time,
        "to_time": to_time,
        "pose_count": len(window),
        "displacement_m": displacement,
        "path_distance_m": distance,
        "avg_speed_m_s": distance / duration if duration > 0 else 0.0,
        "start_pose": first,
        "end_pose": last,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Genspark MCP server — exposes scene analysis tools"
    )
    parser.add_argument(
        "recording", nargs="?",
        help="Path to .vis.pb recording file (enables get_motioncap_tracks)"
    )
    args = parser.parse_args()

    if args.recording:
        set_recording(Path(args.recording).resolve())
        print(f"[genspark-server] Recording context: {_recording_path}", file=sys.stderr)

    mcp.run()
