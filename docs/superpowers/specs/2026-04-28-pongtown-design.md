# Pongtown — Ping-pong table pose estimation

**Date:** 2026-04-28
**Status:** Design approved, ready for implementation plan
**Dev fixture:** `recordings/20260425_140029/`

## Problem

Given a recording (`.vis.pb` + `.seg.pb`) of a ping-pong table where the segmentation file contains masks for `table-top`, `net`, and `person` (all from SAM3), estimate:

1. The 4 image-space corners of the table per frame.
2. The camera pose relative to the table per frame.
3. A single global table pose in ARCore world coordinates, refined across all frames.

Once the global pose is known, the canonical 2.74 m × 1.525 m table rectangle (and the 1.83 m × 0.1525 m net) reprojects into every frame and should have high IoU with the masks (excluding person pixels, which are not penalised).

The dashboard overlay consuming this output is **out of scope for v1**.

## Inputs and constraints

- ITTF table dims: **2740 mm × 1525 mm**, net **1830 mm wide** (152.5 mm overhang each side) × **152.5 mm tall**.
- The net's bottom edge lies on the table midline (`x = 0` in table-local coords, `y ∈ [-762.5, +762.5]`).
- ARCore camera pose per frame is in `PerceiverDataFrame.camera_pose` (assumed drift-free over short captures).
- ARCore plane detection picks up walls in this scene, so we **do not** rely on plane labels — we use the world-frame horizontal plane only as a constraint surface during global refinement.
- `person` masks are subtracted from the IoU denominator so a person walking in front of the table does not penalise pose quality.
- Mask labels are matched by config-driven substrings (we do not hardcode SAM3 wording).

## Module layout

```
server/pongtown/
  __init__.py
  config.yaml          # table dims, RANSAC params, IoU thresholds, mask label substrings
  loader.py            # iterate .vis.pb + .seg.pb in lockstep, pick masks by label
  quad_fit.py          # Stage 1: 4-line RANSAC → 4 image corners (+ net midline)
  pose.py              # Stage 2: PnP with 6 correspondences, reprojection IoU
  world_stabilize.py   # Stage 3: lift to world, geometric/circular median, refine
  render.py            # 3-panel overlay PNGs, IoU timeline plot, optional MP4
  main.py              # argparse, runs stages 1→2→3, writes .pongtown.pb + overlays

proto/pongtown.proto
```

CLI:

```
uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb \
    [--max-frames N] [--sample-every K] [--no-render] [--no-debug] [--stop-after {1,2,3}]
```

`--stop-after` truncates the pipeline at any stage during development.

## Outputs

- `recordings/<name>/<name>.pongtown.pb` — length-delimited per-frame `PongtownResponse` records followed by exactly one summary record (matches snookestown). The summary record has empty `frame_identifier` and `global_table_pose.has_pose == true`.
- `recordings/<name>/<name>.pongtown.debug/overlay/frame_NNNNNN.png` — three-panel montage at every Nth frame (`overlay.every_n_frames`, default 5):
  - **Panel 1 (Stage 1):** RGB + masks (table=cyan, net=magenta, person=yellow). 4 detected table lines (green). Detected midline (red). Detected quad (white).
  - **Panel 2 (Stage 2):** RGB + per-frame PnP-reprojected rectangle (white), net rectangle (magenta), midline (red). Title shows `pnp_iou`.
  - **Panel 3 (Stage 3):** RGB + globally-reprojected rectangle (white, thicker), net (magenta), midline (red). Title shows `global_iou`. `OFF_SCREEN` frames get a red border.
- `recordings/<name>/<name>.pongtown.debug/iou_timeline.png` — line plot of `pnp_iou` and `global_iou` vs frame index.
- `recordings/<name>/<name>.pongtown.mp4` *(optional, behind `--render-mp4`)* — overlay video.

## Algorithms

### Stage 1 — per-frame quadrilateral

Per frame `i`:

