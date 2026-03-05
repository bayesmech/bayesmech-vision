# Segmentation

Offline video segmentation using SAM2 or SAM3. Processes `.vis.pb` recordings and
produces `.seg.pb` annotation files that the dashboard can display.

Select the backend with `--model sam2` (grid-based) or `--model sam3` (text prompts).

## Setup

### SAM3 (one-time)

SAM3 weights require approval — visit [huggingface.co/facebook/sam3](https://huggingface.co/facebook/sam3)
and request access. Once approved:

```bash
huggingface-cli login
```

The model (~3.4 GB) downloads automatically on first run.

### SAM2 (one-time)

Download the checkpoint:

```bash
wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt \
  -P server/segmentation/models/sam2/
```

Other variants (`tiny`, `base_plus`, `large`) follow the same URL pattern.

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
    --model sam3 --text "chair person desk"

# SAM2: auto-grid (no text prompts needed)
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --model sam2 --variant small

# Subsample — only emit annotations for every 5th frame
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --model sam3 --text "object" --sample-every 5

# Quick test on first 100 frames
uv run python segmentation/main.py ../recordings/<name>.vis.pb \
    --model sam2 --max-frames 100
```

Output is written to `recordings/<name>.seg.pb` alongside the input file.

## CLI Options

### Shared

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `sam3` | Backend: `sam2` or `sam3` |
| `--sample-every` | from config | Emit annotations every N frames (both models still process every frame for tracking) |
| `--write-every` | `50` | Flush results to disk every N frames |
| `--max-frames` | `0` | Limit to first N frames for testing (0 = all) |

### SAM3 options

| Flag | Default | Description |
|------|---------|-------------|
| `--text` | `"object"` | Space or comma-separated concepts (e.g. `"chair person desk"`) |
| `--score-thresh` | `0.5` | Min detection confidence to include a mask |

### SAM2 options

| Flag | Default | Description |
|------|---------|-------------|
| `--variant` | `small` | Model size: `tiny`, `small`, `base_plus`, `large` |
| `--chunk` | `100` | Frames per processing chunk |
| `--grid` | `64` | Grid spacing for auto-segmentation prompts (px) |
| `--max-objects` | `6` | Max objects to track simultaneously |
| `--reseed-every` | `5` | Re-run grid prompts every N chunks (0 = mask handoff only) |
| `--vos` | off | Enable torch.compile optimization (single-chunk runs only) |

## Configuration

`segmentation_config.yaml` sets defaults that CLI flags can override:

```yaml
model:
  sam3:
    dtype: "bfloat16"   # "float32" for higher precision if VRAM allows

sampling:
  sample_every_x_frames: 1  # 1 = every frame; 5 = every 5th frame
```

## Sampling Behaviour

Both SAM2 and SAM3 **always process every frame** to maintain temporal tracking consistency.
The `sample_every_x_frames` parameter only controls which frames have annotations *saved* to
the `.seg.pb` file. This reduces output file size and annotation density without affecting
tracking quality.

## How It Works

### SAM3 (streaming)

1. Loads all proto frames (JPEG bytes stay compressed)
2. Initialises a `Sam3VideoModel` streaming session with the specified text concepts
3. For each frame: decodes JPEG → runs SAM3 → tracker maintains object identity
4. Saves results for every `--sample-every` frames (streaming append to `.seg.pb`)

### SAM2 (chunked)

1. Loads all proto frames (JPEG bytes stay compressed)
2. For each chunk of `--chunk` frames:
   - Decodes JPEG → normalises to SAM2 tensors (in memory only)
   - Builds SAM2 inference state directly from tensors (no disk I/O)
   - First chunk / every `--reseed-every` chunks: grid point prompts
   - Other chunks: passes final masks from previous chunk as prompts (handoff)
   - Runs `propagate_in_video` through the chunk
   - Saves results for sampled frames, streams to `.seg.pb` (append mode)
3. CUDA cache cleared between chunks to bound VRAM

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
| SAM2 tiny | < 1 GB | Fastest; good for quick runs |
| SAM2 small | < 1 GB | Good quality; recommended default |
| SAM3 (bfloat16) | ~2 GB | Text prompts; ~18× more params than SAM2 small |
| SAM3 (float32) | ~4 GB | Higher precision; requires ≥6 GB VRAM |
