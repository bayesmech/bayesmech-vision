"""Stable object IDs for chunked segmentation output."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class SegmentationMaskCandidate:
    raw_object_id: int
    label: str
    confidence: float
    mask: np.ndarray


@dataclass(frozen=True)
class StableSegmentationMask:
    stable_object_id: int
    raw_object_id: int
    label: str
    confidence: float
    mask: np.ndarray


@dataclass
class _StableObjectState:
    object_id: int
    label: str
    normalized_label: str
    mask: np.ndarray


def _normalized_label(label: str) -> str:
    return label.strip().lower()


def mask_boundary(mask: np.ndarray, dilation_px: int = 2) -> np.ndarray:
    """Return a slightly dilated mask boundary for robust boundary IoU."""
    mask_u8 = mask.astype(np.uint8, copy=False)
    if mask_u8.size == 0 or not mask_u8.any():
        return np.zeros_like(mask_u8, dtype=bool)

    kernel = np.ones((3, 3), dtype=np.uint8)
    eroded = cv2.erode(mask_u8, kernel, iterations=1)
    boundary = (mask_u8 > 0) & (eroded == 0)

    if dilation_px > 0:
        k = 2 * int(dilation_px) + 1
        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        boundary = cv2.dilate(boundary.astype(np.uint8), dilate_kernel) > 0

    return boundary


def boundary_iou(
    mask_a: np.ndarray,
    mask_b: np.ndarray,
    dilation_px: int = 2,
) -> float:
    """Compute IoU between dilated object boundaries."""
    if mask_a.shape != mask_b.shape:
        return 0.0

    boundary_a = mask_boundary(mask_a, dilation_px=dilation_px)
    boundary_b = mask_boundary(mask_b, dilation_px=dilation_px)
    union = np.logical_or(boundary_a, boundary_b).sum()
    if union == 0:
        return 0.0
    intersection = np.logical_and(boundary_a, boundary_b).sum()
    return float(intersection / union)


class SegmentationIdStabilizer:
    """Map model-local object IDs to stable IDs across SAM session resets.

    Within a single SAM session the model's raw object IDs are trusted. When the
    session resets, raw IDs are intentionally forgotten, and new masks are
    matched back to previous stable objects by same-label boundary IoU.
    """

    def __init__(
        self,
        *,
        boundary_iou_threshold: float = 0.45,
        boundary_dilation_px: int = 2,
    ) -> None:
        self.boundary_iou_threshold = float(boundary_iou_threshold)
        self.boundary_dilation_px = int(boundary_dilation_px)
        self._states: dict[int, _StableObjectState] = {}
        self._raw_to_stable: dict[int, int] = {}
        self._next_stable_id = 1

    def reset_session(self) -> None:
        """Forget raw model IDs while preserving stable object states."""
        self._raw_to_stable.clear()

    def stabilize(
        self,
        candidates: list[SegmentationMaskCandidate],
    ) -> list[StableSegmentationMask]:
        assignments: list[tuple[SegmentationMaskCandidate, int]] = []
        assigned_stable_ids: set[int] = set()
        pending: list[SegmentationMaskCandidate] = []

        for candidate in candidates:
            stable_id = self._raw_to_stable.get(candidate.raw_object_id)
            state = self._states.get(stable_id) if stable_id is not None else None
            if (
                state is not None
                and state.normalized_label == _normalized_label(candidate.label)
                and stable_id not in assigned_stable_ids
            ):
                assignments.append((candidate, stable_id))
                assigned_stable_ids.add(stable_id)
            else:
                pending.append(candidate)

        scored_pairs: list[tuple[float, int, int]] = []
        for cand_idx, candidate in enumerate(pending):
            cand_label = _normalized_label(candidate.label)
            for stable_id, state in self._states.items():
                if stable_id in assigned_stable_ids:
                    continue
                if state.normalized_label != cand_label:
                    continue
                score = boundary_iou(
                    candidate.mask,
                    state.mask,
                    dilation_px=self.boundary_dilation_px,
                )
                if score >= self.boundary_iou_threshold:
                    scored_pairs.append((score, cand_idx, stable_id))

        matched_candidate_indices: set[int] = set()
        for _score, cand_idx, stable_id in sorted(scored_pairs, reverse=True):
            if (
                cand_idx in matched_candidate_indices
                or stable_id in assigned_stable_ids
            ):
                continue
            assignments.append((pending[cand_idx], stable_id))
            matched_candidate_indices.add(cand_idx)
            assigned_stable_ids.add(stable_id)

        for cand_idx, candidate in enumerate(pending):
            if cand_idx in matched_candidate_indices:
                continue
            stable_id = self._new_stable_id()
            assignments.append((candidate, stable_id))
            assigned_stable_ids.add(stable_id)

        stable_masks: list[StableSegmentationMask] = []
        for candidate, stable_id in assignments:
            label = candidate.label
            state = _StableObjectState(
                object_id=stable_id,
                label=label,
                normalized_label=_normalized_label(label),
                mask=candidate.mask.copy(),
            )
            self._states[stable_id] = state
            self._raw_to_stable[candidate.raw_object_id] = stable_id
            stable_masks.append(
                StableSegmentationMask(
                    stable_object_id=stable_id,
                    raw_object_id=candidate.raw_object_id,
                    label=label,
                    confidence=candidate.confidence,
                    mask=candidate.mask,
                )
            )

        stable_masks.sort(key=lambda item: item.stable_object_id)
        return stable_masks

    def _new_stable_id(self) -> int:
        stable_id = self._next_stable_id
        self._next_stable_id += 1
        return stable_id
