"""
Write a ReconstructionResponse proto summary to <name>.recon.pb.
"""

import sys
import yaml
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, reconstruction_pb2
from streamlog.protoio import ProtoIO

_recon_io = ProtoIO(reconstruction_pb2.ReconstructionResponse)


def write_recon_pb(
    vis_path: Path,
    workspace: Path,
    first_frame: perceiver_pb2.PerceiverDataFrame,
    num_total_images: int,
    num_registered_images: int,
    frames_extracted: int,
    sample_every: int,
    cfg: dict,
    elapsed: float,
    splat_ply: Path | None = None,
) -> Path:
    """
    Build a ReconstructionResponse and write it to <vis_path stem>.recon.pb.

    Returns the output path.
    """
    resp = reconstruction_pb2.ReconstructionResponse()

    # Source metadata
    resp.frame_identifier.CopyFrom(first_frame.frame_identifier)
    resp.frames_extracted = frames_extracted
    resp.sample_every = sample_every
    resp.num_total_images = num_total_images

    # COLMAP sparse model stats
    sparse_dir = workspace / "sparse" / "0"
    resp.num_registered_images = num_registered_images
    resp.workspace_path = str(workspace.relative_to(vis_path.parent))

    if sparse_dir.exists():
        resp.num_sparse_points = _count_sparse_points(sparse_dir)
        mre = _mean_reprojection_error(sparse_dir)
        if mre is not None:
            resp.mean_reprojection_error = mre

    # Dense MVS
    fused_ply = workspace / "dense" / "fused.ply"
    if fused_ply.exists():
        resp.num_dense_points = _count_ply_vertices(fused_ply)
        resp.dense_ply_path = str(fused_ply.relative_to(workspace))

    # Gaussian Splatting
    if splat_ply and splat_ply.exists():
        resp.num_gaussians = _count_ply_vertices(splat_ply)
        resp.splat_ply_path = str(splat_ply.relative_to(vis_path.parent))

    # Pipeline metadata
    resp.total_runtime_seconds = elapsed
    resp.config_yaml = yaml.dump(cfg, default_flow_style=False)

    out_path = vis_path.with_suffix("").with_suffix(".recon.pb")
    # Overwrite (not append) for idempotency
    if out_path.exists():
        out_path.unlink()
    _recon_io.write_file(out_path, [resp])
    return out_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_sparse_points(sparse_dir: Path) -> int:
    try:
        import pycolmap
        recon = pycolmap.Reconstruction(str(sparse_dir))
        return recon.num_points3D()
    except Exception:
        pass
    points_bin = sparse_dir / "points3D.bin"
    points_txt = sparse_dir / "points3D.txt"
    if points_bin.exists():
        import struct
        with open(points_bin, "rb") as f:
            (n,) = struct.unpack("<Q", f.read(8))
        return int(n)
    if points_txt.exists():
        count = 0
        with open(points_txt) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    count += 1
        return count
    return 0


def _mean_reprojection_error(sparse_dir: Path) -> float | None:
    try:
        import pycolmap
        recon = pycolmap.Reconstruction(str(sparse_dir))
        errors = [pt.error for pt in recon.points3D.values()]
        if errors:
            return float(sum(errors) / len(errors))
    except Exception:
        pass
    return None


def _count_ply_vertices(ply_path: Path) -> int:
    """Read vertex count from PLY header without loading the full file."""
    try:
        with open(ply_path, "rb") as f:
            for line in f:
                line = line.decode("ascii", errors="ignore").strip()
                if line.startswith("element vertex"):
                    return int(line.split()[-1])
                if line == "end_header":
                    break
    except Exception:
        pass
    return 0
