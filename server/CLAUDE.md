# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **See also**: the root `CLAUDE.md` for project-wide architecture, protobuf formats, WebSocket protocol, mask encoding, and Android app structure. This file covers `server/`-specific tooling.

## Running Tools (all commands run from `server/`)

```bash
# Vision server
uv run python streamlog/main.py   # (or from project root: uv run python server/streamlog/main.py)

# Segmentation — SAM3 (text prompts, requires HuggingFace login)
uv run python segmentation/main.py ../recordings/<name>.vis.pb --model sam3 --text "chair person"

# Segmentation — SAM2 (grid prompts, requires model checkpoint)
uv run python segmentation/main.py ../recordings/<name>.vis.pb --model sam2 --variant small

# Segmentation — quick test (first N frames, emit every Nth)
uv run python segmentation/main.py ../recordings/<name>.vis.pb --model sam3 --text "object" \
    --max-frames 200 --sample-every 5

# Motion capture: RAFT heatmaps + tracking (unified)
uv run python motioncap/main.py ../recordings/<name>.vis.pb
uv run python motioncap/main.py ../recordings/<name>.vis.pb --output-video
uv run python motioncap/main.py ../recordings/<name>.vis.pb --max-frames 200 --output-video

# Re-run only tracking (set regenerate_raft: false in motioncap_config.yaml first)
uv run python motioncap/main.py ../recordings/<name>.vis.pb

# AI video analysis (Gemini / Claude / OpenAI)
uv run python genspark/main.py ../recordings/<name>.vis.pb
uv run python genspark/main.py ../recordings/<name>.vis.pb --provider claude

# 3D reconstruction: COLMAP SfM + Gaussian Splatting
uv run python reconstruct/main.py ../recordings/<name>.vis.pb
uv run python reconstruct/main.py ../recordings/<name>.vis.pb --no-splat   # COLMAP only
uv run python reconstruct/main.py ../recordings/<name>.vis.pb --max-frames 100 --sample-every 10  # quick test
uv run python reconstruct/main.py ../recordings/<name>.vis.pb --dense-mvs   # + dense MVS (requires colmap binary)

# Homography analysis (interactive, from server/)
uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb
```

## One-time Setup

```bash
# RAFT model weights for motioncap (downloads large ~21 MB or small ~5 MB)
uv run python motioncap/download_models.py           # large (default)
uv run python motioncap/download_models.py small     # small variant

# SAM2 checkpoint for segmentation
wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt \
  -P segmentation/models/sam2/

# SAM3 (HuggingFace gated model — request access at facebook/sam3)
huggingface-cli login
```

## Module Structure

| Directory | Purpose |
|-----------|---------|
| `streamlog/` | FastAPI server — live streaming, playback, dashboard WebSocket |
| `segmentation/` | Offline SAM2/SAM3 annotator → writes `.seg.pb` |
| `motioncap/` | Offline RAFT optical-flow motion heatmap → writes `.motion.pb` |
| `genspark/` | Offline AI video analysis (Gemini/Claude/OpenAI) |
| `reconstruct/` | Offline COLMAP SfM + Gaussian Splatting → writes `.recon/`, `.splat.ply`, `.recon.pb` |

## Motion Capture (`motioncap/`)

Unified pipeline via `motioncap/main.py` — two sequential steps, one output file:

- **Step 1 — RAFT** (`raft_motion.py`): dense optical flow with pose-based background subtraction.
  - `subtraction_mode`: `"pose"` (best, uses ARCore depth+pose), `"consensus"` (median flow, no pose), `"absolute"` (raw flow)
  - `reference.strategy`: `"consecutive"` | `"rolling"` | `"time"` | `"first"`
  - Two-pass: pass 1 computes float16 residuals, pass 2 applies global normalisation

- **Step 2 — Tracker** (`tracker.py`): temporal consistency filter + blob detection + nearest-neighbour tracking with velocity prediction. Merges fragmented tracks and interpolates gaps.

- **Pipeline flags** (`motioncap_config.yaml` → `pipeline` section):
  - `pipeline.regenerate_raft: false` — skip RAFT, load heatmaps from existing `.motion.pb`
  - `pipeline.regenerate_tracking: false` — skip tracker, load tracks from existing `.motion.pb`
  - Use `regenerate_raft: false` to quickly re-tune tracking parameters without re-running slow RAFT

Output: `recordings/<name>.motion.pb` — per-frame heatmap records followed by a single tracks-summary record (`MotionCaptureResponse` with `tracks` field populated, detected via `len(resp.tracks) > 0`)

Optional: `recordings/<name>.motion.mp4` — heatmap overlay on RGB with coloured trajectory tails

Also available: **SIFT** (`sift_motion.py`): feature-based, no GPU required.

## Segmentation (`segmentation/`)

- **SAM3** (default): streaming per-frame, text/concept prompts via `--text "..."`, bfloat16 required on 6 GB VRAM GPUs. `trigger_type = TEXT`.
- **SAM2**: chunked 100-frame processing with grid point prompts + mask handoff between chunks. `trigger_type = PROPAGATION`.
- Both backends process every frame for tracker continuity; only emit every `--sample-every` frames.
- Config: `segmentation/segmentation_config.yaml` — `model.sam3.dtype`, `sampling.sample_every_x_frames`

Output: `recordings/<name>.seg.pb` (length-delimited `SegmentationResponse` protos)

## AI Analysis (`genspark/`)

Sends a `.vis.pb` recording to a vision LLM for analysis against a prompt in `genspark/prompt.md`.

- **gemini**: native video upload via Files API (best temporal understanding); needs `GEMINI_API_KEY`
- **claude**: evenly-sampled frames as base64 JPEG images; needs `ANTHROPIC_API_KEY`
- **openai**: evenly-sampled frames as base64 JPEG images; needs `OPENAI_API_KEY`

Config: `genspark/genspark_config.yaml` — provider, video resolution/fps/duration, model names.

## Path Convention

All server Python files set `_server_root = Path(__file__).resolve().parent.parent` and `_project_root = _server_root.parent`, then insert both into `sys.path`. This allows `from proto import perceiver_pb2` and `from streamlog.protoio import ProtoIO` to work regardless of where the script is invoked from.
