# Segmentation

Offline video segmentation using SAM3 text prompts. Processes `.vis.pb` recordings and
produces `.segmentation.pb` annotation files that the dashboard can display.

## Setup

### SAM3 (one-time)

SAM3 weights require approval — visit [huggingface.co/facebook/sam3](https://huggingface.co/facebook/sam3)
and request access. Once approved:

```bash
huggingface-cli login
```

The model (~3.4 GB) downloads automatically on first run.

### Install dependencies

```bash
cd server && uv sync
```

## Usage

From the project root:

```bash
cd server

# SAM3: segment named concepts across a recording
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --text "chair, person, desk"

# Multi-word concepts are passed through intact
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --text "snooker table, yellow ball"

# Subsample — only emit annotations for every 5th frame
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --text "object" --sample-every 5

# Quick test on first 100 frames
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --text "table, person" --max-frames 100
```

Output is written to `recordings/<name>.segmentation.pb` alongside the input file.

## CLI Options

### Shared

| Flag | Default | Description |
|------|---------|-------------|
| `--sample-every` | from config | Emit annotations every N frames (SAM3 still processes every frame for tracking) |
| `--write-every` | `50` | Flush results to disk every N frames |
| `--max-frames` | `0` | Limit to first N frames for testing (0 = all) |

### Prompt Options

| Flag | Default | Description |
|------|---------|-------------|
| `--text` | required | Comma-separated concepts; spaces inside a concept are preserved (e.g. `"snooker table, yellow ball"`) |
| `--score-thresh` | `0.5` | Min detection confidence to include a mask |

## Configuration

`config.yaml` sets the default runtime behavior:

```yaml
sam3:
  dtype: "bfloat16"
  inference_height: 360
  max_num_objects: 8
  session_reset_frames: 100

sampling:
  sample_every_x_frames: 1
```

## Sampling Behaviour

SAM3 **always processes every frame** to maintain temporal tracking consistency.
The `sample_every_x_frames` parameter only controls which frames have annotations *saved* to
the `.segmentation.pb` file. This reduces output file size and annotation density without affecting
tracking quality.

## How It Works

1. Loads all proto frames (JPEG bytes stay compressed)
2. Initialises a `Sam3VideoModel` streaming session with the specified text concepts
3. For each frame: decodes JPEG → runs SAM3 → tracker maintains object identity
4. Saves results for every `--sample-every` frames (streaming append to `.segmentation.pb`)

## Mask Format

```
[height: uint32 LE][width: uint32 LE][zlib(np.packbits(mask.flatten()))]
```

To decode in Python:

```python
import struct, zlib, numpy as np

h, w = struct.unpack('<II', mask_data[:8])
packed = zlib.decompress(mask_data[8:])
mask = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[:h * w].reshape(h, w)
```

## Performance

| Model | VRAM | Notes |
|-------|------|-------|
| SAM3 (bfloat16) | ~2 GB | Recommended default for text-prompted segmentation |
| SAM3 (float32) | ~4 GB | Higher precision; requires ≥6 GB VRAM |
