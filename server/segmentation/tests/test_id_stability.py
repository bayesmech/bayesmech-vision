import numpy as np

from segmentation.id_stability import (
    SegmentationIdStabilizer,
    SegmentationMaskCandidate,
)


def _mask(offset: int = 0) -> np.ndarray:
    mask = np.zeros((24, 24), dtype=bool)
    mask[6 + offset : 16 + offset, 7:17] = True
    return mask


def test_stabilizer_matches_same_label_mask_iou_after_session_reset() -> None:
    stabilizer = SegmentationIdStabilizer(iou_threshold=0.7)

    first = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=10,
                label="person",
                confidence=0.9,
                mask=_mask(),
            )
        ]
    )
    assert first[0].stable_object_id == 1

    stabilizer.reset_session()
    second = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=3,
                label="person",
                confidence=0.8,
                mask=_mask(offset=1),
            )
        ]
    )

    assert second[0].stable_object_id == first[0].stable_object_id


def test_stabilizer_does_not_match_different_label() -> None:
    stabilizer = SegmentationIdStabilizer(iou_threshold=0.7)

    first = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=1,
                label="person",
                confidence=0.9,
                mask=_mask(),
            )
        ]
    )

    stabilizer.reset_session()
    second = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=1,
                label="chair",
                confidence=0.9,
                mask=_mask(),
            )
        ]
    )

    assert second[0].stable_object_id != first[0].stable_object_id


def test_stabilizer_does_not_match_same_label_below_mask_iou_threshold() -> None:
    stabilizer = SegmentationIdStabilizer(iou_threshold=0.7)

    first = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=1,
                label="person",
                confidence=0.9,
                mask=_mask(),
            )
        ]
    )

    stabilizer.reset_session()
    second = stabilizer.stabilize(
        [
            SegmentationMaskCandidate(
                raw_object_id=2,
                label="person",
                confidence=0.9,
                mask=_mask(offset=8),
            )
        ]
    )

    assert second[0].stable_object_id != first[0].stable_object_id
