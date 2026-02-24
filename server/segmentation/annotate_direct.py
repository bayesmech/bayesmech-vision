#!/usr/bin/env python3
"""
Fast standalone annotation of a .vis.pb recording using SAM2 directly.

Unlike annotate_recording.py (which sends frames over WebSocket in 20-frame
batches, re-initializing SAM2 81+ times and re-running auto-segmentation each
time), this script uses **chunked annotation with mask handoff**:

  1. Load all frames from the .vis.pb file into memory
  2. For each chunk of CHUNK_SIZE frames:
       a. Write chunk frames as JPEGs to a temp dir (parallel, fast)
       b. init_state on the chunk (~1.3 GB CPU RAM for 100 frames @ 1024px)
       c. First chunk or every --reseed-every chunks: grid point prompts on frame 0
          Other chunks: pass final masks from previous chunk as mask prompts
       d. propagate_in_video through the chunk
       e. Collect results, reset inference state
  3. Write all results to a .seg.pb file

Key advantages over annotate_recording.py (batch=20, server-based):
  - No WebSocket / HTTP overhead
  - Mask handoff preserves temporal continuity across chunks
  - VOS optimization (torch.compile) amortized over larger chunks
  - Bounded CPU RAM: CHUNK_SIZE × 12.6 MB (e.g. 100 × 12.6 = 1.3 GB)
  - 100-frame chunks → ~16 init_state calls instead of 81

Usage:
    uv run python segmentation/annotate_direct.py recordings/<name>.vis.pb
    uv run python segmentation/annotate_direct.py recordings/<name>.vis.pb --chunk 200
    uv run python segmentation/annotate_direct.py recordings/<name>.vis.pb --grid 64
    uv run python segmentation/annotate_direct.py recordings/<name>.vis.pb --variant tiny
    uv run python segmentation/annotate_direct.py recordings/<name>.vis.pb --reseed-every 3
"""

import os
import sys
import time
import tempfile
import shutil
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_server_root))

import base64
from io import BytesIO

import numpy as np
import cv2
import torch
import yaml
from PIL import Image as PILImage
from tqdm import tqdm

from proto import segmentation_pb2, perceiver_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)

# Load config for defaults
_config_path = Path(__file__).parent / "segmentation_config.yaml"
with open(_config_path) as f:
    _CONFIG = yaml.safe_load(f)

# Colors for mask PNG encoding (RGBA)
_COLORS = [
    (255, 0, 0, 128), (0, 255, 0, 128), (0, 0, 255, 128),
    (255, 255, 0, 128), (255, 0, 255, 128), (0, 255, 255, 128),
]


def seg_path(recording_path: Path) -> Path:
    name = recording_path.name
    if name.endswith(".vis.pb"):
        return recording_path.parent / (name.removesuffix(".vis.pb") + ".seg.pb")
    return recording_path.with_suffix(".seg.pb")


def decode_frame(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    """Decode an ImageFrame to a numpy RGB array."""
    img = frame.rgb_frame
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    elif img.format == ImageFormat.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        total = len(raw) // 3
        side = int(total ** 0.5)
        return raw.reshape((side, side, 3))
    else:
        raise ValueError(f"Unsupported format: {img.format}")


def write_jpeg(args):
    idx, rgb, out_dir = args
    path = os.path.join(out_dir, f"{idx:05d}.jpg")
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])


