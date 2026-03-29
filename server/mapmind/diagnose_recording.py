#!/usr/bin/env python3
"""Diagnose a .vis.pb recording: GPS coverage, ARCore pose quality, depth stats."""
import sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
_server_root = _here.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from proto import perceiver_pb2
from streamlog.protoio import ProtoIO

rec = Path(sys.argv[1]).resolve()
frames = ProtoIO(perceiver_pb2.PerceiverDataFrame).read_file(rec)
print(f"Loaded {len(frames)} frames")

# Extract data
arcore_pos = []
gps_lat, gps_lon, gps_alt, gps_acc = [], [], [], []
has_depth = 0
has_rgb = 0
has_imu = 0
has_gps = 0
depth_sizes = []

for f in frames:
    # ARCore pose
    if f.HasField("camera_pose"):
        p = f.camera_pose
        arcore_pos.append([p.position.x, p.position.y, p.position.z])
    else:
        arcore_pos.append([float('nan')] * 3)

    # Depth
    if f.HasField("depth_frame") and len(f.depth_frame.data) > 0:
        has_depth += 1
        depth_sizes.append(len(f.depth_frame.data))

    if f.HasField("rgb_frame") and len(f.rgb_frame.data) > 0:
        has_rgb += 1

    # IMU
    if f.HasField("imu_data"):
        has_imu += 1

    # GPS
    if f.HasField("gps_location") and f.gps_location.latitude != 0:
        has_gps += 1
        gps_lat.append(f.gps_location.latitude)
        gps_lon.append(f.gps_location.longitude)
        gps_alt.append(f.gps_location.altitude)
        gps_acc.append(f.gps_location.accuracy)

arcore_pos = np.array(arcore_pos)

print(f"\nRGB frames:   {has_rgb}/{len(frames)}")
print(f"Depth frames: {has_depth}/{len(frames)}")
print(f"IMU frames:   {has_imu}/{len(frames)}")
print(f"GPS frames:   {has_gps}/{len(frames)}")

if depth_sizes:
    print(f"Depth sizes:  min={min(depth_sizes)}, max={max(depth_sizes)}, median={sorted(depth_sizes)[len(depth_sizes)//2]}")

# ARCore pose stats
valid = ~np.isnan(arcore_pos[:, 0])
print(f"\nARCore poses: {valid.sum()}/{len(frames)} valid")
if valid.sum() > 1:
    diffs = np.linalg.norm(np.diff(arcore_pos[valid], axis=0), axis=1)
    print(f"  Consecutive displacement: mean={diffs.mean():.3f}m, max={diffs.max():.3f}m, median={np.median(diffs):.3f}m")
    print(f"  Position range: X=[{arcore_pos[valid,0].min():.1f}, {arcore_pos[valid,0].max():.1f}], "
          f"Y=[{arcore_pos[valid,1].min():.1f}, {arcore_pos[valid,1].max():.1f}], "
          f"Z=[{arcore_pos[valid,2].min():.1f}, {arcore_pos[valid,2].max():.1f}]")
    jumps = diffs > 2.0  # >2m jump between consecutive frames
    print(f"  Jumps >2m: {jumps.sum()} ({100*jumps.mean():.1f}%)")

# GPS stats
if gps_acc:
    print(f"\nGPS accuracy: mean={np.mean(gps_acc):.1f}m, max={np.max(gps_acc):.1f}m, min={np.min(gps_acc):.1f}m")

# Plot
fig, axes = plt.subplots(2, 2, figsize=(16, 14))

# 1. ARCore XY trajectory
ax = axes[0, 0]
if valid.sum() > 0:
    ax.plot(arcore_pos[valid, 0], arcore_pos[valid, 2], 'b-', linewidth=0.3, alpha=0.5)
    ax.plot(arcore_pos[valid, 0][0], arcore_pos[valid, 2][0], 'go', markersize=8, label='start')
    ax.plot(arcore_pos[valid, 0][-1], arcore_pos[valid, 2][-1], 'ro', markersize=8, label='end')
ax.set_title("ARCore Pose (X vs Z)")
ax.set_xlabel("X (m)")
ax.set_ylabel("Z (m)")
ax.set_aspect("equal")
ax.legend()
ax.grid(True, alpha=0.3)

# 2. GPS trajectory
ax = axes[0, 1]
if gps_lat:
    lat = np.array(gps_lat)
    lon = np.array(gps_lon)
    # Convert to metres from first point
    lat0, lon0 = lat[0], lon[0]
    gps_x = (lon - lon0) * 111320 * np.cos(np.radians(lat0))
    gps_y = (lat - lat0) * 110540
    ax.plot(gps_x, gps_y, 'r-', linewidth=0.5, alpha=0.7)
    ax.plot(gps_x[0], gps_y[0], 'go', markersize=8, label='start')
    ax.plot(gps_x[-1], gps_y[-1], 'ro', markersize=8, label='end')
    ax.set_title(f"GPS Trajectory ({has_gps} fixes, mean acc {np.mean(gps_acc):.1f}m)")
    ax.set_aspect("equal")
    ax.legend()
else:
    ax.set_title("No GPS data")
ax.set_xlabel("East (m)")
ax.set_ylabel("North (m)")
ax.grid(True, alpha=0.3)

# 3. ARCore height (Y) over time — should be flat for ground vehicle
ax = axes[1, 0]
if valid.sum() > 0:
    ax.plot(arcore_pos[valid, 1], 'b-', linewidth=0.3)
    ax.set_title("ARCore Y (height) over time")
    ax.set_ylabel("Y (m)")
    ax.set_xlabel("Frame")
    ax.grid(True, alpha=0.3)

# 4. Consecutive frame displacement
ax = axes[1, 1]
if valid.sum() > 1:
    ax.plot(diffs, 'b-', linewidth=0.3)
    ax.axhline(y=2.0, color='r', linestyle='--', label='>2m jump')
    ax.set_title("Consecutive frame displacement")
    ax.set_ylabel("Distance (m)")
    ax.set_xlabel("Frame")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(True, alpha=0.3)

fig.suptitle(rec.name, fontsize=14)
fig.tight_layout()
out = rec.with_suffix(".diagnostic.png")
fig.savefig(out, dpi=150)
print(f"\nWrote {out}")
