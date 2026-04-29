# Scene 3 Period Visualization Work Log

## Scope

This note is only for the pendulum period visualization renderer added in:

`tools/render_period_visualization.py`

It is not repository-wide guidance, not a general Scene 3 video pipeline log, and not a motion-tracking or segmentation-stability work log. Other agents should use it only when working on the pendulum period visualization task.

## Intention

Create a presentation clip that makes the pendulum period visible from the tracked bob motion. The visualization follows the `steel ball` segmentation centroid, plots its horizontal displacement against a downward time axis, marks one min-max-min extrema triplet, and displays the measured period.

The intended Scene 3 presentation value is:

`T ~= 2.07s`

## Inputs Used For The Prototype Render

Scene directory:

`/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum`

Relevant files:

- `IMG_0858.MOV` as the source video.
- `20260429_200000.seg.pb` as the segmentation source for the bob centroid.

The renderer does not require motion-tracking protobufs. It measures from segmentation masks, not from `20260429_200000.motion.pb`.

## Renderer Behavior

`tools/render_period_visualization.py`:

- Reads length-delimited `SegmentationResponse` records.
- Finds the mask with label `steel ball`.
- Computes the centroid for that mask in each frame.
- Interpolates missing centroid samples so the plotted curve remains continuous.
- Smooths the centroid path for extrema detection.
- Uses an explicit min-max-min triplet when `--period-frames` is provided.
- Draws the bob marker, path, time-axis trace, extrema markers, and period text onto the source video.

Important coordinate fix:

- Scene 3 segmentation masks are `640x360`, while the rendered video is `1920x1080`.
- The renderer scales centroid coordinates from mask pixels into video pixels before drawing. Without this, the bob marker appears in the upper-left instead of on the pendulum bob.

## Prototype Command

From the `server/` directory:

```bash
uv run python ../tools/render_period_visualization.py \
  --scene-dir "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum" \
  --source-video "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum/IMG_0858.MOV" \
  --seg "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum/20260429_200000.seg.pb" \
  --output "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum/final-cut-2/period visualization.mp4" \
  --debug-csv "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum/final-cut-2/period_debug.csv" \
  --debug-png "/Users/bhuvanesh.s/Files/BayesMechYCVideo/Scene3 Pendulum/final-cut-2/period_debug.png" \
  --start-frame 256 \
  --frames 330 \
  --period-frames 513,544,575
```

## Period Measurement Used

The selected extrema were:

- minimum at full source frame `513`
- maximum at full source frame `544`
- minimum at full source frame `575`

At `30fps`:

- first half-period: `1.033333s`
- second half-period: `1.033333s`
- full period: `2.066667s`

The rendered label rounds this to:

`T ~= 2.07s`

## Validation

Validation performed for this branch:

```bash
cd server
uv run python -m py_compile ../tools/render_period_visualization.py
uv run python ../tools/render_period_visualization.py --help
```

Visual checks performed on the prototype output:

- The yellow bob marker is drawn on the pendulum bob, not in mask-space coordinates.
- The trace follows the bob motion through the selected window.
- The displayed period corresponds to frames `513,544,575`.
