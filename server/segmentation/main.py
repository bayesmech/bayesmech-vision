#!/usr/bin/env python3
"""
Offline annotation of a .vis.pb recording using SAM2 or SAM3.

Select the model backend with --model:
  sam2  Grid-based point prompts; chunked processing; SAM2 tiny/small/base_plus/large
  sam3  Text/concept prompts; streaming per-frame; requires HuggingFace access

Both backends always process every frame for tracking continuity, but only emit
annotation results for every --sample-every frames (default from config).

Usage:
    cd server

    # SAM3 (text prompts)
    uv run python segmentation/main.py ../recordings/<name>.vis.pb \\
        --model sam3 --text "chair person desk"

    # SAM2 (auto-grid)
    uv run python segmentation/main.py ../recordings/<name>.vis.pb \\
        --model sam2 --variant small

    # Testing / subsampling
    uv run python segmentation/main.py ../recordings/<name>.vis.pb \\
        --model sam3 --text "object" --max-frames 200 --sample-every 5

SAM3 setup (one-time):
    Request access at https://huggingface.co/facebook/sam3
    Then: huggingface-cli login

SAM2 setup (one-time):
    wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt \\
      -P server/segmentation/models/sam2/
"""

import argparse
import os
import struct
import sys
import time
import zlib
from collections import OrderedDict
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_server_root))

import cv2
import numpy as np
import torch
import yaml
from PIL import Image
from tqdm import tqdm

from proto import segmentation_pb2, perceiver_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)

_config_path = Path(__file__).parent / "segmentation_config.yaml"
with open(_config_path) as f:
    _CONFIG = yaml.safe_load(f)

# ImageNet normalization constants (SAM2)
_IMG_MEAN = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32)[:, None, None]
_IMG_STD = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32)[:, None, None]


def seg_path(recording_path: Path) -> Path:
    name = recording_path.name
    if name.endswith(".vis.pb"):
        return recording_path.parent / (name.removesuffix(".vis.pb") + ".seg.pb")
    return recording_path.with_suffix(".seg.pb")


# ── Frame decoding ──────────────────────────────────────────────────────────

