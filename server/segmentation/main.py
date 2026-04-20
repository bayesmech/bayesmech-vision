#!/usr/bin/env python3
"""
Offline annotation of a .vis.pb recording using SAM3 text prompts.

SAM3 processes every frame for tracker continuity, but only emits annotation
results for every --sample-every frames (default from config).

Usage:
    cd server

    uv run python segmentation/main.py ../recordings/<name>.vis.pb \\
        --text "chair, person, desk"

    uv run python segmentation/main.py ../recordings/<name>.vis.pb \\
        --text "snooker table, yellow ball" --max-frames 200 --sample-every 5

SAM3 setup (one-time):
    Request access at https://huggingface.co/facebook/sam3
    Then: huggingface-cli login
"""

import argparse
import os

# Must be set before any CUDA context is created (before torch is imported).
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import struct
import sys
import time
import zlib
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

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

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as f:
    _CONFIG = yaml.safe_load(f)

_SAM3_INTERNAL_CONFIG_OVERRIDES = {
    # These are implementation details rather than product-facing knobs.
    "new_det_thresh": 0.5,
    "hotstart_delay": 5,
    "hotstart_unmatch_thresh": 3,
    "init_trk_keep_alive": 10,
    "max_trk_keep_alive": 15,
    "recondition_every_nth_frame": 9999,
}


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


# ── SAM3 backend ────────────────────────────────────────────────────────────

def run_sam3(args, frames: list, out_path: Path, sample_every: int) -> tuple[int, int]:
    """Run SAM3 streaming annotation. Returns (total_results, total_masks)."""
    sam3_cfg = _CONFIG.get("sam3", {})
    dtype_str = sam3_cfg.get("dtype", "bfloat16")
    dtype = torch.bfloat16 if dtype_str == "bfloat16" else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"

    local_model_dir = Path(__file__).parent / "models" / "sam3"
    model_source = str(local_model_dir) if local_model_dir.exists() else "facebook/sam3"

    print(f"\nLoading SAM3 on {device} (dtype={dtype_str})...")
    if model_source == "facebook/sam3":
        print("(Downloading from HuggingFace — requires approved access and huggingface-cli login)")
    else:
        print(f"(Loading from local: {local_model_dir})")
    t0 = time.time()
    from transformers import Sam3VideoConfig, Sam3VideoModel, Sam3VideoProcessor

    processor = Sam3VideoProcessor.from_pretrained(model_source)

    # Apply a small set of stable tracking overrides, plus a couple of user-facing
    # controls from config.yaml.
    config = Sam3VideoConfig.from_pretrained(model_source)
    patched = []
    for key, val in _SAM3_INTERNAL_CONFIG_OVERRIDES.items():
        if hasattr(config, key):
            setattr(config, key, val)
            patched.append(f"{key}={val}")
    max_num_objects = sam3_cfg.get("max_num_objects")
    if max_num_objects is not None and hasattr(config, "max_num_objects"):
        config.max_num_objects = max_num_objects
        patched.append(f"max_num_objects={max_num_objects}")
    if patched:
        print(f"  Config overrides: {', '.join(patched)}")

    # Use Flash Attention 2 if available (reduces attention VRAM); fall back silently.
    _fa2_kwargs = {"attn_implementation": "flash_attention_2"}
    try:
        model = Sam3VideoModel.from_pretrained(
            model_source, config=config, dtype=dtype, **_fa2_kwargs
        ).to(device)
        print("  Attention: Flash Attention 2")
    except Exception:
        model = Sam3VideoModel.from_pretrained(
            model_source, config=config, dtype=dtype
        ).to(device)
    model.eval()

    print(f"SAM3 loaded in {time.time()-t0:.1f}s")
    if torch.cuda.is_available():
        print(f"VRAM: {torch.cuda.memory_allocated()/1024**3:.2f} GB allocated")

    # Only commas delimit concepts so multi-word phrases survive intact.
    concepts = [c.strip() for c in args.text.split(",") if c.strip()]
    infer_h = sam3_cfg.get("inference_height")
    session_reset_frames = sam3_cfg.get("session_reset_frames", 100)
    print(f"\nConcepts: {concepts}  (sampling every {sample_every} frames)")
    if session_reset_frames:
        print(f"Session reset every {session_reset_frames} frames to bound VRAM growth")
    else:
        print("Session reset disabled")
    if infer_h:
        print(f"Downsampling frames to height={infer_h}px before inference")

    def _new_session():
        sess = processor.init_video_session(
            inference_device=device,
            processing_device="cpu",
            video_storage_device="cpu",
            dtype=dtype,
        )
        processor.add_text_prompt(sess, concepts)
        return sess

    session = _new_session()
    frames_in_session = 0

    total_results = 0
    total_masks = 0
    batch: list[segmentation_pb2.SegmentationResponse] = []
    total_t0 = time.time()

    with tqdm(total=len(frames), desc="Annotating (SAM3)", unit="frame") as progress:
        for global_idx, proto_frame in enumerate(frames):
            # Periodically reset session to free accumulated per-object VRAM
            if session_reset_frames and frames_in_session >= session_reset_frames:
                del session
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                session = _new_session()
                frames_in_session = 0

            rgb = decode_frame_rgb(proto_frame)
            if infer_h:
                h, w = rgb.shape[:2]
                infer_w = int(w * infer_h / h)
                rgb = cv2.resize(rgb, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            inputs = processor(images=Image.fromarray(rgb), return_tensors="pt").to(device)

            # Release fragmented reserved-but-unallocated CUDA memory before each
            # forward pass so the NMS allocation spike has the full budget available.
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            with torch.inference_mode():
                raw_out = model(
                    inference_session=session,
                    frame=inputs.pixel_values[0],
                )
            outputs = processor.postprocess_outputs(
                session, raw_out, original_sizes=inputs.original_sizes,
            )
            frames_in_session += 1

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
            # Invert prompt_to_obj_ids → obj_id_to_label for O(1) lookup per mask
            obj_id_to_label = {
                oid: label
                for label, oids in outputs.get("prompt_to_obj_ids", {}).items()
                for oid in oids
            }

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
                m.label = obj_id_to_label.get(int(obj_id), "")

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

    parser = argparse.ArgumentParser(
        description="Annotate a .vis.pb recording with SAM3 text-prompted segmentation masks")
    parser.add_argument("recording", help="Path to .vis.pb recording")
    parser.add_argument("--sample-every", type=int, default=cfg_sample,
                        help=f"Emit annotations every N frames (default: {cfg_sample})")
    parser.add_argument("--write-every", type=int, default=50,
                        help="Flush results to disk every N frames (default: 50)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Limit to first N frames for testing (0 = all)")
    parser.add_argument("--text", required=True,
                        help="Required comma-separated concepts; spaces inside each concept are preserved")
    parser.add_argument("--score-thresh", type=float, default=0.5,
                        help="Min detection confidence (default: 0.5)")

    args = parser.parse_args()

    if args.sample_every < 1:
        parser.error("--sample-every must be >= 1")
    if not args.text.strip():
        parser.error("--text must not be empty")

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
    total_results, total_masks = run_sam3(args, frames, out_path, args.sample_every)

    total_elapsed = time.time() - total_t0
    fps = len(frames) / total_elapsed if total_elapsed > 0 else 0
    sampled = max(1, (len(frames) + args.sample_every - 1) // args.sample_every)
    print(f"\nDone: {len(frames)} frames in {total_elapsed:.1f}s ({fps:.2f} fps)")
    print(f"Sampled: {total_results}/{sampled} frames with masks "
          f"({total_masks} total masks) → {out_path.name}")


if __name__ == "__main__":
    main()
