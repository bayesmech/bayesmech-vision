# MapMind C++ Binary

`mapmind` is an offline batch tool that ingests `.vis.pb` recordings, routes them through a selectable SLAM backend, and writes per-frame `MapMindFrame` records plus a trailing `MapMindResult` that carries the SLAM metadata and the resulting point cloud.

Current status:

- Native protobuf I/O for `.vis.pb` and `.mapmind.pb` is implemented.
- YAML configuration is parsed natively with `yaml-cpp`.
- Backend selection is routed through `src/slam_backend.{h,cc}` so `arcore_passthrough` and `rtabmap` can coexist.
- `rtabmap` links cleanly against system `OpenCV`/`PCL` plus the local install under `build/system-prefix/rtabmap`, and `src/rtabmap_backend.cc` now feeds RTAB-Map, collects optimized poses/links, and writes the colored point cloud to both `MapMindResult.point_cloud` and a `.ply/.png` export.
- When RTAB-Map can’t keep RGB/depth in the signatures, the backend falls back to replaying the recorded depth frames with the optimized poses, so the resulting cloud looks flat even though it still contains per-pixel color data.

Build:

```
cd server/mapmind
cmake -S . -B build
cmake --build build -j
```

Run:

```
cd server/mapmind
./build/mapmind ../../recordings/<name>.vis.pb
```

The binary writes `recordings/<name>.mapmind.pb` plus `recordings/<name>.mapmind.point_cloud.ply` (XYZRGB) and `recordings/<name>.mapmind.point_cloud.png` (rendered preview). Use `server/mapmind/render_point_cloud.py` (via `uv run python mapmind/render_point_cloud.py <path>`) if you want a quick PNG without installing a viewer.

Package discovery:

`mapmind` prefers system packages first; if a local RTAB-Map install exists it adds:

- `server/mapmind/build/system-prefix/rtabmap`

Legacy hints remain for older local builds:

- `server/mapmind/build/vendor/rtabmap`
- `server/mapmind/build/vendor-prefix/*`

Dependency note:

- System dependencies: `protobuf`, `yaml-cpp`, `protoc`, `libopencv-dev`, `libpcl-dev`.
- RTAB-Map source is under `libs/rtabmap`.
- Point-cloud preview relies on the project’s `uv` environment (`uv run python mapmind/render_point_cloud.py ...`).

RTAB-Map integration

1. `src/CMakeLists.txt` sets `MAPMIND_HAVE_RTABMAP` when `rtabmap::core` is available and links the binary against the local RTAB-Map install under `build/system-prefix/rtabmap`.
2. `src/rtabmap_backend.cc` decodes each frame via the same format as `motioncap/geometry.py`, feeds RGB-D + pose into `rtabmap::Rtabmap::process`, and fetches optimized poses/constraints through `rtabmap::Rtabmap::getGraph`.
3. Loop closures are counted from the returned `rtabmap::Link` objects; the optimized poses are converted back into `mapmind::PoseSample`.
4. `util3d::cloudRGBFromSensorData` is used when signatures still carry RGB/depth; when that fails we rerun depth unprojection with the optimized poses so the cloud still carries per-pixel color even if it looks flat.
5. The backend writes a `.ply` and `.png`, and `MapMindResult.point_cloud` gets populated at the end of `src/main.cc`.

Color roadmap

1. Make sure RTAB-Map keeps RGB/depth in each signature by adjusting parameters such as `Rtabmap/DetectionRate`, `RGBD/LoopClosureReextractFeatures`, and `Mem/IncrementalMemory`. Once those buffers are present, `util3d::cloudRGBFromSensorData` will give the same rich colors that RTAB-Map’s GUI shows.
2. If the data is already there, iterate the optimized signatures and copy the stored `SensorData` into the exporter before voxelizing. That guarantees the exported cloud matches the RTAB-Map view instead of the depth-reprojected fallback.
3. The renderer script (`server/mapmind/render_point_cloud.py`) can then re-project the high-fidelity cloud for quick previews, and you can open `recordings/<name>.mapmind.point_cloud.ply` in Meshlab/CloudCompare for interactive inspection.
