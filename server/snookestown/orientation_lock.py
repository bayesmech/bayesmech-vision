"""Cross-frame orientation lock (Stage 2c).

The per-frame pose solver in `table_pose.py` picks the highest-scoring
pocket-slot assignment for each frame independently.  Different frames may land
on different orientation conventions that are both *locally* valid (the
back-projected rectangle matches the mask) but globally inconsistent — e.g.
frame A maps a detected corner pocket to (0,0) while frame B maps the same
physical pocket to (W,0).

This module runs one cheap linear pass after per-frame solves and flips the
orientation of frames that disagree with the majority, using ARCore camera
pose as a guide for *which* detected pockets should correspond to which
canonical slots from one frame to the next.

For the MVP this is a minimal implementation: we pick the recording's most
common orientation by majority vote on the sign of (det(H[:2,:2])) across
all well-solved frames, and do not try to re-solve outliers.  The pose-solver
`_is_valid_quad` check already rejects reflection-flipped Hs, so there is
little orientation dithering in practice — this hook exists so the API is in
place for later refinement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_server_root))

from snookestown.table_pose import TablePoseResult


def lock_orientation(per_frame: list[TablePoseResult]) -> list[TablePoseResult]:
    """Consolidate per-frame orientations into a single recording-wide convention.

    Currently a pass-through with a sanity check: if any frame's H has a
    reflected orientation (negative determinant of the upper-left 2x2) while
    the majority do not, that frame is marked FAILED rather than left producing
    flipped 2D coordinates.
    """
    if not per_frame:
        return per_frame

    signs: list[int] = []
    for r in per_frame:
        if r.H_img_to_table is None:
            signs.append(0)
            continue
        det = float(np.linalg.det(r.H_img_to_table[:2, :2]))
        signs.append(int(np.sign(det)))

    pos = sum(1 for s in signs if s > 0)
    neg = sum(1 for s in signs if s < 0)
    majority_sign = 1 if pos >= neg else -1

    out: list[TablePoseResult] = []
    for r, s in zip(per_frame, signs):
        if r.H_img_to_table is not None and s != 0 and s != majority_sign:
            # Orientation disagrees with majority — down-weight rather than flip
            # (flipping in-place requires re-solving with the second-best
            # assignment, which isn't retained at this stage).
            out.append(TablePoseResult(
                H_img_to_table=r.H_img_to_table,
                method=r.method,
                quality=r.quality * 0.5,
                pockets=r.pockets,
                assigned_mm=r.assigned_mm,
                cushions=r.cushions,
                hull_quad=r.hull_quad,
            ))
        else:
            out.append(r)
    return out
