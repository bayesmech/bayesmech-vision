"""Pongtown CLI: stages 1→2→3 with debug overlays.

Usage:
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb --stop-after 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import cv2
import yaml

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from pongtown.loader import iter_bundles
from pongtown.quad_fit import (
    METHOD_QUAD_FAILED,
    fit_table_quadrilateral,
)
from pongtown.render import montage, render_stage1_panel


log = logging.getLogger("pongtown")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ping-pong table pose pipeline")
    p.add_argument("vis_path", type=Path)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--sample-every", type=int, default=1)
    p.add_argument("--stop-after", type=int, choices=[1, 2, 3], default=3)
    p.add_argument("--no-debug", action="store_true")
    p.add_argument("--render-mp4", action="store_true")
    return p.parse_args()


def _seg_path_for(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.seg.pb"


def _debug_dir(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.debug"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args()

    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    cfg = yaml.safe_load(open(cfg_path))

    vis_path = args.vis_path.resolve()
    seg_path = _seg_path_for(vis_path)
    if not vis_path.is_file() or not seg_path.is_file():
        raise FileNotFoundError(f"Missing inputs: {vis_path} or {seg_path}")

    debug = (not args.no_debug) and cfg["pipeline"]["debug_dumps"]
    odir: Path | None = None
    if debug:
        odir = _debug_dir(vis_path) / "overlay"
        odir.mkdir(parents=True, exist_ok=True)
        log.info("Debug overlays → %s", odir)

    every = max(1, int(cfg["overlay"]["every_n_frames"]))

    stage1_results: list[tuple[int, object]] = []

    for b in iter_bundles(
        vis_path, seg_path, cfg["mask_labels"],
        max_frames=args.max_frames, sample_every=args.sample_every,
    ):
        qres = fit_table_quadrilateral(b.table_mask, b.net_mask, cfg=cfg)
        stage1_results.append((b.frame_idx, qres))
        if qres.method != METHOD_QUAD_FAILED:
            log.info(
                "frame %d: stage1 method=%d quality=%.2f",
                b.frame_idx, qres.method, qres.quality,
            )
        if debug and odir is not None and (b.frame_idx % every) == 0:
            panel = render_stage1_panel(
                b.rgb, b.table_mask, b.net_mask, b.person_mask,
                qres.quad_img, qres.midline_img,
                f"f{b.frame_idx} stage1 m={qres.method} q={qres.quality:.2f}",
            )
            cv2.imwrite(str(odir / f"frame_{b.frame_idx:06d}.png"), panel)

    if args.stop_after == 1:
        ok = sum(1 for _, r in stage1_results if r.method != METHOD_QUAD_FAILED)
        log.info("Stage 1 done: %d / %d frames with a quad", ok, len(stage1_results))
        return

    raise NotImplementedError("Stages 2 and 3 are added in later tasks")


if __name__ == "__main__":
    main()