def decode_frame_rgb(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    """Decode an ImageFrame to a numpy RGB array (original resolution)."""
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


# ── Mask encoding ───────────────────────────────────────────────────────────

def encode_mask_compressed(mask: np.ndarray) -> bytes:
    """Encode boolean mask as compressed binary.

    Format: [height: uint32_le][width: uint32_le][zlib(np.packbits(mask.flatten()))]
    """
    h, w = mask.shape
    header = struct.pack("<II", h, w)
    packed = np.packbits(mask.ravel())
    compressed = zlib.compress(packed.tobytes(), level=1)
    return header + compressed


# ── SAM2 helpers ────────────────────────────────────────────────────────────

def rgb_to_sam2_tensor(rgb: np.ndarray, image_size: int) -> torch.Tensor:
    resized = cv2.resize(rgb, (image_size, image_size), interpolation=cv2.INTER_LINEAR)
    t = torch.from_numpy(resized).permute(2, 0, 1).float() / 255.0
    return (t - _IMG_MEAN) / _IMG_STD


class InMemoryImageLoader:
    """SAM2-compatible frame loader serving pre-processed tensors."""

    def __init__(self, proto_frames, image_size: int, offload_to_cpu: bool,
                 device: torch.device):
        self.images = []
        first_rgb = decode_frame_rgb(proto_frames[0])
        self.video_height, self.video_width = first_rgb.shape[:2]
        for pf in proto_frames:
            t = rgb_to_sam2_tensor(decode_frame_rgb(pf), image_size)
            if not offload_to_cpu:
                t = t.to(device, non_blocking=True)
            self.images.append(t)

    def __getitem__(self, index):
        return self.images[index]

    def __len__(self):
        return len(self.images)


def build_inference_state(predictor, image_loader: InMemoryImageLoader,
                          offload_video_to_cpu=True, offload_state_to_cpu=False):
    """Build SAM2 inference_state from in-memory tensors."""
    device = predictor.device
    inference_state = {
        "images": image_loader,
        "num_frames": len(image_loader),
        "offload_video_to_cpu": offload_video_to_cpu,
        "offload_state_to_cpu": offload_state_to_cpu,
        "video_height": image_loader.video_height,
        "video_width": image_loader.video_width,
        "device": device,
        "storage_device": torch.device("cpu") if offload_state_to_cpu else device,
        "point_inputs_per_obj": {},
        "mask_inputs_per_obj": {},
        "cached_features": {},
        "constants": {},
        "obj_id_to_idx": OrderedDict(),
        "obj_idx_to_id": OrderedDict(),
        "obj_ids": [],
        "output_dict_per_obj": {},
        "temp_output_dict_per_obj": {},
        "frames_tracked_per_obj": {},
    }
    with torch.inference_mode():
        predictor._get_image_feature(inference_state, frame_idx=0, batch_size=1)
    return inference_state


# ── SAM2 backend ────────────────────────────────────────────────────────────

def run_sam2(args, frames: list, out_path: Path, sample_every: int) -> tuple[int, int]:
    """Run SAM2 chunked annotation. Returns (total_results, total_masks)."""
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
        print(f"Download: wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/{ckpt_name}"
              f" -P {checkpoint_path.parent}")
        sys.exit(1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    print(f"\nLoading SAM2 {args.variant} on {device} (vos_optimized={args.vos})...")
    t0 = time.time()
    from sam2.build_sam import build_sam2_video_predictor
    predictor = build_sam2_video_predictor(
        config_file=cfg_name, ckpt_path=str(checkpoint_path),
        device=device, vos_optimized=args.vos,
    )
    image_size = predictor.image_size
    print(f"SAM2 loaded in {time.time()-t0:.1f}s (image_size={image_size})")
    if torch.cuda.is_available():
        print(f"VRAM: {torch.cuda.memory_allocated()/1024**3:.2f} GB allocated")

    first_rgb = decode_frame_rgb(frames[0])
    height, width = first_rgb.shape[:2]
    del first_rgb

    n_chunks = (len(frames) + args.chunk - 1) // args.chunk
    print(f"\n{len(frames)} frames → {n_chunks} chunks of {args.chunk}"
          f"  (sampling every {sample_every} frames)")

    # Build grid prompts
    grid, margin = args.grid, args.grid // 2
    grid_points = []
    for y in range(margin, height - margin, grid):
        for x in range(margin, width - margin, grid):
            grid_points.append([x, y])
    if len(grid_points) > args.max_objects:
        step = max(1, len(grid_points) // args.max_objects)
        grid_points = grid_points[::step][:args.max_objects]

    handoff_masks: dict[int, np.ndarray] = {}
    total_results = 0
    total_masks = 0
    batch: list[segmentation_pb2.SegmentationResponse] = []
    total_t0 = time.time()
    progress = tqdm(total=len(frames), desc="Annotating (SAM2)", unit="frame")

    for chunk_idx in range(n_chunks):
        chunk_start = chunk_idx * args.chunk
        chunk_end = min(chunk_start + args.chunk, len(frames))
        chunk_protos = frames[chunk_start:chunk_end]
        chunk_len = len(chunk_protos)

        loader = InMemoryImageLoader(chunk_protos, image_size, True, predictor.device)
        with torch.inference_mode():
            inference_state = build_inference_state(predictor, loader)

        use_grid = (
            chunk_idx == 0
            or not handoff_masks
            or (args.reseed_every > 0 and chunk_idx % args.reseed_every == 0)
        )
        with torch.inference_mode():
            if use_grid:
                for obj_id, point in enumerate(grid_points, start=1):
                    predictor.add_new_points_or_box(
                        inference_state=inference_state, frame_idx=0, obj_id=obj_id,
                        points=np.array([point], dtype=np.float32),
                        labels=np.array([1], dtype=np.int32),
                    )
            else:
                for obj_id, prev_mask in handoff_masks.items():
                    predictor.add_new_mask(
                        inference_state=inference_state, frame_idx=0, obj_id=obj_id,
                        mask=torch.from_numpy(prev_mask),
                    )

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

        for frame_local_idx in sorted(chunk_segments.keys()):
            global_idx = chunk_start + frame_local_idx
            if global_idx >= len(frames):
                continue
            if global_idx % sample_every != 0:  # sampling gate
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
                m = resp.masks.add()
                m.object_id = obj_id
                m.pixel_count = pixel_count
                m.confidence = 1.0
                m.mask_data = encode_mask_compressed(mask)
            if resp.masks:
                batch.append(resp)
                total_results += 1
                total_masks += len(resp.masks)

        if len(batch) >= args.write_every:
            _seg_io.write_file(out_path, batch)
            batch.clear()

        last_local = chunk_len - 1
        handoff_masks = {
            obj_id: mask
            for obj_id, mask in chunk_segments.get(last_local, {}).items()
            if np.sum(mask) > 0
        }

        predictor.reset_state(inference_state)
        del inference_state, loader, chunk_segments
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        progress.update(chunk_len)
        elapsed = time.time() - total_t0
        fps = chunk_end / elapsed if elapsed > 0 else 0
        tqdm.write(
            f"  Chunk {chunk_idx+1}/{n_chunks} [{'grid' if use_grid else 'handoff'}]: "
            f"frames {chunk_start}-{chunk_end-1}, {total_results} results, {fps:.2f} fps"
        )

    progress.close()
    if batch:
        _seg_io.write_file(out_path, batch)

    return total_results, total_masks


# ── SAM3 backend ────────────────────────────────────────────────────────────

def run_sam3(args, frames: list, out_path: Path, sample_every: int) -> tuple[int, int]:
    """Run SAM3 streaming annotation. Returns (total_results, total_masks)."""
    dtype_str = _CONFIG["model"]["sam3"].get("dtype", "bfloat16")
    dtype = torch.bfloat16 if dtype_str == "bfloat16" else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"

    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    local_model_dir = Path(__file__).parent / "models" / "sam3"
    model_source = str(local_model_dir) if local_model_dir.exists() else "facebook/sam3"

    print(f"\nLoading SAM3 on {device} (dtype={dtype_str})...")
    if model_source == "facebook/sam3":
        print("(Downloading from HuggingFace — requires approved access and huggingface-cli login)")
    else:
        print(f"(Loading from local: {local_model_dir})")
    t0 = time.time()
    from transformers import Sam3VideoModel, Sam3VideoProcessor
    processor = Sam3VideoProcessor.from_pretrained(model_source)
    model = Sam3VideoModel.from_pretrained(model_source).to(device, dtype=dtype)
    model.eval()
    print(f"SAM3 loaded in {time.time()-t0:.1f}s")
    if torch.cuda.is_available():
        print(f"VRAM: {torch.cuda.memory_allocated()/1024**3:.2f} GB allocated")

    # Comma-separated allows multi-word concepts ("chess pawn, chess king");
    # space-separated works for single-word concepts ("pawn king queen").
    if "," in args.text:
        concepts = [c.strip() for c in args.text.split(",") if c.strip()]
    else:
        concepts = args.text.split()
    infer_h = _CONFIG["model"]["sam3"].get("inference_height")
    print(f"\nConcepts: {concepts}  (sampling every {sample_every} frames)")
    if infer_h:
        print(f"Downsampling frames to height={infer_h}px before inference")

    session = processor.init_video_session(
        inference_device=device,
        processing_device="cpu",
        video_storage_device="cpu",
        dtype=dtype,
    )
    processor.add_text_prompt(session, concepts)

    total_results = 0
    total_masks = 0
    batch: list[segmentation_pb2.SegmentationResponse] = []
    total_t0 = time.time()

    with tqdm(total=len(frames), desc="Annotating (SAM3)", unit="frame") as progress:
        for global_idx, proto_frame in enumerate(frames):
            rgb = decode_frame_rgb(proto_frame)
            if infer_h:
                h, w = rgb.shape[:2]
                infer_w = int(w * infer_h / h)
                rgb = cv2.resize(rgb, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            inputs = processor(images=Image.fromarray(rgb), return_tensors="pt").to(device)

            with torch.inference_mode():
                raw_out = model(
                    inference_session=session,
                    frame=inputs.pixel_values[0],
                )
            outputs = processor.postprocess_outputs(
                session, raw_out, original_sizes=inputs.original_sizes,
            )

            if global_idx % sample_every != 0:  # sampling gate
                progress.update(1)
                continue

            resp = segmentation_pb2.SegmentationResponse()
            resp.frame_identifier.CopyFrom(proto_frame.frame_identifier)
            resp.trigger_type = (
                segmentation_pb2.SegmentationResponse.SegmentationTriggerType.TEXT
            )

            obj_ids = outputs["object_ids"].tolist() if hasattr(outputs["object_ids"], "tolist") else list(outputs["object_ids"])
            scores = outputs["scores"]
            masks = outputs["masks"]

            for i, obj_id in enumerate(obj_ids):
                score = float(scores[i])
                if score < args.score_thresh:
                    continue
                mask = masks[i].cpu().numpy().astype(bool)
                if not mask.any():
                    continue
                m = resp.masks.add()
                m.object_id = int(obj_id)
                m.pixel_count = int(mask.sum())
                m.confidence = score
                m.mask_data = encode_mask_compressed(mask)

            if resp.masks:
                batch.append(resp)
                total_results += 1
                total_masks += len(resp.masks)

            if len(batch) >= args.write_every:
                _seg_io.write_file(out_path, batch)
                batch.clear()

            progress.update(1)
            if (global_idx + 1) % 50 == 0:
                elapsed = time.time() - total_t0
                fps = (global_idx + 1) / elapsed if elapsed > 0 else 0
                tqdm.write(
                    f"  Frame {global_idx+1}/{len(frames)}  "
                    f"{total_results} results  {fps:.2f} fps"
                )

    if batch:
        _seg_io.write_file(out_path, batch)

    return total_results, total_masks


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    cfg_sample = _CONFIG.get("sampling", {}).get("sample_every_x_frames", 1)
    cfg_sam2 = _CONFIG.get("model", {}).get("sam2", {})

    parser = argparse.ArgumentParser(
        description="Annotate a .vis.pb recording with SAM2 or SAM3 segmentation masks")
    parser.add_argument("recording", help="Path to .vis.pb recording")

    # Shared
    parser.add_argument("--model", choices=["sam2", "sam3"], default="sam3",
                        help="Segmentation backend (default: sam3)")
    parser.add_argument("--sample-every", type=int, default=cfg_sample,
                        help=f"Emit annotations every N frames (default: {cfg_sample})")
    parser.add_argument("--write-every", type=int, default=50,
                        help="Flush results to disk every N frames (default: 50)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Limit to first N frames for testing (0 = all)")

    # SAM3-only
    sam3 = parser.add_argument_group("SAM3 options")
    sam3.add_argument("--text", default="object",
                      help="Space or comma-separated concepts (default: \"object\")")
    sam3.add_argument("--score-thresh", type=float, default=0.5,
                      help="Min detection confidence (default: 0.5)")

    # SAM2-only
    sam2 = parser.add_argument_group("SAM2 options")
    sam2.add_argument("--variant", choices=["tiny", "small", "base_plus", "large"],
                      default=cfg_sam2.get("variant", "small"),
                      help="SAM2 model variant (default: small)")
    sam2.add_argument("--chunk", type=int, default=100,
                      help="Frames per SAM2 processing chunk (default: 100)")
    sam2.add_argument("--grid", type=int, default=64,
                      help="Grid spacing for point prompts in px (default: 64)")
    sam2.add_argument("--max-objects", type=int, default=6,
                      help="Max objects to track (default: 6)")
    sam2.add_argument("--reseed-every", type=int, default=5,
                      help="Re-run grid prompts every N chunks (default: 5)")
    sam2.add_argument("--vos", action="store_true", default=False,
                      help="Enable SAM2 torch.compile optimization")

    args = parser.parse_args()

    if args.sample_every < 1:
        parser.error("--sample-every must be >= 1")

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = seg_path(rec_path)

    # Load frames
    print(f"Loading {rec_path.name}...")
    t0 = time.time()
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames in recording")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames in {time.time()-t0:.1f}s")

    if args.max_frames > 0 and len(frames) > args.max_frames:
        frames = frames[:args.max_frames]
        print(f"Limiting to first {args.max_frames} frames")

    # Clear output file
    if out_path.exists():
        out_path.unlink()

    total_t0 = time.time()

    if args.model == "sam2":
        total_results, total_masks = run_sam2(args, frames, out_path, args.sample_every)
    else:
        total_results, total_masks = run_sam3(args, frames, out_path, args.sample_every)

    total_elapsed = time.time() - total_t0
    fps = len(frames) / total_elapsed if total_elapsed > 0 else 0
    sampled = max(1, (len(frames) + args.sample_every - 1) // args.sample_every)
    print(f"\nDone: {len(frames)} frames in {total_elapsed:.1f}s ({fps:.2f} fps)")
    print(f"Sampled: {total_results}/{sampled} frames with masks "
          f"({total_masks} total masks) → {out_path.name}")


if __name__ == "__main__":
    main()
