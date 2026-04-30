import struct
import zlib

import numpy as np

from proto import segmentation_pb2
from motioncap.segmentation_tracks import build_segmentation_trajectories


def _encode_mask(mask: np.ndarray) -> bytes:
    h, w = mask.shape
    header = struct.pack("<II", h, w)
    packed = np.packbits(mask.ravel())
    return header + zlib.compress(packed.tobytes(), level=1)


def _add_mask(response, object_id: int, label: str, mask: np.ndarray) -> None:
    item = response.masks.add()
    item.object_id = object_id
    item.label = label
    item.confidence = 0.9
    item.pixel_count = int(mask.sum())
    item.mask_data = _encode_mask(mask)


def test_segmentation_trajectories_are_gated_by_heatmap_motion() -> None:
    frame_ids = [100 + idx for idx in range(5)]
    timestamps = [idx * 1_000_000 for idx in range(5)]
    heatmaps = [np.zeros((10, 10), dtype=np.uint8) for _ in frame_ids]

    moving_mask = np.zeros((10, 10), dtype=bool)
    moving_mask[2:5, 2:5] = True
    static_mask = np.zeros((10, 10), dtype=bool)
    static_mask[6:9, 6:9] = True

    for idx in (1, 2, 3):
        heatmaps[idx][moving_mask] = 80

    records = []
    for idx, frame_id in enumerate(frame_ids):
        response = segmentation_pb2.SegmentationResponse()
        response.frame_identifier.frame_number = frame_id
        response.frame_identifier.timestamp_ns = timestamps[idx]
        _add_mask(response, 1, "person", moving_mask)
        _add_mask(response, 2, "chair", static_mask)
        records.append(response)

    tracks = build_segmentation_trajectories(
        records,
        heatmaps,
        frame_ids,
        timestamps,
        {
            "segmentation_tracking": {
                "motion_value_threshold": 48,
                "min_motion_observations": 2,
                "min_motion_observation_fraction": 0.05,
                "min_presence_fraction": 0.01,
                "min_mask_area": 1,
            }
        },
    )

    assert [track.track_id for track in tracks] == [1]
    assert tracks[0].label == "person"
    assert sorted(tracks[0].positions) == [0, 1, 2, 3, 4]
