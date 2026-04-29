# Scene 3 Pendulum Video Work Log

## Intention

Create a presentation-ready Scene 3 pendulum sequence showing the same source window through multiple synchronized views:

- Original video
- Label-stable segmentation masks
- Segmentation masks overlaid on the original video
- Motion tracking with interpolated phantom tracks hidden
- Pendulum period visualization derived from the steel-ball segmentation centroid

The main goal was to make the physics story visually clear: the pendulum behaves periodically, and the measured period is about 2 seconds.

## Source Data

Scene directory:

`/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum`

Inputs used:

- `IMG_0858.MOV` — original source video
- `20260429_200000.vis.pb` — recorded RGB frames
- `20260429_200000.seg.pb` — segmentation masks
- `20260429_200000.motion.pb` — motion heatmaps and tracks
- `20260429_200000.motion.mp4` — original motion tracking render

## Rendered Outputs

Initial full derived renders:

- `20260429_200000.segmentation_masks.mp4`
- `20260429_200000.segmentation_overlay.mp4`
- `20260429_200000.motion_tracking_patched.mp4`

First final cut:

`scene3-finalcut/`

- `original.mp4`
- `segmentation.mp4`
- `segment overlay.mp4`
- `motion tracking.mp4`
- `period visualization.mp4`
- `period_debug.csv`
- `period_debug.png`

Second aligned final cut:

`final-cut-2/`

- `original.mp4`
- `segmentation.mp4`
- `segment overlay.mp4`
- `motion tracking.mp4`
- `period visualization.mp4`
- `period_debug.csv`
- `period_debug.png`

The `final-cut-2` videos are all aligned to full source frames `256..585`, exactly 11 seconds at 30 fps.

## Scripts And One-Off Commands

Most trimming and render work was done with one-off Python and `ffmpeg` commands from the shell.

The saved prototype script is:

`tools/render_period_visualization.py`

It renders the period visualization by:

- reading `20260429_200000.seg.pb`
- extracting the `steel ball` mask centroid per frame
- interpolating missing centroid samples lightly
- smoothing the signal with a Savitzky-Golay filter
- identifying extrema in `x(t)`
- rendering a synchronized video overlay with a downward time-axis plot

Important bug fixed in the prototype script:

- Segmentation masks are `640x360`, while rendered video is `1920x1080`.
- The steel-ball marker initially appeared in the upper-left because the centroid was drawn in mask coordinates.
- The script now scales centroid coordinates back into video pixels before drawing the marker/trail.

## Period Measurement

The selected period is measured from steel-ball segmentation extrema:

- minimum at full frame `513`
- maximum at full frame `544`
- minimum at full frame `575`

Measured values:

- first half-period: `1.033333s`
- second half-period: `1.033333s`
- full period: `2.066667s`

Displayed value:

`T ≈ 2.07s`

## Repository Code Changes

Code changes were made on a separate worktree/branch:

Worktree:

`/Users/bhuvanesh.s/.codex/worktrees/motioncap-interpolated-render`

Branch:

`codex/motioncap-interpolated-render`

PR:

`https://github.com/bayesmech/bayesmech-vision/pull/20`

Commit:

`559ad14 Hide interpolated motion tracks in renders`

Files changed:

- `server/motioncap/main.py`
- `server/streamlog/video_layers.py`

Behavior change:

- Interpolated motion-track positions remain in protobuf data for analysis.
- Interpolated positions are no longer drawn as live markers, labels, or trajectory-tail points.
- This prevents guessed gap-fill positions from appearing as phantom tracked objects in motion tracking renders.

## Verification

Verification performed:

- `uv run python -m py_compile motioncap/main.py streamlog/video_layers.py`
- Direct `_draw_tracks` smoke test:
  - interpolated-only track leaves canvas unchanged
  - detected track renders
- Rendered the known bad Scene 3 frame through patched renderer:
  - interpolated `T3` marker/label disappeared
- Probed final videos with `ffprobe`:
  - `final-cut-2` clips are `1920x1080`, `30fps`, `330` frames, `11.000s`
- Sampled period visualization frames with `ffmpeg` and visually checked:
  - steel-ball marker is on the bob
  - period trace appears in the final section

## Notes For Future Work

- The current period visualization is a Python/OpenCV prototype, not a polished Remotion composition.
- If the visual direction is approved, consider moving `tools/render_period_visualization.py` into the main repo with command-line arguments for scene path, frame window, output path, and label.
- A Remotion version could polish text layout and transitions, but Python should remain the source of truth for measurement/debug artifacts.
- The motion tracker itself still has permissive tracking/interpolation settings; PR #20 fixes the misleading visualization, not the tracker scoring logic.
