# Snooker Pongtown Editor

Interactive top-view editor for `.pongtown.pb` files in snooker mode.

```bash
cd server
uv run python ../analysis/snooker/main.py ../recordings/<name>/<name>.pongtown.pb
```

The editor derives sibling context files automatically:

- `<stem>.vis.pb`
- `<stem>.segmentation.pb`, falling back to `<stem>.seg.pb`

For non-standard locations:

```bash
uv run python ../analysis/snooker/main.py path/to/file.pongtown.pb \
  --vis path/to/file.vis.pb \
  --seg path/to/file.segmentation.pb
```

The editor stores snooker color in `PongtownResponse.BallPosition.track_id`:

| ID | Color |
| --- | --- |
| 1 | white |
| 2 | red |
| 3 | yellow |
| 4 | green |
| 5 | brown |
| 6 | blue |
| 7 | pink |
| 8 | black |

Edits are written back into the dashboard-native
`snooker_tracking.ball_positions` field, and mirrored into the deprecated
per-frame `ball_positions` plus trailing summary `ball_trajectory.positions` for
editor compatibility. Summary trajectory segments and bounces are cleared on
save because manual snooker edits can represent multiple balls.

On load, native snooker tracking and generated Pongtown summary positions are
copied into the editor's per-frame records when needed, and the UI opens on the
first frame containing a generated ball observation instead of frame 0.

The UI also shows smaller synchronized previews of the original RGB frame and
the segmentation overlay, with current Pongtown ball detections marked on both.

If a `.pongtown.pb` has no ball positions, the editor backfills them from
segmentation `ball` masks on startup, projects them into table coordinates using
the stored Pongtown pose, and saves the updated Pongtown file with a `.bak`
backup unless `--no-save-backfill` is passed.

The RGB preview includes a person/table occlusion signal. Green means the person
mask is below the configured threshold; red means person pixels significantly
intrude into a dilated tabletop region. Red frames are automatically replaced
with ball positions copied from the previous frame and saved unless
`--no-occlusion-fix` or `--no-save-occlusion-fix` is passed.
