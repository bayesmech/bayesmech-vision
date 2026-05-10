# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **See also**: the root `CLAUDE.md` for project-wide architecture, protobuf formats, WebSocket protocol, mask encoding, and Android app structure. This file covers `server/`-specific tooling.

## Running Tools (all commands run from `server/`)

```bash
# Streamlog server (Rust)
cd streamlog && cargo run

# Recordings are now stored in per-folder structure: recordings/<name>/<name>.vis.pb

# Segmentation — SAM3 (text prompts, requires HuggingFace login)
uv run python segmentation/main.py ../recordings/<name>/<name>.vis.pb --text "chair, person"

# Segmentation — quick test (first N frames, emit every Nth)
uv run python segmentation/main.py ../recordings/<name>/<name>.vis.pb --text "object" \
    --max-frames 200 --sample-every 5

# Motion capture: RAFT heatmaps + tracking (unified)
uv run python motioncap/main.py ../recordings/<name>/<name>.vis.pb
uv run python motioncap/main.py ../recordings/<name>/<name>.vis.pb --debug-render-video
uv run python motioncap/main.py ../recordings/<name>/<name>.vis.pb --max-frames 200 --debug-render-video

# Re-run only tracking (set regenerate_raft: false in motioncap/config.yaml first)
uv run python motioncap/main.py ../recordings/<name>/<name>.vis.pb

# AI video analysis (Gemini native video upload)
uv run python genspark/main.py ../recordings/<name>/<name>.vis.pb

# 3D reconstruction: COLMAP SfM + Gaussian Splatting
uv run python reconstruct/main.py ../recordings/<name>/<name>.vis.pb
uv run python reconstruct/main.py ../recordings/<name>/<name>.vis.pb --no-splat   # COLMAP only
uv run python reconstruct/main.py ../recordings/<name>/<name>.vis.pb --max-frames 100 --sample-every 10  # quick test
uv run python reconstruct/main.py ../recordings/<name>/<name>.vis.pb --dense-mvs   # + dense MVS (requires colmap binary)

# IDOSLAM: unified trajectory + road-ground triangulation + canonical dashboard data
uv run python idoslam/main.py ../recordings/<name>/<name>.vis.pb

# Homography analysis (interactive, from server/)
uv run python ../analysis/homography/main.py ../recordings/<name>/<name>.vis.pb
```

## One-time Setup

```bash
# RAFT model weights for motioncap (downloads large ~21 MB or small ~5 MB)
uv run python motioncap/download_models.py           # large (default)
uv run python motioncap/download_models.py small     # small variant

# SAM3 (HuggingFace gated model — request access at facebook/sam3)
huggingface-cli login
```

## Module Structure

| Directory | Purpose |
|-----------|---------|
| `streamlog/` | Rust Streamlog server — live streaming, playback, dashboard WebSocket; includes a small Python `protoio` compatibility helper for analyzers |
| `segmentation/` | Offline SAM3 annotator → writes `.segmentation.pb` |
| `motioncap/` | Offline RAFT optical-flow motion heatmap → writes `.motioncap.pb` |
| `genspark/` | Offline AI video analysis (Gemini native video upload) |
| `reconstruct/` | Offline COLMAP SfM + Gaussian Splatting → writes `.recon/`, `.splat.ply`, `.recon.pb` |
| `idoslam/` | Offline SLAM pipeline → writes `.idoslam.pb` and workspace CSV/JSON artifacts consumed by the dashboard |

## Motion Capture (`motioncap/`)

Unified pipeline via `motioncap/main.py` — two sequential steps, one output file:

- **Step 1 — RAFT** (`raft_motion.py`): dense optical flow with pose-based background subtraction.
  - `subtraction_mode`: `"pose"` (best, uses ARCore depth+pose), `"consensus"` (median flow, no pose), `"absolute"` (raw flow)
  - `reference.strategy`: `"consecutive"` | `"rolling"` | `"time"` | `"first"`
  - Two-pass: pass 1 computes float16 residuals, pass 2 applies global normalisation

- **Step 2 — Tracker** (`tracker.py`): temporal consistency filter + blob detection + nearest-neighbour tracking with velocity prediction. Merges fragmented tracks and interpolates gaps.

- **Pipeline flags** (`motioncap/config.yaml` → `pipeline` section):
  - `pipeline.regenerate_raft: false` — skip RAFT, load heatmaps from existing `.motioncap.pb`
  - `pipeline.regenerate_tracking: false` — skip tracker, load tracks from existing `.motioncap.pb`
  - Use `regenerate_raft: false` to quickly re-tune tracking parameters without re-running slow RAFT

Output: `recordings/<name>/<name>.motioncap.pb` — per-frame heatmap records followed by a single tracks-summary record (`MotionCaptureResponse` with `tracks` field populated, detected via `len(resp.tracks) > 0`)

Optional: `recordings/<name>/<name>.motioncap.mp4` — heatmap overlay on RGB with coloured trajectory tails

Also available: **SIFT** (`sift_motion.py`): feature-based, no GPU required.

## Segmentation (`segmentation/`)

- **SAM3**: streaming per-frame, text/concept prompts via `--text "..."`. `trigger_type = TEXT`.
- SAM3 processes every frame for tracker continuity; only emits every `--sample-every` frames.
- Config: `segmentation/config.yaml` — `sam3.dtype`, `sam3.inference_height`, `sam3.max_num_objects`, `sam3.session_reset_frames`, `sampling.sample_every_x_frames`

Output: `recordings/<name>/<name>.segmentation.pb` (length-delimited `SegmentationResponse` protos)

## AI Analysis (`genspark/`)

Sends a `.vis.pb` recording to Gemini's native video upload API for analysis against a prompt in `genspark/prompt.md`. Requires `GEMINI_API_KEY`.

Config: `genspark/config.yaml` — video resolution/fps/duration and Gemini model settings.

## Path Convention

Python analyzer scripts set `_server_root = Path(__file__).resolve().parent.parent` and `_project_root = _server_root.parent`, then insert both into `sys.path`. This allows `from proto import perceiver_pb2` and the compatibility helper `from streamlog.protoio import ProtoIO` to work regardless of where the script is invoked from.
