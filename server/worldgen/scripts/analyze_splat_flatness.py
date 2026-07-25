#!/usr/bin/env python3
"""Analyze how "flat" a Gaussian-splat model's Gaussians are.

Surfaces (a wall, a tabletop) should be reconstructed by Gaussians that are
squashed along the surface normal: two large axes in the surface plane and one
near-zero axis across it. This tool measures that directly from the anisotropic
per-axis scales.

Input can be either:
  * a trained 3DGS .ply (has scale_0..2 as log std devs + rot_0..3), or
  * a viewer preview .json that carries per-point scale_x/scale_y/scale_z.

A legacy preview.json that only has the isotropic `scale` field CANNOT answer
the flatness question (the three axes were averaged away when it was written) —
the tool says so explicitly rather than guessing.

Usage:
  cd server
  uv run python worldgen/scripts/analyze_splat_flatness.py <model.splat.ply | *.preview.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


def _load_scales_from_ply(path: Path) -> np.ndarray:
    from plyfile import PlyData

    v = PlyData.read(str(path))["vertex"]
    # PLY stores scales as log std dev.
    return np.exp(
        np.stack([np.asarray(v[f"scale_{j}"]) for j in range(3)], axis=1)
    ).astype(np.float64)


def _load_scales_from_preview(path: Path) -> np.ndarray | None:
    data = json.loads(path.read_text())
    points = data.get("points", [])
    if not points:
        return None
    if not all(k in points[0] for k in ("scale_x", "scale_y", "scale_z")):
        return None
    return np.array(
        [[p["scale_x"], p["scale_y"], p["scale_z"]] for p in points], dtype=np.float64
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    path = Path(sys.argv[1]).resolve()
    if not path.exists():
        raise SystemExit(f"No such file: {path}")

    if path.suffix == ".ply":
        scales = _load_scales_from_ply(path)
    else:
        scales = _load_scales_from_preview(path)
        if scales is None:
            print(f"File: {path}")
            print(
                "\nThis preview has only the isotropic `scale` field — the three "
                "per-axis\nvariances were averaged away when it was written, so "
                "flatness is NOT\nmeasurable from it. Re-run the trainer (which now "
                "emits scale_x/y/z) or\npoint this tool at the trained .ply to measure "
                "flatness."
            )
            raise SystemExit(1)

    n = len(scales)
    s_sorted = np.sort(scales, axis=1)  # ascending: [min, mid, max] per Gaussian
    smin, smid, smax = s_sorted[:, 0], s_sorted[:, 1], s_sorted[:, 2]
    # Flatness = how much smaller the thin axis is than the two in-plane axes.
    flat_ratio = smin / np.maximum(smid, 1e-9)          # ~0 => disk-like (flat)
    planar = smid / np.maximum(smax, 1e-9)              # ~1 => the two big axes are comparable (planar, not a needle)

    print(f"File: {path}")
    print(f"Gaussians: {n:,}")
    print("\nPer-axis std dev (metres), sorted min<=mid<=max:")
    for name, arr in (("min axis", smin), ("mid axis", smid), ("max axis", smax)):
        print(
            f"  {name}: p05={np.percentile(arr,5):.4f} "
            f"med={np.median(arr):.4f} p95={np.percentile(arr,95):.4f}"
        )

    print("\nFlatness (thin-axis / mid-axis; small = flat, disk-like):")
    print(f"  median ratio: {np.median(flat_ratio):.3f}")
    for thr in (0.1, 0.2, 0.3):
        frac = float(np.mean(flat_ratio < thr))
        print(f"  ratio < {thr:.1f}: {frac*100:5.1f}%  (thin axis < {int(thr*100)}% of mid axis)")

    disk = (flat_ratio < 0.25) & (planar > 0.5)
    needle = (planar < 0.3)
    print("\nShape mix:")
    print(f"  disk-like (flat, surface-aligned): {float(np.mean(disk))*100:5.1f}%")
    print(f"  needle-like (one long axis):       {float(np.mean(needle))*100:5.1f}%")
    print(
        f"  roundish (min/max > 0.5):          "
        f"{float(np.mean(smin/np.maximum(smax,1e-9) > 0.5))*100:5.1f}%"
    )
    print(
        "\nInterpretation: a scene dominated by walls/table should show a high "
        "disk-like\nfraction — many Gaussians squashed flat along a surface normal."
    )


if __name__ == "__main__":
    main()
