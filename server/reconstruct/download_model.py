#!/usr/bin/env python3
"""Download the PAGE-4D checkpoint into models/page4d/.

Usage: cd server && uv run python reconstruct/download_model.py
"""
import sys
from pathlib import Path
from urllib.request import urlopen

_URL = "https://huggingface.co/datasets/zhouk777/PAGE4D/resolve/main/checkpoint_nomask.pt"
_DEST = Path(__file__).resolve().parent / "models" / "page4d" / "checkpoint_nomask.pt"


def main() -> None:
    _DEST.parent.mkdir(parents=True, exist_ok=True)
    if _DEST.exists():
        print(f"Already present: {_DEST}")
        return
    print(f"Downloading {_URL}\n      -> {_DEST}")
    with urlopen(_URL) as r, open(_DEST, "wb") as f:
        total = int(r.headers.get("content-length", 0))
        read = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            read += len(chunk)
            if total:
                print(f"\r  {read/1e6:.0f}/{total/1e6:.0f} MB", end="", file=sys.stderr)
    print(f"\nSaved {_DEST}")


if __name__ == "__main__":
    main()
