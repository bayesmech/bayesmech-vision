from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobDefinition:
    name: str
    title: str
    description: str
    entrypoint: Path
    input_suffixes: tuple[str, ...] = (".vis.pb",)
    requires_gpu: bool = False

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_suffixes": list(self.input_suffixes),
            "requires_gpu": self.requires_gpu,
            "available": self.entrypoint.is_file(),
        }


def builtin_job_registry(server_root: Path) -> dict[str, JobDefinition]:
    """Return the server jobs that may be invoked over the runner API.

    Keeping this as an explicit allowlist is intentional. A runner may be
    reachable from the public Internet, so accepting an arbitrary executable
    or shell command here would turn it into a remote-shell service.
    """

    definitions = (
        JobDefinition(
            "segmentation",
            "Segmentation",
            "Run SAM-based video segmentation.",
            server_root / "segmentation" / "main.py",
            requires_gpu=True,
        ),
        JobDefinition(
            "motioncap",
            "Motion Capture",
            "Generate optical-flow heatmaps and tracked motion overlays.",
            server_root / "motioncap" / "main.py",
            requires_gpu=True,
        ),
        JobDefinition(
            "idoslam",
            "Map Generation",
            "Run the IDOSLAM localization and mapping pipeline.",
            server_root / "idoslam" / "main.py",
        ),
        JobDefinition(
            "pongtown",
            "Pongtown",
            "Estimate table pose and table-tennis or snooker trajectories.",
            server_root / "pongtown" / "main.py",
        ),
        JobDefinition(
            "reconstruct",
            "3D Reconstruction",
            "Run COLMAP reconstruction and optional Gaussian splatting.",
            server_root / "reconstruct" / "main.py",
            requires_gpu=True,
        ),
        JobDefinition(
            "genspark",
            "AI Analysis",
            "Run the video analysis and insight-generation pipeline.",
            server_root / "genspark" / "main.py",
        ),
    )
    return {definition.name: definition for definition in definitions}
