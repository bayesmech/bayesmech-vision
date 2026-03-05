"""
Annotator — annotation store for segmentation results.

Loads pre-computed .seg.pb files (produced by segmentation/main.py) and serves
lookups by (timestamp_ns, frame_number). No live segmentation server connection.

Annotation file: recordings/foo.vis.pb -> recordings/foo.seg.pb
Format: length-delimited SegmentationResponse protos.

To generate annotations offline:
    uv run python segmentation/segmentation/main.py recordings/<name>.vis.pb
"""

import bisect
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

_server_root = Path(__file__).parent.parent
_project_root = _server_root.parent
sys.path.append(str(_project_root))
sys.path.append(str(_project_root / "proto"))
sys.path.append(str(_server_root))
from proto import perceiver_pb2, segmentation_pb2

from streamlog.protoio import ProtoIO

logger = logging.getLogger(__name__)

PerceiverDataFrame = perceiver_pb2.PerceiverDataFrame
SegmentationResponse = segmentation_pb2.SegmentationResponse

_seg_io = ProtoIO(SegmentationResponse)

# Key type for annotation lookup: (timestamp_ns, frame_number)
AnnotationKey = tuple[int, int]


def _key(fid: perceiver_pb2.PerceiverFrameIdentifier) -> AnnotationKey:
    return (fid.timestamp_ns, fid.frame_number)


def _seg_path(recording_path: Path) -> Path:
    """recordings/YYYYMMDD_HHMMSS.vis.pb -> recordings/YYYYMMDD_HHMMSS.seg.pb"""
    name = recording_path.name
    if name.endswith(".vis.pb"):
        return recording_path.parent / (name.removesuffix(".vis.pb") + ".seg.pb")
    return recording_path.with_suffix(".seg.pb")


class Annotator:
    """
    Pure annotation store — loads and serves pre-computed segmentation results.

    No live segmentation server connection. Use segmentation/main.py to generate
    .seg.pb files offline, then this class loads and serves them.
    """

    def __init__(self, **_kwargs) -> None:
        self._annotations: dict[AnnotationKey, SegmentationResponse] = {}
        self._by_frame_number: dict[int, SegmentationResponse] = {}
        self._sorted_fns: list[int] = []
        self._recording_path: Optional[Path] = None
        self._annotation_callback: Optional[Callable] = None

    # ── Lifecycle (no-ops, kept for interface compatibility) ────────────

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    # ── Annotation store API ───────────────────────────────────────────

    def set_annotation_callback(self, cb: Callable) -> None:
        """Set callback(SegmentationResponse) for broadcasting new annotations."""
        self._annotation_callback = cb

    def load_annotations(self, recording_path: Path) -> int:
        """Load existing .seg.pb file into memory dict. Returns count loaded."""
        self._annotations.clear()
        self._by_frame_number.clear()
        self._sorted_fns.clear()
        self._recording_path = recording_path
        seg_file = _seg_path(recording_path)
        if not seg_file.exists():
            logger.info(f"No annotation file found: {seg_file.name}")
            return 0

        responses = _seg_io.read_file(seg_file)
        for resp in responses:
            k = _key(resp.frame_identifier)
            self._annotations[k] = resp
            fn = resp.frame_identifier.frame_number
            self._by_frame_number[fn] = resp
        self._sorted_fns = sorted(self._by_frame_number.keys())
        logger.info(f"Loaded {len(responses)} annotations from {seg_file.name}")
        return len(responses)

    def annotate_recording(self, frames: list[PerceiverDataFrame]) -> None:
        """No-op. Use segmentation/main.py for offline batch annotation."""
        already = len(self._annotations)
        total = len(frames)
        if already > 0:
            logger.info(f"Annotation store has {already}/{total} frames annotated")
        else:
            logger.info(
                f"No annotations loaded for {total} frames. "
                f"Run: uv run python segmentation/segmentation/main.py <recording>"
            )

    def get_annotation(self, ts: int, fn: int) -> Optional[SegmentationResponse]:
        return self._annotations.get((ts, fn))

    def get_annotation_floor(self, fn: int) -> Optional[SegmentationResponse]:
        """Return the annotation for the largest frame_number <= fn."""
        idx = bisect.bisect_right(self._sorted_fns, fn) - 1
        if idx < 0:
            return None
        return self._by_frame_number.get(self._sorted_fns[idx])

    def has_annotation(self, ts: int, fn: int) -> bool:
        return (ts, fn) in self._annotations

    def all_annotations(self) -> list[SegmentationResponse]:
        return list(self._annotations.values())

    @property
    def pending_count(self) -> int:
        return 0

    @property
    def completed_count(self) -> int:
        return len(self._annotations)
