#!/usr/bin/env python3
"""
3D Reconstruction pipeline: COLMAP SfM + optional Gaussian Splatting.

Reads a .vis.pb recording, extracts frames, runs COLMAP for 3D reconstruction,
and optionally trains a 3D Gaussian Splatting model.

Note: ARCore poses from the recording are NOT used for reconstruction —
COLMAP runs full SfM (feature extraction + matching + bundle adjustment)
for reliable, drift-free camera pose estimation.

Usage
-----
    cd server
    uv run python reconstruct/main.py ../recordings/<name>.vis.pb
    uv run python reconstruct/main.py ../recordings/<name>.vis.pb --no-splat
    uv run python reconstruct/main.py ../recordings/<name>.vis.pb --no-colmap  # reuse existing workspace
    uv run python reconstruct/main.py ../recordings/<name>.vis.pb --max-frames 100 --sample-every 10
    uv run python reconstruct/main.py ../recordings/<name>.vis.pb --dense-mvs
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2
from streamlog.protoio import ProtoIO
from reconstruct.colmap_bridge import extract_frames, run_colmap_sfm, run_dense_mvs
from reconstruct.export import write_recon_pb
from reconstruct.gsplat_trainer import render_video

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="COLMAP + Gaussian Splatting pipeline")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording file")
    p.add_argument("--sample-every", type=int, default=None,
                   help="Use every Nth frame (overrides config)")
    p.add_argument("--max-frames", type=int, default=None,
                   help="Maximum frames to extract (overrides config)")
    p.add_argument("--no-colmap", action="store_true",
                   help="Skip COLMAP — reuse existing workspace")
    p.add_argument("--no-splat", action="store_true",
                   help="Skip Gaussian Splatting training")
    p.add_argument("--dense-mvs", action="store_true",
                   help="Run dense MVS after COLMAP sparse reconstruction")
    p.add_argument("--max-steps", type=int, default=None,
                   help="Gaussian Splatting training steps (overrides config)")
    p.add_argument("--data-factor", type=int, default=None,
                   help="Image downsample factor for 3DGS training (overrides config)")
    p.add_argument("--render-video", action="store_true",
                   help="After training, render the trained splat along COLMAP camera path → .splat.mp4")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = _CONFIG

    vis_path = args.recording.resolve()
    if not vis_path.exists():
        log.error("Recording not found: %s", vis_path)
        sys.exit(1)

    # Workspace lives next to the recording: <name>.recon/
    workspace = vis_path.with_suffix("").with_suffix(".recon")

    # Apply CLI overrides
    if args.sample_every is not None:
        cfg["extraction"]["sample_every"] = args.sample_every
    if args.max_frames is not None:
        cfg["extraction"]["max_frames"] = args.max_frames
    if args.max_steps is not None:
        cfg["gaussian_splatting"]["max_steps"] = args.max_steps
    if args.data_factor is not None:
        cfg["gaussian_splatting"]["data_factor"] = args.data_factor
    if args.no_colmap:
        cfg["pipeline"]["run_colmap"] = False
    if args.no_splat:
        cfg["pipeline"]["run_gaussian_splatting"] = False
    if args.dense_mvs:
        cfg["pipeline"]["run_dense_mvs"] = True

    t_start = time.time()

    # ── Step 1: Extract frames ────────────────────────────────────────────────
    log.info("=== Step 1: Reading %s", vis_path.name)
    frames = _frame_io.read_file(vis_path)
    log.info("Loaded %d total frames", len(frames))

    sample_every = cfg["extraction"]["sample_every"]
    max_frames = cfg["extraction"]["max_frames"]
    frames = frames[::sample_every]
    if max_frames > 0:
        frames = frames[:max_frames]
    log.info("Using %d frames (sample_every=%d, max_frames=%d)",
             len(frames), sample_every, max_frames or len(frames))

    images_dir = extract_frames(frames, workspace, cfg)
    log.info("Extracted %d images to %s", len(list(images_dir.iterdir())), images_dir)

    # ── Step 2: COLMAP SfM ────────────────────────────────────────────────────
    sparse_dir = workspace / "sparse" / "0"
    n_registered = 0
    if cfg["pipeline"]["run_colmap"]:
        log.info("=== Step 2: COLMAP SfM")
        n_registered = run_colmap_sfm(workspace, cfg)
        if n_registered == 0:
            log.error("COLMAP failed to register any images. Check the extracted frames.")
            sys.exit(1)
        log.info("COLMAP registered %d images", n_registered)
    else:
        if not sparse_dir.exists():
            log.error("--no-colmap specified but no sparse model at %s", sparse_dir)
            sys.exit(1)
        log.info("Skipping COLMAP, using existing workspace at %s", workspace)

    # ── Step 3: Dense MVS (optional) ─────────────────────────────────────────
    if cfg["pipeline"]["run_dense_mvs"]:
        log.info("=== Step 3: Dense MVS")
        run_dense_mvs(workspace, cfg)

    # ── Step 4: Gaussian Splatting ────────────────────────────────────────────
    if cfg["pipeline"]["run_gaussian_splatting"]:
        log.info("=== Step 4: Gaussian Splatting")
        try:
            from reconstruct.gsplat_trainer import train_splat
        except ImportError as e:
            log.error("gsplat not installed: %s\nInstall with: uv pip install gsplat "
                      "--index-url https://docs.gsplat.studio/whl/pt27cu126", e)
            sys.exit(1)

        ply_path = vis_path.with_suffix("").with_suffix(".splat.ply")
        train_splat(workspace, ply_path, cfg)
        log.info("Saved Gaussian Splatting model to %s", ply_path)

    # ── Step 4b: Render video ─────────────────────────────────────────────────
    ply_path = vis_path.with_suffix("").with_suffix(".splat.ply")  # noqa: F841 (may differ from above)
    if args.render_video and ply_path.exists():
        log.info("=== Step 4b: Rendering video")
        # Free training VRAM (optimizer states, gradients) before rendering
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        video_path = vis_path.with_suffix("").with_suffix(".splat.mp4")
        render_video(workspace, ply_path, video_path, cfg)
        log.info("Saved render video to %s", video_path)

    elapsed = time.time() - t_start

    # ── Step 5: Write summary proto ───────────────────────────────────────
    splat_ply = vis_path.with_suffix("").with_suffix(".splat.ply") \
                if cfg["pipeline"]["run_gaussian_splatting"] else None
    recon_pb = write_recon_pb(
        vis_path=vis_path,
        workspace=workspace,
        first_frame=frames[0],
        num_total_images=len(frames),
        num_registered_images=n_registered if cfg["pipeline"]["run_colmap"] else 0,
        frames_extracted=len(list((workspace / "images").glob("*.jpg"))),
        sample_every=cfg["extraction"]["sample_every"],
        cfg=cfg,
        elapsed=elapsed,
        splat_ply=splat_ply,
    )
    log.info("=== Done in %.1fs. Workspace: %s  Summary: %s",
             elapsed, workspace, recon_pb)


if __name__ == "__main__":
    main()
