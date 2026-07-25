# Efficient pose-estimation and point-cloud-generation.

Pose estimation and point cloud generation models are inaccurate, hard to serve, take an insane amount of compute. This project will do model surgery and compute graph understanding to figure out why it is so expensive to run these models, particularly with very small output bottlenecks. And then to develop better models. 

## Setup

```bash
git clone --recurse-submodules <this-repo>
pip install -r vendor/vggt_omega/requirements.txt
pip install -e vendor/vggt_omega/

huggingface-cli login

./scripts/download_checkpoints.sh checkpoints/
./scripts/download_datasets.sh data/
```

## Minimal VGGT-Omega Video Inference

For the simplest video-to-geometry path, use:

```bash
python scripts/infer_vggt_omega_video.py \
  --video path/to/video.mp4 \
  --ckpt checkpoints/vggt_omega/vggt_omega_1b_512.pt \
  --out outputs/video_name \
  --ply
```

This samples frames from the video, runs VGGT-Omega on the sampled sequence, and
saves:

- `outputs/video_name/pointclouds/frame_000000.npz`, one file per sampled frame.
  Each point file contains `xyz`, `rgb`, `uv`, `flat_indices`, `depth`, `conf`,
  and optionally `segmentation`.
- `outputs/video_name/camera_trajectory.npz`, containing `extrinsics`,
  `intrinsics`, `camera_to_world`, `camera_centers`, source frame indices, and
  timestamps.
- `outputs/video_name/metadata.json`, containing preprocessing and convention
  details.

VGGT-Omega itself predicts per-frame `depth`, `depth_conf`, and a camera pose
encoding from the video frames. `encoding_to_camera` decodes the pose encoding
into intrinsics and camera-from-world extrinsics. The script then unprojects
each predicted depth pixel with those camera parameters, so the point clouds and
camera trajectory are derived directly from the model's video-only outputs.
By default the script sends all sampled frames in one model call so the camera
trajectory lives in one coherent model world frame. Use `--window` only as a
VRAM fallback for long clips; smaller windows produce per-window world frames.

If you have segmentation aligned to the video, pass either:

```bash
python scripts/infer_vggt_omega_video.py ... --seg-video path/to/segments.mp4
```

or a directory of maps named like extracted frames:

```bash
python scripts/infer_vggt_omega_video.py ... --seg-dir path/to/segmentation_frames
```

The script applies the same crop/resize preprocessing to segmentation maps using
nearest-neighbor sampling, then joins labels to points through `flat_indices`.
For each point, `uv` is the corresponding pixel coordinate in VGGT-Omega's
preprocessed image space. If you pass `--seg-preprocessed`, each segmentation
map must already have the exact same height and width as the VGGT-Omega output;
the script validates this before attaching labels to points.

## Gaussian Splatting from VGGT Output

The native `/worldgen @MarkerA-@MarkerB` flow appends one length-delimited
`VggtInferenceResponse` record to the recording's single `<name>.vggt.pb`
file. Records carry their source frame numbers, so later marker ranges extend
the same artifact and overlapping frames use the newest computation. The file
can then be consumed by the 3D Gaussian Splatting trainer:

```bash
cd server
uv run python worldgen/scripts/train_vggt_splat.py \
  --vggt-pb path/to/result.vggt.pb
```

The trainer uses VGGT's world-space point clouds for initialization and the
same VGGT camera poses/intrinsics for photometric optimization. It reconstructs
the exact center-cropped/resized RGB frames from the original `.vis.pb` so
training pixels match VGGT's preprocessed camera model.

Default constraints prioritize clean output over speed:

- `WORLDGEN_SPLAT_STEPS=30000`
- `WORLDGEN_SPLAT_MAX_INIT_POINTS=180000`
- `WORLDGEN_SPLAT_MAX_GAUSSIANS=1000000`
- `WORLDGEN_SPLAT_MIN_CONFIDENCE=0.55`
- `WORLDGEN_SPLAT_DATA_FACTOR=1`

Outputs are written next to the VGGT protobuf as `.splat.ply`,
`.splat.preview.json`, and `.splat-workspace/`. Set `WORLDGEN_SPLAT=0` to keep
only the VGGT point cloud response.

## Research Directions

1. Auto-regressive inference when trying to do physics modeling, and optimizations on top of that auto-regressive model once it works.
2. Mitigate depth regularization for small, fast-moving objects. Use segmentation, or surface roughness estimation to do this.
3. Compute graph tracing for each depth point, understanding how VGGT can be distilled while retaining most of the compute structure.
4. Improve the pre-training recepie instead of just having self-distillation on random internet data. Understanding how training order affects things.
