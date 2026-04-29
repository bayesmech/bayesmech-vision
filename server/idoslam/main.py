#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from idoslam.common import (
    idoslam_proto_path,
    pairwise_motion_csv_path,
    pairwise_trajectory_csv_path,
    refined_trajectory_csv_path,
    seg_path,
    triangulated_correspondences_csv_path,
    triangulated_ground_points_csv_path,
    triangulated_pair_logs_path,
    workspace_path,
)
from idoslam.export import hydrate_workspace_from_idoslam_pb, write_idoslam_pb

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the consolidated IDOSLAM pipeline")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--resume", action="store_true", help="Resume from an existing .idoslam.pb checkpoint")
    p.add_argument("--skip-plane", action="store_true", help="Skip plane-width estimation")
    p.add_argument("--skip-canonical", action="store_true", help="Skip canonical-track optimisation")
    p.add_argument("--skip-triangulated", action="store_true", help="Skip triangulated ground-point stage")
    return p.parse_args()


def run_step(name: str, cmd: list[str]) -> None:
    log.info("=== %s", name)
    log.info("$ %s", " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, check=True)


def pose_refinement_enabled() -> bool:
    with _CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f) or {}
    pose_cfg = raw.get("pose_refinement") or {}
    return bool(pose_cfg.get("enabled", True))


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    if not recording.exists():
        raise FileNotFoundError(f"Recording not found: {recording}")

    segmentation = seg_path(recording)
    if not segmentation.exists():
        raise FileNotFoundError(f"Segmentation not found: {segmentation}")

    python = sys.executable
    script_dir = Path(__file__).resolve().parent
    refinement_enabled = pose_refinement_enabled()
    output = idoslam_proto_path(recording)
    workspace = workspace_path(recording)
    raw_trajectory_csv = pairwise_trajectory_csv_path(recording)
    pairwise_motion_csv = pairwise_motion_csv_path(recording)
    refined_csv = refined_trajectory_csv_path(recording)
    success = False

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        resume_state = {
            "has_raw_poses": False,
            "has_refined_poses": False,
            "has_pairwise_motion": False,
            "has_ground_points": False,
            "has_pair_debug": False,
            "has_correspondences": False,
        }

        if args.resume:
            if not output.exists():
                raise FileNotFoundError(f"--resume requested but checkpoint proto not found: {output}")
            resume_state = hydrate_workspace_from_idoslam_pb(recording)
            log.info("=== Resume")
            log.info("Hydrated checkpoint state from %s: %s", output, resume_state)

        reran_pairwise = False

        if resume_state["has_raw_poses"] and raw_trajectory_csv.exists():
            log.info("=== Pairwise trajectory")
            log.info("Skipping because raw VO poses were restored from %s", output)
        else:
            pairwise_cmd = [
                python,
                str(script_dir / "sift_pairwise_trajectory.py"),
                str(recording),
                "--start-frame",
                str(args.start_frame),
            ]
            if args.max_frames > 0:
                pairwise_cmd.extend(["--max-frames", str(args.max_frames)])
            run_step("Pairwise trajectory", pairwise_cmd)
            reran_pairwise = True
            write_idoslam_pb(recording)
            log.info("Checkpointed %s after pairwise trajectory", output)

        if refinement_enabled:
            if args.resume and not reran_pairwise and resume_state["has_refined_poses"] and refined_csv.exists():
                log.info(
                    "Refined poses were restored from %s; rerunning GPS pose refinement from restored raw VO poses",
                    output,
                )
            if not pairwise_motion_csv.exists():
                log.info(
                    "Pairwise motion metadata missing; GPS refinement will use uniform visual pair weights"
                )
            refine_cmd = [
                python,
                str(script_dir / "gps_pose_refiner.py"),
                str(recording),
            ]
            run_step("GPS pose refinement", refine_cmd)
            write_idoslam_pb(recording)
            log.info("Checkpointed %s after GPS pose refinement", output)
        else:
            log.info("=== GPS pose refinement")
            log.info("Skipping because pose_refinement.enabled=false in %s", _CONFIG_PATH)

        run_mapping_stages = not args.resume
        if not run_mapping_stages:
            log.info("=== Plane width estimation")
            log.info("Skipping because --resume restores existing mapping artifacts from the checkpoint")
            log.info("=== Canonical track optimisation")
            log.info("Skipping because --resume restores existing mapping artifacts from the checkpoint")

        if run_mapping_stages and not args.skip_plane:
            plane_cmd = [
                python,
                str(script_dir / "track_width_map_plane.py"),
                str(recording),
            ]
            run_step("Plane width estimation", plane_cmd)

        if run_mapping_stages and not args.skip_canonical:
            if args.skip_plane:
                raise RuntimeError("Canonical stage requires plane-width output; do not use --skip-plane")
            canonical_cmd = [
                python,
                str(script_dir / "canonical_track_optimizer.py"),
                str(recording),
            ]
            run_step("Canonical track optimisation", canonical_cmd)

        has_restored_triangulated_for_this_run = (
            args.resume
            and not reran_pairwise
            and resume_state["has_ground_points"]
            and triangulated_ground_points_csv_path(recording).exists()
        )
        can_resume_triangulated = (
            has_restored_triangulated_for_this_run
            and resume_state["has_pair_debug"]
            and resume_state["has_correspondences"]
            and triangulated_correspondences_csv_path(recording).exists()
            and triangulated_pair_logs_path(recording).exists()
        )
        if not args.skip_triangulated:
            if can_resume_triangulated:
                log.info("=== Triangulated ground points")
                log.info("Skipping because triangulated outputs were restored from %s", output)
            else:
                if args.resume and resume_state["has_pair_debug"] and not resume_state["has_correspondences"]:
                    log.info("=== Triangulated ground points")
                    log.info("Checkpoint proto lacks stored correspondences; rerunning triangulation")
                triangulated_cmd = [
                    python,
                    str(script_dir / "track_width_map_triangulated.py"),
                    str(recording),
                ]
                run_step("Triangulated ground points", triangulated_cmd)
                write_idoslam_pb(recording)
                log.info("Checkpointed %s after triangulated ground points", output)

        written = write_idoslam_pb(recording)
        log.info("Wrote %s", written)
        success = True
    finally:
        if success and workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
            log.info("Removed workspace %s", workspace)


if __name__ == "__main__":
    main()
