#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from idoslam.common import (
    idoslam_proto_path,
    plane_width_csv_path,
    road_debug_video_output_path,
    seg_path,
    sift_debug_video_output_path,
    track_map_png_output_path,
)
from idoslam.export import write_idoslam_pb

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the consolidated IDOSLAM pipeline")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--segmentation", type=Path, default=None, help="Optional .seg.pb path")
    p.add_argument("--output", type=Path, default=None, help="Output .idoslam.pb path")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--debug-render-video", action="store_true", help="Render the debug videos and track-map PNG")
    p.add_argument("--skip-plane", action="store_true", help="Skip plane-width estimation")
    p.add_argument("--skip-canonical", action="store_true", help="Skip canonical-track optimisation")
    p.add_argument("--skip-triangulated", action="store_true", help="Skip triangulated ground-point stage")
    return p.parse_args()


def run_step(name: str, cmd: list[str]) -> None:
    log.info("=== %s", name)
    log.info("$ %s", " ".join(shlex.quote(part) for part in cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    if not recording.exists():
        raise FileNotFoundError(f"Recording not found: {recording}")

    segmentation = args.segmentation.resolve() if args.segmentation else seg_path(recording)
    if not segmentation.exists():
        raise FileNotFoundError(f"Segmentation not found: {segmentation}")

    if args.debug_render_video and (args.skip_plane or args.skip_canonical):
        raise RuntimeError("--debug-render-video requires the plane and canonical stages")
    if args.debug_render_video and args.skip_triangulated:
        raise RuntimeError("--debug-render-video requires the triangulated stage")

    python = sys.executable
    script_dir = Path(__file__).resolve().parent

    with tempfile.TemporaryDirectory(prefix="idoslam.") as workspace_str:
        workspace = Path(workspace_str)
        pairwise_dir = workspace / "pairwise"
        plane_dir = workspace / "track_width_plane"
        canonical_dir = workspace / "canonical"
        triangulated_dir = workspace / "triangulated"

        pairwise_cmd = [
            python,
            str(script_dir / "sift_pairwise_trajectory.py"),
            str(recording),
            "--segmentation",
            str(segmentation),
            "--output-dir",
            str(pairwise_dir),
            "--start-frame",
            str(args.start_frame),
        ]
        if args.max_frames > 0:
            pairwise_cmd.extend(["--max-frames", str(args.max_frames)])
        run_step("Pairwise trajectory", pairwise_cmd)

        if not args.skip_plane:
            plane_cmd = [
                python,
                str(script_dir / "track_width_map_plane.py"),
                str(recording),
                "--segmentation",
                str(segmentation),
                "--output-dir",
                str(plane_dir),
            ]
            run_step("Plane width estimation", plane_cmd)

        if not args.skip_canonical:
            if args.skip_plane:
                raise RuntimeError("Canonical stage requires plane-width output; do not use --skip-plane")
            canonical_cmd = [
                python,
                str(script_dir / "canonical_track_optimizer.py"),
                str(recording),
                "--segmentation",
                str(segmentation),
                "--width-csv",
                str(plane_dir / plane_width_csv_path(recording).name),
                "--output-dir",
                str(canonical_dir),
            ]
            run_step("Canonical track optimisation", canonical_cmd)

        if not args.skip_triangulated:
            triangulated_cmd = [
                python,
                str(script_dir / "track_width_map_triangulated.py"),
                str(recording),
                "--segmentation",
                str(segmentation),
                "--trajectory-csv",
                str(pairwise_dir / "trajectory_pairwise_sift.csv"),
                "--output-dir",
                str(triangulated_dir),
            ]
            run_step("Triangulated ground points", triangulated_cmd)

        if args.debug_render_video:
            road_debug_output = road_debug_video_output_path(recording)
            road_video_cmd = [
                python,
                str(script_dir / "road_grid_overlay_video.py"),
                str(recording),
                "--segmentation",
                str(segmentation),
                "--track-width-dir",
                str(plane_dir),
                "--canonical-dir",
                str(canonical_dir),
                "--output",
                str(road_debug_output),
            ]
            run_step("Road-grid debug video", road_video_cmd)

            sift_debug_output = sift_debug_video_output_path(recording)
            sift_video_cmd = [
                python,
                str(script_dir / "feature_correspondence_video.py"),
                str(recording),
                "--correspondences-csv",
                str(triangulated_dir / "point_correspondences.csv"),
                "--pair-logs",
                str(triangulated_dir / "pair_logs.json"),
                "--output",
                str(sift_debug_output),
            ]
            run_step("SIFT correspondence video", sift_video_cmd)

            internal_track_map = pairwise_dir / "track_plot.png"
            if internal_track_map.exists():
                published_track_map = track_map_png_output_path(recording)
                shutil.copy2(internal_track_map, published_track_map)
                log.info("Wrote %s", published_track_map)
            else:
                log.warning("Track map PNG was not produced by the pairwise stage")

        output = args.output.resolve() if args.output else idoslam_proto_path(recording)
        written = write_idoslam_pb(
            recording=recording,
            segmentation=segmentation,
            output_path=output,
            trajectory_csv=pairwise_dir / "trajectory_pairwise_sift.csv",
            ground_points_csv=triangulated_dir / "ground_points.csv",
            correspondences_csv=triangulated_dir / "point_correspondences.csv",
            pair_logs_path=triangulated_dir / "pair_logs.json",
        )
        log.info("Wrote %s", written)


if __name__ == "__main__":
    main()
