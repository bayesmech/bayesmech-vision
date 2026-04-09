"""
COLMAP SfM pipeline for .vis.pb recordings.

Runs full COLMAP Structure-from-Motion: feature extraction → sequential
matching → incremental mapper → bundle adjustment.

ARCore poses are intentionally NOT used to seed COLMAP, because ARCore's
visual-inertial odometry accumulates drift over long sequences and its
coordinate scale/orientation may differ from COLMAP's output.  COLMAP
performs independent, drift-free pose estimation from the pixel data.

For scale recovery and downstream use, the ARCore depth maps stored in the
recording remain available to callers.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2
from motioncap.geometry import decode_frame_rgb, build_K

log = logging.getLogger(__name__)


# ── Frame extraction ──────────────────────────────────────────────────────────

def extract_frames(
    frames: list[perceiver_pb2.PerceiverDataFrame],
    workspace: Path,
    cfg: dict,
) -> Path:
    """
    Decode RGB frames and save as JPEG images to <workspace>/images/.

    Returns the images directory path.
    """
    images_dir = workspace / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    quality = cfg["extraction"]["image_quality"]
    k_cache: list[np.ndarray] = []

    # Build a padded filename length based on total count
    pad = len(str(len(frames)))

    for i, frame in enumerate(frames):
        try:
            rgb = decode_frame_rgb(frame)
        except (ValueError, Exception) as exc:
            log.warning("Frame %d: could not decode RGB (%s), skipping", i, exc)
            continue

        # Cache intrinsics from first frame with valid data
        build_K(frame, k_cache)

        img_path = images_dir / f"frame_{i:0{pad}d}.jpg"
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(img_path), bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])

    log.info("Extracted %d images to %s", len(list(images_dir.glob("*.jpg"))), images_dir)
    return images_dir


# ── COLMAP SfM ────────────────────────────────────────────────────────────────

def _colmap_cmd(binary: str, subcommand: str, **kwargs) -> list[str]:
    """Build a COLMAP CLI command list from keyword arguments."""
    cmd = [binary, subcommand]
    for key, val in kwargs.items():
        cmd += [f"--{key}", str(val)]
    return cmd


def run_colmap_sfm(workspace: Path, cfg: dict) -> int:
    """
    Run the full COLMAP SfM pipeline inside *workspace*:
      1. feature_extractor   — detect + describe SIFT features
      2. sequential_matcher / exhaustive_matcher
      3. mapper              — incremental SfM + bundle adjustment

    Returns the number of registered images (0 on failure).

    Requires pycolmap to be installed for reading the final model.
    pycolmap is also used in programmatic mode when available; falls back
    to the 'colmap' CLI binary otherwise.
    """
    try:
        import pycolmap
        _run_colmap_via_pycolmap(workspace, cfg, pycolmap)
    except ImportError:
        log.warning("pycolmap not found; falling back to 'colmap' CLI binary")
        binary = cfg["pipeline"].get("colmap_binary", "colmap")
        _run_colmap_via_cli(workspace, cfg, binary)

    # Count registered images
    sparse_dir = workspace / "sparse" / "0"
    if not sparse_dir.exists():
        return 0
    try:
        import pycolmap
        recon = pycolmap.Reconstruction(str(sparse_dir))
        return recon.num_reg_images()
    except Exception:
        # Fall back to counting images.bin / images.txt
        images_bin = sparse_dir / "images.bin"
        images_txt = sparse_dir / "images.txt"
        if images_bin.exists():
            return _count_colmap_images_bin(images_bin)
        if images_txt.exists():
            return _count_colmap_images_txt(images_txt)
        return 0


def _run_colmap_via_pycolmap(workspace: Path, cfg: dict, pycolmap) -> None:
    """Drive COLMAP SfM through pycolmap's Python API (pycolmap >= 3.10)."""
    database_path = workspace / "database.db"
    images_dir = workspace / "images"
    sparse_dir = workspace / "sparse"
    sparse_dir.mkdir(exist_ok=True)

    colmap_cfg = cfg["colmap"]
    fe_cfg = colmap_cfg["feature_extraction"]
    mapper_cfg = colmap_cfg["mapper"]

    # ── Feature extraction ─────────────────────────────────────────────────
    log.info("  COLMAP: feature extraction")
    reader_opts = pycolmap.ImageReaderOptions()
    reader_opts.camera_model = colmap_cfg["camera_model"]

    extraction_opts = pycolmap.FeatureExtractionOptions()
    extraction_opts.max_image_size = fe_cfg["max_image_size"]
    extraction_opts.sift.max_num_features = fe_cfg["max_num_features"]

    pycolmap.extract_features(
        database_path=str(database_path),
        image_path=str(images_dir),
        camera_mode=pycolmap.CameraMode.SINGLE if colmap_cfg["single_camera"]
                    else pycolmap.CameraMode.PER_FOLDER,
        reader_options=reader_opts,
        extraction_options=extraction_opts,
    )

    # ── Feature matching ───────────────────────────────────────────────────
    matcher = colmap_cfg["matcher"]
    overlap = colmap_cfg.get("sequential_overlap", 10)
    log.info("  COLMAP: %s matching (overlap=%d)", matcher, overlap)
    if matcher == "sequential":
        pairing_opts = pycolmap.SequentialPairingOptions()
        pairing_opts.overlap = overlap
        pycolmap.match_sequential(
            database_path=str(database_path),
            pairing_options=pairing_opts,
        )
    else:
        pycolmap.match_exhaustive(database_path=str(database_path))

    # ── Incremental mapper (SfM) ───────────────────────────────────────────
    log.info("  COLMAP: incremental mapper")
    pipeline_opts = pycolmap.IncrementalPipelineOptions()
    pipeline_opts.min_num_matches = mapper_cfg["min_num_matches"]
    pipeline_opts.mapper.init_min_num_inliers = mapper_cfg["init_min_num_inliers"]
    pipeline_opts.mapper.abs_pose_min_num_inliers = mapper_cfg["abs_pose_min_num_inliers"]
    pipeline_opts.mapper.abs_pose_min_inlier_ratio = mapper_cfg["abs_pose_min_inlier_ratio"]

    maps = pycolmap.incremental_mapping(
        database_path=str(database_path),
        image_path=str(images_dir),
        output_path=str(sparse_dir),
        options=pipeline_opts,
    )
    if not maps:
        log.warning("  COLMAP mapper produced no reconstructions")
        return

    # Take the largest reconstruction and write it to sparse/0/
    best = max(maps.values(), key=lambda r: r.num_reg_images())
    out_dir = sparse_dir / "0"
    out_dir.mkdir(exist_ok=True)
    best.write(str(out_dir))
    log.info("  COLMAP: registered %d images, %d 3D points",
             best.num_reg_images(), best.num_points3D())


