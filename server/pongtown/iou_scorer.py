"""Per-frame quad-vs-mask IoU scoring with ignore classes.

Score:
    target = table_top_mask ∪ net_mask
    quad   = rasterised reprojected quadrilateral
    ignore = person_mask ∪ bat_mask     (pixels that should NOT penalise us
                                         in either direction)

    IoU = |(quad ∩ target) \\ ignore| / |(quad ∪ target) \\ ignore|

Ignore pixels are removed from both the intersection and the union (only
when they actually lie inside one of those sets — pixels outside both are
already excluded). This way a person / bat occluding the table neither
boosts nor reduces the score.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class FrameScore:
    quad_pixels: int            # |quad|
    target_pixels: int          # |target|
    intersection_pixels: int    # |quad ∩ target| after removing ignore
    union_pixels: int           # |quad ∪ target| after removing ignore
    ignored_intersection: int   # ignore pixels removed from intersection
    ignored_union: int          # ignore pixels removed from union
    iou: float                  # final score


class IoUScorer:
    """Score a per-frame reprojected quad against the segmentation masks."""

    def __init__(self) -> None:
        pass

    def score(
        self,
        quad_img: np.ndarray | None,
        image_shape: tuple[int, int],
        table_top_mask: np.ndarray | None,
        net_mask: np.ndarray | None,
        person_mask: np.ndarray | None,
        bat_mask: np.ndarray | None,
    ) -> FrameScore:
        h, w = image_shape
        if quad_img is None:
            return FrameScore(0, 0, 0, 0, 0, 0, 0.0)

        # Rasterise the quad.
        canvas = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(
            canvas,
            [np.round(quad_img).astype(np.int32).reshape(-1, 1, 2)],
            1,
        )
        quad = canvas.astype(bool)

        # Build target = table_top ∪ net.
        target = np.zeros((h, w), dtype=bool)
        if table_top_mask is not None:
            target |= table_top_mask
        if net_mask is not None:
            target |= net_mask

        # Build ignore = person ∪ bat.
        ignore = np.zeros((h, w), dtype=bool)
        if person_mask is not None:
            ignore |= person_mask
        if bat_mask is not None:
            ignore |= bat_mask

        intersection = quad & target
        union = quad | target

        ignored_intersection = int((intersection & ignore).sum())
        ignored_union = int((union & ignore).sum())

        intersection_after = intersection & ~ignore
        union_after = union & ~ignore

        inter_n = int(intersection_after.sum())
        union_n = int(union_after.sum())
        iou = float(inter_n) / float(union_n) if union_n > 0 else 0.0

        return FrameScore(
            quad_pixels=int(quad.sum()),
            target_pixels=int(target.sum()),
            intersection_pixels=inter_n,
            union_pixels=union_n,
            ignored_intersection=ignored_intersection,
            ignored_union=ignored_union,
            iou=iou,
        )


def summarise(scores: list[FrameScore]) -> dict:
    """Return mean / min / max / deciles of `iou` across the supplied scores.

    Frames with target_pixels == 0 (no table mask in this frame) are
    skipped — scoring them as 0 IoU pulls the distribution down for a
    reason that has nothing to do with pose quality.
    """
    arr = np.array([s.iou for s in scores if s.target_pixels > 0])
    if arr.size == 0:
        return {"n": 0}
    out: dict[str, float | int] = {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }
    for q in range(10, 100, 10):
        out[f"p{q}"] = float(np.percentile(arr, q))
    return out
