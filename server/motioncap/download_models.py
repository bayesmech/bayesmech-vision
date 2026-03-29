#!/usr/bin/env python3
"""
Download RAFT optical-flow model weights into server/motioncap/models/raft/.

Usage (from project root):
    cd server
    uv run python motioncap/download_models.py
"""

import sys
import urllib.request
from pathlib import Path

_models_dir = Path(__file__).parent / "models" / "raft"
_models_dir.mkdir(parents=True, exist_ok=True)


def _progress(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100.0, downloaded / total_size * 100)
        mb = downloaded / 1e6
        total_mb = total_size / 1e6
        print(f"\r  {pct:5.1f}%  {mb:.1f} / {total_mb:.1f} MB", end="", flush=True)


def download(variant: str) -> None:
    out_path = _models_dir / f"raft_{variant}.pth"
    if out_path.exists():
        print(f"  {out_path.name} already present — skipping")
        return

    # Resolve the URL from torchvision's own metadata
    if variant == "large":
        from torchvision.models.optical_flow import Raft_Large_Weights
        url = Raft_Large_Weights.DEFAULT.url
    elif variant == "small":
        from torchvision.models.optical_flow import Raft_Small_Weights
        url = Raft_Small_Weights.DEFAULT.url
    else:
        raise ValueError(f"Unknown variant: {variant!r}")

    print(f"Downloading raft_{variant}  ← {url}")
    urllib.request.urlretrieve(url, out_path, reporthook=_progress)
    print(f"\n  Saved → {out_path}")


if __name__ == "__main__":
    variants = sys.argv[1:] or ["large"]
    for v in variants:
        download(v)
    print("Done.")