def _run_colmap_via_cli(workspace: Path, cfg: dict, binary: str) -> None:
    """Drive COLMAP SfM by calling the 'colmap' CLI binary via subprocess."""
    if not shutil.which(binary):
        raise RuntimeError(
            f"COLMAP binary '{binary}' not found on PATH. "
            "Install COLMAP: https://colmap.github.io/install.html"
        )

    database_path = workspace / "database.db"
    images_dir = workspace / "images"
    sparse_dir = workspace / "sparse"
    sparse_dir.mkdir(exist_ok=True)

    colmap_cfg = cfg["colmap"]
    fe_cfg = colmap_cfg["feature_extraction"]
    mapper_cfg = colmap_cfg["mapper"]

    def run(cmd: list[str]) -> None:
        log.info("  $ %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("COLMAP failed:\n%s", result.stderr[-2000:])
            raise RuntimeError(f"COLMAP command failed: {cmd[1]}")
        if result.stdout:
            log.debug(result.stdout[-1000:])

    camera_params = (
        f"--ImageReader.camera_model={colmap_cfg['camera_model']} "
        f"--ImageReader.single_camera={'1' if colmap_cfg['single_camera'] else '0'}"
    )

    run(_colmap_cmd(binary, "feature_extractor",
        **{"database_path": database_path,
           "image_path": images_dir,
           "ImageReader.camera_model": colmap_cfg["camera_model"],
           "ImageReader.single_camera": "1" if colmap_cfg["single_camera"] else "0",
           "SiftExtraction.max_image_size": fe_cfg["max_image_size"],
           "SiftExtraction.max_num_features": fe_cfg["max_num_features"]}))

    if colmap_cfg["matcher"] == "sequential":
        run(_colmap_cmd(binary, "sequential_matcher",
            **{"database_path": database_path,
               "SequentialMatching.overlap": colmap_cfg.get("sequential_overlap", 10)}))
    else:
        run(_colmap_cmd(binary, "exhaustive_matcher",
            **{"database_path": database_path}))

    run(_colmap_cmd(binary, "mapper",
        **{"database_path": database_path,
           "image_path": images_dir,
           "output_path": sparse_dir,
           "Mapper.min_num_matches": mapper_cfg["min_num_matches"],
           "Mapper.init_min_num_inliers": mapper_cfg["init_min_num_inliers"],
           "Mapper.abs_pose_min_num_inliers": mapper_cfg["abs_pose_min_num_inliers"],
           "Mapper.abs_pose_min_inlier_ratio": mapper_cfg["abs_pose_min_inlier_ratio"]}))


# ── Dense MVS (optional) ──────────────────────────────────────────────────────

def run_dense_mvs(workspace: Path, cfg: dict) -> Path:
    """
    Run dense Multi-View Stereo using the COLMAP CLI binary.
    Requires COLMAP compiled with CUDA (dense MVS is not in pycolmap).

    Returns the path to the fused dense.ply point cloud.
    """
    binary = cfg["pipeline"].get("colmap_binary", "colmap")
    if not shutil.which(binary):
        raise RuntimeError(
            f"Dense MVS requires the COLMAP binary '{binary}' on PATH. "
            "Install COLMAP: https://colmap.github.io/install.html"
        )

    dense_dir = workspace / "dense"
    dense_dir.mkdir(exist_ok=True)
    sparse_dir = workspace / "sparse" / "0"
    mvs_cfg = cfg["dense_mvs"]

    def run(cmd: list[str]) -> None:
        log.info("  $ %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Dense MVS failed:\n%s", result.stderr[-2000:])
            raise RuntimeError(f"COLMAP dense command failed: {cmd[1]}")

    # Undistort images into dense/images/ using the sparse model
    run(_colmap_cmd(binary, "image_undistorter",
        **{"image_path": workspace / "images",
           "input_path": sparse_dir,
           "output_path": dense_dir,
           "output_type": "COLMAP"}))

    # Patch-match stereo depth estimation
    run(_colmap_cmd(binary, "patch_match_stereo",
        **{"workspace_path": dense_dir,
           "workspace_format": "COLMAP",
           "PatchMatchStereo.max_image_size": mvs_cfg["patch_match"]["max_image_size"],
           "PatchMatchStereo.window_radius": mvs_cfg["patch_match"]["window_radius"],
           "PatchMatchStereo.gpu_index": mvs_cfg["patch_match"]["gpu_index"]}))

    # Depth map fusion into a dense point cloud
    ply_path = dense_dir / "fused.ply"
    run(_colmap_cmd(binary, "stereo_fusion",
        **{"workspace_path": dense_dir,
           "workspace_format": "COLMAP",
           "input_type": "geometric",
           "output_path": ply_path,
           "StereoFusion.min_num_pixels": mvs_cfg["fusion"]["min_num_pixels"],
           "StereoFusion.max_reproj_error": mvs_cfg["fusion"]["max_reproj_error"]}))

    log.info("Dense MVS complete: %s", ply_path)
    return ply_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_colmap_images_bin(path: Path) -> int:
    """Quick parse of images.bin to count registered images."""
    import struct
    with open(path, "rb") as f:
        (n,) = struct.unpack("<Q", f.read(8))
    return int(n)


def _count_colmap_images_txt(path: Path) -> int:
    """Count registered images from images.txt (every other non-comment line)."""
    count = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                count += 1
    return count // 2  # images.txt has 2 lines per image