def encode_mask_png(mask: np.ndarray, obj_id: int) -> bytes:
    """Encode a boolean mask to base64 PNG bytes."""
    color = _COLORS[(int(obj_id) - 1) % len(_COLORS)]
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[mask] = color
    img = PILImage.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("recording", help="Path to .vis.pb recording")
    parser.add_argument("--chunk", type=int, default=100,
                        help="Frames per processing chunk (default: 100). "
                             "Higher = fewer SAM2 reinits but more CPU RAM. "
                             "100 frames ≈ 1.3 GB RAM.")
    parser.add_argument("--grid", type=int,
                        default=_CONFIG["streaming"].get("auto_segment_grid_size", 64),
                        help="Grid spacing for auto-segmentation prompts (pixels)")
    parser.add_argument("--max-objects", type=int,
                        default=_CONFIG["streaming"].get("max_tracked_objects", 6),
                        help="Max objects to track")
    parser.add_argument("--variant", choices=["tiny", "small", "base_plus", "large"],
                        default=_CONFIG["model"]["sam2"].get("variant", "small"),
                        help="SAM2 model variant")
    # vos_optimized uses torch.compile(mode="max-autotune") with CUDA Graphs.
    # CUDA Graphs record a fixed execution graph per input shape — resetting
    # SAM2 state between chunks corrupts the graph allocator.
    # Default: False (safe for multi-chunk). Pass --vos only for single-chunk
    # runs (very short recordings that fit in RAM).
    parser.add_argument("--vos", action="store_true", default=False,
                        help="Enable VOS torch.compile (only for single-chunk runs; "
                             "breaks with multi-chunk due to CUDA Graph state)")
    parser.add_argument("--no-vos", dest="vos", action="store_false")
    parser.add_argument("--reseed-every", type=int, default=5,
                        help="Re-run grid point prompts every N chunks instead of using "
                             "mask handoff (default: 5 = every 500 frames). "
                             "Set to 0 to disable reseeding (mask handoff only).")
    args = parser.parse_args()

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = seg_path(rec_path)

    # ── Load frames ───────────────────────────────────────────────────────
    print(f"Loading {rec_path.name}...")
    t0 = time.time()
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames in recording")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames in {time.time()-t0:.1f}s")

    print("Decoding frames...")
    t0 = time.time()
    rgb_frames = []
    for f in tqdm(frames, unit="frame", desc="Decoding"):
        rgb_frames.append(decode_frame(f))
    print(f"Decoded in {time.time()-t0:.1f}s")

    height, width = rgb_frames[0].shape[:2]
    n_chunks = (len(frames) + args.chunk - 1) // args.chunk
    chunk_ram_mb = args.chunk * 1024 * 1024 * 3 * 4 / (1024 ** 2)
    print(f"\n{len(frames)} frames → {n_chunks} chunks of {args.chunk} "
          f"(~{chunk_ram_mb:.0f} MB CPU RAM per chunk)")

    # ── Load SAM2 ─────────────────────────────────────────────────────────
    variant_map = {
        "tiny":      ("sam2.1_hiera_tiny.pt",      "configs/sam2.1/sam2.1_hiera_t.yaml"),
        "small":     ("sam2.1_hiera_small.pt",     "configs/sam2.1/sam2.1_hiera_s.yaml"),
        "base_plus": ("sam2.1_hiera_base_plus.pt", "configs/sam2.1/sam2.1_hiera_b+.yaml"),
        "large":     ("sam2.1_hiera_large.pt",     "configs/sam2.1/sam2.1_hiera_l.yaml"),
    }
    ckpt_name, cfg_name = variant_map[args.variant]
    checkpoint_path = Path(__file__).parent / "models" / "sam2" / ckpt_name

    if not checkpoint_path.exists():
        print(f"Checkpoint not found: {checkpoint_path}")
        print(f"Download: wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/{ckpt_name} "
              f"-P {checkpoint_path.parent}")
        sys.exit(1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    print(f"\nLoading SAM2 {args.variant} on {device} (vos_optimized={args.vos})...")
    t0 = time.time()
    from sam2.build_sam import build_sam2_video_predictor
    predictor = build_sam2_video_predictor(
        config_file=cfg_name,
        ckpt_path=str(checkpoint_path),
        device=device,
        vos_optimized=args.vos,
    )
    print(f"SAM2 loaded in {time.time()-t0:.1f}s")
    if torch.cuda.is_available():
        print(f"VRAM: {torch.cuda.memory_allocated()/1024**3:.2f} GB allocated")

    # ── Build grid prompts (reused for first chunk only) ──────────────────
    grid = args.grid
    margin = grid // 2
    grid_points = []
    for y in range(margin, height - margin, grid):
        for x in range(margin, width - margin, grid):
            grid_points.append([x, y])
    if len(grid_points) > args.max_objects:
        step = max(1, len(grid_points) // args.max_objects)
        grid_points = grid_points[::step][: args.max_objects]

    # ── Process chunks ────────────────────────────────────────────────────
    results: list[segmentation_pb2.SegmentationResponse] = []
    # Maps obj_id -> boolean mask from the last frame of the previous chunk
    handoff_masks: dict[int, np.ndarray] = {}

    total_t0 = time.time()
    progress = tqdm(total=len(frames), desc="Annotating", unit="frame")

    temp_dir = tempfile.mkdtemp(prefix="sam2_direct_")
    try:
        for chunk_idx in range(n_chunks):
            chunk_start = chunk_idx * args.chunk
            chunk_end = min(chunk_start + args.chunk, len(frames))
            chunk_rgb = rgb_frames[chunk_start:chunk_end]
            chunk_frames = frames[chunk_start:chunk_end]
            chunk_len = len(chunk_rgb)

            # Write this chunk's frames to temp dir (parallel)
            # Clear dir first by removing and recreating it
            shutil.rmtree(temp_dir, ignore_errors=True)
            os.makedirs(temp_dir)
            jobs = [(i, f, temp_dir) for i, f in enumerate(chunk_rgb)]
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
                list(pool.map(write_jpeg, jobs))

            # Init SAM2 on this chunk.
            # offload_video_to_cpu=True: frames stay in CPU RAM (not VRAM).
            # async_loading_frames=False: load synchronously so we don't race
            # with the background thread filling RAM during propagation.
            with torch.inference_mode():
                inference_state = predictor.init_state(
                    video_path=temp_dir,
                    offload_video_to_cpu=True,
                    async_loading_frames=False,
                )

            # Add prompts for frame 0 of this chunk.
            # Reseed with grid points on the first chunk, when tracking was lost,
            # or every --reseed-every chunks to prevent tracking drift over time.
            use_grid = (
                chunk_idx == 0
                or not handoff_masks
                or (args.reseed_every > 0 and chunk_idx % args.reseed_every == 0)
            )
            with torch.inference_mode():
                if use_grid:
                    for obj_id, point in enumerate(grid_points, start=1):
                        predictor.add_new_points_or_box(
                            inference_state=inference_state,
                            frame_idx=0,
                            obj_id=obj_id,
                            points=np.array([point], dtype=np.float32),
                            labels=np.array([1], dtype=np.int32),
                        )
                else:
                    # Subsequent chunks: pass mask from last frame of previous chunk
                    for obj_id, prev_mask in handoff_masks.items():
                        predictor.add_new_mask(
                            inference_state=inference_state,
                            frame_idx=0,
                            obj_id=obj_id,
                            mask=torch.from_numpy(prev_mask),
                        )

            # Propagate through this chunk.
            # torch.compiler.cudagraph_mark_step_begin() is required when using
            # vos_optimized=True (torch.compile + CUDA Graphs) across multiple
            # init_state/propagate calls in the same process. Without it, CUDA
            # Graphs reuse tensor buffers from the previous run causing errors.
            if hasattr(torch, "compiler") and hasattr(torch.compiler, "cudagraph_mark_step_begin"):
                torch.compiler.cudagraph_mark_step_begin()

            chunk_segments: dict[int, dict] = {}
            with torch.inference_mode():
                for out_frame_idx, out_obj_ids, out_mask_logits in \
                        predictor.propagate_in_video(inference_state):
                    chunk_segments[out_frame_idx] = {
                        int(oid): (out_mask_logits[i] > 0.0).cpu().numpy().squeeze()
                        for i, oid in enumerate(out_obj_ids)
                    }

            # Build SegmentationResponse protos for this chunk
            for frame_local_idx in sorted(chunk_segments.keys()):
                global_idx = chunk_start + frame_local_idx
                if global_idx >= len(frames):
                    continue

                masks_dict = chunk_segments[frame_local_idx]
                resp = segmentation_pb2.SegmentationResponse()
                resp.frame_identifier.CopyFrom(frames[global_idx].frame_identifier)
                resp.trigger_type = (
                    segmentation_pb2.SegmentationResponse.SegmentationTriggerType.PROPAGATION
                )

                for obj_id, mask in masks_dict.items():
                    pixel_count = int(np.sum(mask))
                    if pixel_count == 0:
                        continue
                    # Use the logit tensor for confidence
                    logit_idx = list(chunk_segments[frame_local_idx].keys()).index(obj_id)
                    mask_msg = resp.masks.add()
                    mask_msg.object_id = obj_id
                    mask_msg.pixel_count = pixel_count
                    mask_msg.confidence = 1.0  # mask is already thresholded
                    mask_msg.mask_data = encode_mask_png(mask, obj_id)

                if resp.masks:
                    results.append(resp)

            # Handoff: save masks from last frame of this chunk
            last_local = chunk_len - 1
            if last_local in chunk_segments:
                handoff_masks = {
                    obj_id: mask
                    for obj_id, mask in chunk_segments[last_local].items()
                    if np.sum(mask) > 0
                }
            else:
                handoff_masks = {}

            # Clean up inference state
            predictor.reset_state(inference_state)

            progress.update(chunk_len)
            elapsed = time.time() - total_t0
            fps = (chunk_end) / elapsed if elapsed > 0 else 0
            prompt_type = "grid" if use_grid else "handoff"
            tqdm.write(
                f"  Chunk {chunk_idx+1}/{n_chunks} [{prompt_type}]: "
                f"frames {chunk_start}-{chunk_end-1}, "
                f"{len(results)} total results, {fps:.2f} fps"
            )

    finally:
        progress.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

    total_elapsed = time.time() - total_t0
    fps = len(frames) / total_elapsed if total_elapsed > 0 else 0
    print(f"\nDone: {len(frames)} frames in {total_elapsed:.1f}s "
          f"({fps:.2f} fps, {total_elapsed/len(frames):.2f}s/frame)")

    # ── Save results ──────────────────────────────────────────────────────
    if results:
        if out_path.exists():
            out_path.unlink()
        _seg_io.write_file(out_path, results)
        total_masks = sum(len(r.masks) for r in results)
        frame_numbers_with_results = {r.frame_identifier.frame_number for r in results}
        coverage_pct = len(frame_numbers_with_results) / len(frames) * 100
        useful = sum(1 for r in results if any(m.confidence > 0 and m.pixel_count > 0 for m in r.masks))
        print(f"Wrote {len(results)} results ({total_masks} masks) to {out_path.name}")
        print(f"Coverage: {len(frame_numbers_with_results)}/{len(frames)} frames ({coverage_pct:.1f}%)")
        print(f"Useful results (non-zero masks): {useful}/{len(results)}")
    else:
        print("No segmentation results produced")


if __name__ == "__main__":
    main()
