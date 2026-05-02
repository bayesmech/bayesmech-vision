"""
DashboardBridge — binary WebSocket handler for dashboard connections.

Protocol
--------
Server → Dashboard (binary):
  0x01 + length-delimited PerceiverDataFrame(s)
  0x02 + length-delimited SegmentationResponse
  0x03 + length-delimited PongtownResponse(s)

Dashboard → Server (text/JSON):
  {"action": "seek", "start": N, "end": M}
  {"action": "get_stats"}
  {"action": "get_annotations"}
  {"action": "get_pongtown", "frame_number": N}

Server → Dashboard (text/JSON):
  {"type": "stats", ...}
  {"type": "annotations", ...}
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

_server_root = Path(__file__).parent.parent
_project_root = _server_root.parent
sys.path.append(str(_project_root))
sys.path.append(str(_project_root / "proto"))
sys.path.append(str(_server_root))
from proto import perceiver_pb2, pongtown_pb2, segmentation_pb2

from streamlog.protoio import ProtoIO

if TYPE_CHECKING:
    from streamlog.frame_store import FrameStore
    from streamlog.annotator import Annotator

logger = logging.getLogger(__name__)

PREFIX_FRAME = b"\x01"
PREFIX_ANNOTATION = b"\x02"
PREFIX_PONGTOWN = b"\x03"

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)
_pong_io = ProtoIO(pongtown_pb2.PongtownResponse)


class DashboardBridge:
    """Manages dashboard WebSocket connections with binary protobuf protocol."""

    def __init__(self, store: "FrameStore", annotator: "Annotator") -> None:
        self._store = store
        self._annotator = annotator
        self._connections: set[WebSocket] = set()
        self._pongtown_cache_path: Path | None = None
        self._pongtown_cache_mtime_ns: int | None = None
        self._pongtown_by_frame: dict[int, pongtown_pb2.PongtownResponse] = {}

    @staticmethod
    def _dedupe_annotations(
        annotations: list[segmentation_pb2.SegmentationResponse],
    ) -> list[segmentation_pb2.SegmentationResponse]:
        deduped: list[segmentation_pb2.SegmentationResponse] = []
        seen: set[tuple[int, int]] = set()
        for ann in annotations:
            fid = ann.frame_identifier
            key = (fid.timestamp_ns, fid.frame_number)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ann)
        return deduped

    async def _send_annotations(
        self,
        websocket: WebSocket,
        annotations: list[segmentation_pb2.SegmentationResponse],
        *,
        batch_size: int | None = None,
    ) -> None:
        annotations = self._dedupe_annotations(annotations)
        if not annotations:
            return
        if batch_size is None or len(annotations) <= batch_size:
            await websocket.send_bytes(PREFIX_ANNOTATION + _seg_io.encode(annotations))
            return
        for i in range(0, len(annotations), batch_size):
            chunk = annotations[i:i + batch_size]
            await websocket.send_bytes(PREFIX_ANNOTATION + _seg_io.encode(chunk))
            await asyncio.sleep(0)

    @staticmethod
    def _frame_number_from_pongtown(
        record: pongtown_pb2.PongtownResponse,
    ) -> int | None:
        try:
            if not record.HasField("frame_identifier"):
                return None
            return int(record.frame_identifier.frame_number)
        except Exception:
            return None

    def _current_pongtown_path(self) -> Path | None:
        current = self._store.current_file
        if current is None:
            return None

        stem = current.name.removesuffix(".vis.pb") if current.name.endswith(".vis.pb") else current.stem
        path = current.parent / f"{stem}.pongtown.pb"
        return path if path.exists() else None

    def _load_current_pongtown_by_frame(self) -> dict[int, pongtown_pb2.PongtownResponse]:
        path = self._current_pongtown_path()
        if path is None:
            self._pongtown_cache_path = None
            self._pongtown_cache_mtime_ns = None
            self._pongtown_by_frame = {}
            return {}

        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return {}

        if (
            self._pongtown_cache_path == path
            and self._pongtown_cache_mtime_ns == mtime_ns
        ):
            return self._pongtown_by_frame

        by_frame: dict[int, pongtown_pb2.PongtownResponse] = {}
        for record in _pong_io.read_file(path):
            frame_number = self._frame_number_from_pongtown(record)
            if frame_number is not None:
                by_frame[frame_number] = record

        self._pongtown_cache_path = path
        self._pongtown_cache_mtime_ns = mtime_ns
        self._pongtown_by_frame = by_frame
        logger.info(
            "Loaded %d Pongtown frame records from %s",
            len(by_frame),
            path.name,
        )
        return by_frame

    def _pongtown_for_frame_numbers(
        self,
        frame_numbers: list[int],
    ) -> list[pongtown_pb2.PongtownResponse]:
        by_frame = self._load_current_pongtown_by_frame()
        return [by_frame[frame_number] for frame_number in frame_numbers if frame_number in by_frame]

    async def _send_pongtown(
        self,
        websocket: WebSocket,
        records: list[pongtown_pb2.PongtownResponse],
        *,
        batch_size: int | None = None,
    ) -> None:
        if not records:
            return
        if batch_size is None or len(records) <= batch_size:
            await websocket.send_bytes(PREFIX_PONGTOWN + _pong_io.encode(records))
            return
        for i in range(0, len(records), batch_size):
            chunk = records[i:i + batch_size]
            await websocket.send_bytes(PREFIX_PONGTOWN + _pong_io.encode(chunk))
            await asyncio.sleep(0)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Full lifecycle of a single dashboard WS connection."""
        await websocket.accept()
        self._connections.add(websocket)
        logger.info(f"Dashboard connected  (total: {len(self._connections)})")

        if self._store.source == "live":
            # In live mode, bootstrap the dashboard with the latest frame and its
            # matching annotation. File playback is client-driven via seek().
            latest = self._store.latest()
            if latest:
                try:
                    await websocket.send_bytes(PREFIX_FRAME + _frame_io.encode([latest]))
                except Exception:
                    pass

                ann = self._annotator.get_annotation_floor(
                    latest.frame_identifier.frame_number
                )
                if ann is not None:
                    fid = ann.frame_identifier
                    masks = ann.masks or []
                    mask_sizes = [len(m.mask_data) for m in masks if m.mask_data]
                    logger.info(
                        f"Dashboard connect: sending latest annotation "
                        f"ts={fid.timestamp_ns} fn={fid.frame_number} "
                        f"masks={len(masks)} mask_data_sizes={mask_sizes}"
                    )
                    try:
                        await self._send_annotations(websocket, [ann])
                    except Exception:
                        pass
        elif self._annotator.completed_count:
            logger.info(
                "Dashboard connect: file mode with %d loaded annotations; "
                "deferring annotation replay until seek/get_annotations",
                self._annotator.completed_count,
            )

        # Subscribe to live frames
        async def on_frame(frame: perceiver_pb2.PerceiverDataFrame) -> None:
            try:
                await websocket.send_bytes(PREFIX_FRAME + _frame_io.encode([frame]))
            except Exception:
                pass

        unsub = self._store.subscribe(on_frame)

        try:
            while True:
                try:
                    text = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                    await self._handle_message(websocket, json.loads(text))
                except asyncio.TimeoutError:
                    pass  # keep-alive
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.error(f"Dashboard WS error: {exc}", exc_info=True)
        finally:
            unsub()
            self._connections.discard(websocket)
            logger.info(f"Dashboard disconnected  (total: {len(self._connections)})")

    async def broadcast_annotation(self, resp: segmentation_pb2.SegmentationResponse) -> None:
        """Push an annotation to all connected dashboards."""
        payload = PREFIX_ANNOTATION + _seg_io.encode([resp])
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_bytes(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.discard(ws)

    # ── Internal ──────────────────────────────────────────────────────────

    async def _handle_message(self, ws: WebSocket, msg: dict) -> None:
        action = msg.get("action")

        if action == "get_stats":
            await ws.send_text(json.dumps({"type": "stats", **self._store.stats()}))

        elif action == "seek":
            start = int(msg.get("start", 0))
            end = int(msg.get("end", start + 1))
            frames = self._store.get_range(start, end)
            if frames:
                payload = PREFIX_FRAME + _frame_io.encode(frames)
                await ws.send_bytes(payload)

                # Send matching annotations so segmentation pane stays in sync.
                # Use floor lookup so sparse annotations (e.g. every 30th frame)
                # are resolved to the nearest prior annotated frame.
                annotations = []
                for f in frames:
                    fid = f.frame_identifier
                    ann = self._annotator.get_annotation_floor(fid.frame_number)
                    if ann is not None:
                        annotations.append(ann)
                    else:
                        logger.debug(
                            f"seek: no annotation at or before fn={fid.frame_number}"
                        )
                logger.info(
                    f"seek [{start}:{end}] → {len(frames)} frames, {len(annotations)} annotations "
                    f"(annotator has {self._annotator.completed_count} total)"
                )
                if annotations:
                    await self._send_annotations(ws, annotations)

                frame_numbers = [
                    int(f.frame_identifier.frame_number)
                    for f in frames
                    if f.HasField("frame_identifier")
                ]
                pongtown_records = await asyncio.to_thread(
                    self._pongtown_for_frame_numbers,
                    frame_numbers,
                )
                if pongtown_records:
                    await self._send_pongtown(ws, pongtown_records)

        elif action == "get_trajectory":
            asyncio.create_task(self._send_trajectory(ws))

        elif action == "get_sensor_data":
            asyncio.create_task(self._send_sensor_data(ws))

        elif action == "get_annotations":
            frame_number = msg.get("frame_number")
            if frame_number is not None:
                ann = self._annotator.get_annotation_floor(int(frame_number))
                if ann is not None:
                    await self._send_annotations(ws, [ann])
                return

            annotations = self._annotator.all_annotations()
            if annotations:
                logger.info(
                    "get_annotations: sending %d annotations in batches",
                    len(annotations),
                )
                await self._send_annotations(ws, annotations, batch_size=64)

        elif action == "get_pongtown":
            frame_number = msg.get("frame_number")
            if frame_number is not None:
                records = await asyncio.to_thread(
                    self._pongtown_for_frame_numbers,
                    [int(frame_number)],
                )
                if records:
                    await self._send_pongtown(ws, records)
                return

            start = msg.get("start")
            end = msg.get("end")
            if start is None:
                start = 0
            if end is None:
                end = self._store.frame_count
            frame_numbers = [
                int(f.frame_identifier.frame_number)
                for f in self._store.get_range(int(start), int(end))
                if f.HasField("frame_identifier")
            ]
            records = await asyncio.to_thread(
                self._pongtown_for_frame_numbers,
                frame_numbers,
            )
            if records:
                await self._send_pongtown(ws, records, batch_size=64)

    async def _send_trajectory(self, ws: WebSocket) -> None:
        try:
            positions = await asyncio.to_thread(self._store.compute_trajectory)
            await ws.send_text(json.dumps({"type": "trajectory", "positions": positions}))
        except Exception as exc:
            logger.warning(f"Failed to send trajectory: {exc}")

    async def _send_sensor_data(self, ws: WebSocket) -> None:
        try:
            frames = await asyncio.to_thread(self._store.compute_sensor_data)
            await ws.send_text(json.dumps({"type": "sensor_data", "frames": frames}))
        except Exception as exc:
            logger.warning(f"Failed to send sensor data: {exc}")