1. Build target mask `M_i = (table_mask ∪ net_mask) \ person_mask`.
2. Sobel on `M_i.astype(uint8)*255`, threshold to sparse boundary pixels with gradient orientations.
3. **4-line RANSAC**: iteratively fit lines.
   - Pick 2 random edge pixels with similar gradient orientation, fit line, count inliers (point-line distance < `d_thresh` and gradient orientation agreement < `θ_thresh`).
   - Best line → remove inliers → repeat until 4 lines extracted.
   - Reject if any line has < `min_inliers` support → `QUAD_FAILED`.
4. Pair into opposite sides by clustering line angles (mod 180°). Reject if not 2+2.
5. **4 corners** = pairwise intersections of opposite-group lines, ordered CCW from the top-left of `M_i`'s bbox.
6. **Net midline**: run the same 4-line RANSAC on `net_mask` alone; pick the long edge whose pixels fall mostly inside `table_mask` — this is the midline.
7. **Midline constraint check**: the midline must be parallel to the table's short edges (within `θ_parallel_thresh`) and equidistant from them. Violations downgrade `quad_quality` or reject.
8. **Recovery from 3 lines**: when 4-line RANSAC finds only 3 strong table lines, synthesise the missing short edge by reflecting the visible one through the midline. Mark these frames `QUAD_FROM_MIDLINE`.
9. **Sanity gates**: convexity, area-ratio vs `M_i` in `[0.7, 1.4]`, aspect ratio in `[1.2, 2.5]`. Fail → `QUAD_FAILED`.

Emits `quad_img[4×2]`, `midline_img[2×2]`, and `quad_quality ∈ [0, 1]` per frame.

### Stage 2 — PnP (camera pose relative to table)

Canonical world-frame corners (table-local frame, z = 0, origin at table center, x = long axis, mm):

```
P_corners = [(-1370, -762.5, 0), (1370, -762.5, 0), (1370, 762.5, 0), (-1370, 762.5, 0)]
P_midline = [(0, -762.5, 0), (0, +762.5, 0)]
```

1. `cv2.solvePnP(P_corners, quad_img, K, ..., SOLVEPNP_IPPE_SQUARE)` returns two solutions (planar ambiguity).
2. **Compute midline–edge intersections in image space**: the detected `midline_img` segment is the net's bottom edge, which extends past the table due to the 15.25 cm net overhang. Intersect the infinite line through `midline_img` with each of the two table long edges (from `quad_img`) to get two image points `m_left_img`, `m_right_img`. These — not the raw `midline_img` endpoints — are the PnP correspondences for `P_midline`.
3. **Disambiguate via midline**: pick the IPPE solution whose reprojected `P_midline` endpoints are closest (Euclidean, summed) to `(m_left_img, m_right_img)`. The camera being on one side of the net makes this asymmetric and reliable — no separate IoU disambiguation needed.
4. `cv2.solvePnPRefineLM` over all 6 correspondences (`P_corners` + `P_midline` ↔ `quad_img` + `(m_left_img, m_right_img)`).
5. Reprojection IoU: rasterise the canonical 2.74 × 1.525 rectangle under the chosen pose, IoU against `M_i`. Reject if `pnp_iou < iou_min` (default 0.6).
6. Store `T_table_to_camera[4×4]` per frame.

### Stage 3 — world stabilisation

1. **Lift to world**: for each frame with a valid Stage-2 pose, `T_table_to_world_i = T_camera_to_world_i ∘ T_table_to_camera_i`.
2. **Project onto SE(2) on world horizontal plane**: extract `(x, y)` (world-frame xy of table center), `yaw` (rotation about world-up), `z` (height above world floor).
3. **Robust aggregate**:
   - `(x, y, z)` → geometric median (Weiszfeld iterations).
   - `yaw` → circular median mod π (table has 180° symmetry; net majority vote breaks the residual half-turn ambiguity, since each per-frame pose has already been disambiguated by midline in Stage 2).
