#!/usr/bin/env bash
# Download evaluation datasets for VGGT-Omega inference.
# Includes:
#   7scenes  - Microsoft 7-Scenes indoor relocalization benchmark
#   sintel   - MPI-Sintel dynamic scene depth + camera benchmark
#
# Usage: ./scripts/download_datasets.sh [data_dir]
#   data_dir defaults to ./data
#
# Set DATASETS env var to select a subset, e.g.: DATASETS=sintel ./scripts/download_datasets.sh
#
# 7-Scenes license: non-commercial use only.
#   https://www.microsoft.com/en-us/research/project/rgb-d-dataset-7-scenes/
# Sintel license: Creative Commons Attribution 3.0 (CC BY 3.0).
#   http://sintel.is.tue.mpg.de/

set -euo pipefail

DATA_DIR="${1:-data}"
SEVEN_SCENES_DIR="$DATA_DIR/7scenes"

# ── helpers ────────────────────────────────────────────────────────────────────

check_deps() {
    for cmd in wget unzip tar; do
        command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd not found"; exit 1; }
    done
}

download_and_extract() {
    local url="$1"
    local dest="$2"
    local name
    name=$(basename "$url")
    local zip_path="$dest/$name"

    if [[ -f "$zip_path" ]]; then
        echo "  Already downloaded: $name"
    else
        echo "  Downloading $name ..."
        wget --no-verbose --show-progress -P "$dest" "$url"
    fi

    echo "  Extracting $name ..."
    unzip -q -o "$zip_path" -d "$dest"
    rm -f "$zip_path"
}

# ── 7-Scenes ───────────────────────────────────────────────────────────────────

download_7scenes() {
    echo "=== Microsoft 7-Scenes ==="
    mkdir -p "$SEVEN_SCENES_DIR"

    local base="http://download.microsoft.com/download/2/8/5/28564B23-0828-408F-8631-23B1EFF1DAC8"

    local scenes=(chess fire heads office pumpkin redkitchen stairs)
    for scene in "${scenes[@]}"; do
        echo "--- $scene ---"
        download_and_extract "$base/$scene.zip" "$SEVEN_SCENES_DIR"
    done

    echo "7-Scenes saved to $SEVEN_SCENES_DIR"
    echo ""
}

# ── MPI-Sintel ─────────────────────────────────────────────────────────────────

download_sintel() {
    echo "=== MPI-Sintel ==="
    local sintel_dir="$DATA_DIR/sintel"
    mkdir -p "$sintel_dir"

    local base="http://files.is.tue.mpg.de/sintel"

    # Training split: clean + final passes with depth and camera ground truth
    local archives=(
        "MPI-Sintel-training_images.zip"
        "MPI-Sintel-training_extras.zip"
    )
    for archive in "${archives[@]}"; do
        local zip_path="$sintel_dir/$archive"
        if [[ -f "$zip_path" ]]; then
            echo "  Already downloaded: $archive"
        else
            echo "  Downloading $archive ..."
            wget --no-verbose --show-progress -P "$sintel_dir" "$base/$archive"
        fi
        echo "  Extracting $archive ..."
        unzip -q -o "$zip_path" -d "$sintel_dir"
        rm -f "$zip_path"
    done

    # Camera parameters (camdata_left.tar.gz inside training_extras; also available separately)
    echo "Sintel saved to $sintel_dir"
    echo "  Structure: sintel/{training/{clean,final,depth,camdata_left}}"
    echo ""
}

# ── main ───────────────────────────────────────────────────────────────────────

check_deps
mkdir -p "$DATA_DIR"

DATASETS="${DATASETS:-7scenes sintel}"

if [[ "$DATASETS" == *"7scenes"* ]]; then
    download_7scenes
fi

if [[ "$DATASETS" == *"sintel"* ]]; then
    download_sintel
fi

echo "All requested datasets downloaded to $DATA_DIR"
