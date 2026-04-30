"""Pytest path setup for local server modules and generated protos."""

from pathlib import Path
import sys

_server_root = Path(__file__).resolve().parent
_project_root = _server_root.parent

for _path in (_project_root, _project_root / "proto", _server_root):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)