4. **Refine** *(gated by `world.refine`, default true)*: Powell on `(x, y, z, yaw)` minimising

   ```
   cost = − Σ_i [ α · IoU(reproj_table_rect_i, table_mask_i \ person_i)
                + β · IoU(reproj_net_strip_i,  net_mask_i   \ person_i)
                + γ · line_overlap(reproj_midline_i, detected_midline_i) ]
   ```

   over a stratified sample of `world.refine_sample_size` frames (default 50). `α=1.0, β=0.5, γ=0.3`. `line_overlap` is the fraction of detected-midline pixels within `d_line_thresh` of the reprojected midline.
5. **Reproject** the global pose into every frame to produce `quad_img_global` and `global_iou`. Frames where the reprojected quad falls entirely off-screen are flagged `OFF_SCREEN`.

Summary record stores `T_table_to_world_global[4×4]`, `frames_used`, mean / p10 / p90 IoU, plus canonical geometry constants.

## Proto schema

```proto
syntax = "proto3";
package bayesmech.vision;
import "perceiver.proto";

message PongtownResponse {
  PerceiverFrameIdentifier frame_identifier = 1;

  message TablePose {
    enum Method {
      UNKNOWN = 0;
      QUAD_FULL = 1;            // 4 strong table lines
      QUAD_FROM_MIDLINE = 2;    // 3 table lines + net midline reflection
      QUAD_FAILED = 3;
      OFF_SCREEN = 4;
    }
    Method method = 1;
    float quad_quality = 2;
    float pnp_iou = 3;
    repeated float quad_img = 4 [packed = true];        // [u0,v0,...,u3,v3], CCW
    repeated float midline_img = 5 [packed = true];     // [u0,v0,u1,v1]
    repeated float T_table_to_camera = 6 [packed = true]; // 4x4 row-major
    repeated float quad_img_global = 7 [packed = true];   // Stage 3 reprojection
    float global_iou = 8;
  }
  TablePose table_pose = 2;

  message GlobalTablePose {
    bool has_pose = 1;
    repeated float T_table_to_world = 2 [packed = true]; // 4x4 row-major
    float refined_cost = 3;
    uint32 frames_used = 4;
    float mean_iou = 5;
    float p10_iou = 6;
    float p90_iou = 7;
  }
  GlobalTablePose global_table_pose = 3;

  // Summary record only (mm)
  float table_width_mm = 4;     // 2740
  float table_height_mm = 5;    // 1525
  float net_overhang_mm = 6;    // 152.5
  float net_height_mm = 7;      // 152.5
}
```

## Configuration (`server/pongtown/config.yaml`)

- `mask_labels.table`, `mask_labels.net`, `mask_labels.person` — substring matchers.
- `ransac.distance_threshold_px`, `ransac.angle_threshold_deg`, `ransac.min_inliers`, `ransac.max_iterations`.
- `quad.area_ratio_min`, `quad.area_ratio_max`, `quad.aspect_ratio_min`, `quad.aspect_ratio_max`, `quad.parallel_threshold_deg`.
- `pnp.iou_min`, `pnp.method` (default `IPPE_SQUARE`).
- `world.refine` (bool), `world.refine_sample_size`, `world.iou_alpha`, `world.iou_beta`, `world.line_gamma`, `world.line_distance_threshold_px`.
- `overlay.every_n_frames`.

Values ship tuned against the dev fixture; the file's top comment names that recording.

## Validation plan

Manual, mirrors snookestown's pattern (offline analysis code, no automated suite):

1. `--stop-after 1` on dev fixture: spot-check ~10 overlays; 4-line fit tracks table on >80% of fully-visible frames.
2. `--stop-after 2`: mean `pnp_iou` > 0.75 on non-failed frames; per-frame pose temporally coherent (no random flips) when contact-sheeted.
3. Full pipeline: median `global_iou` ≥ median `pnp_iou`; `frames_used` > 50% of valid frames.
4. Person-occlusion regression: frames with people in front of the table do not depress IoU (since person is set-minussed out of the denominator).
5. Midline ablation: rerun with `world.line_gamma: 0.0` and confirm `global_iou` regresses — proves the midline term contributes.

## Out of scope for v1

- Dashboard panel/overlay (deferred).
- `analysis/pongtown/` interactive viewer.
- Streaming/online mode (this is offline batch like segmentation/motioncap).
- Multi-table support.
