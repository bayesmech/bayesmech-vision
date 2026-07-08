#!/usr/bin/env bash
# Download VGGT-Omega model checkpoints from Hugging Face.
# Requires: huggingface-hub CLI (`pip install huggingface_hub`)
# You must request access at https://huggingface.co/facebook/VGGT-Omega first,
# then authenticate with `huggingface-cli login`.

set -euo pipefail

CKPT_DIR="${1:-checkpoints}"
mkdir -p "$CKPT_DIR"

echo "Downloading VGGT-Omega checkpoints to $CKPT_DIR ..."
echo "NOTE: Access to facebook/VGGT-Omega on Hugging Face must be requested"
echo "      and approved before this script will succeed."
echo "      Run: huggingface-cli login"
echo ""

huggingface-cli download facebook/VGGT-Omega \
    --local-dir "$CKPT_DIR/VGGT-Omega" \
    --repo-type model

echo "Checkpoints saved to $CKPT_DIR/VGGT-Omega"
echo "Done."
