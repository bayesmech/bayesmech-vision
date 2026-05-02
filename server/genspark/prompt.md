You are analyzing a video recorded from a mobile AR (Augmented Reality) data capture system running on Android using ARCore. The video is captured from a **first-person perspective** using the phone's rear camera. In addition to the RGB frames shown here, the system simultaneously captures depth maps, IMU (accelerometer + gyroscope) data, GPS coordinates, and 3D geometry (planes and point clouds) — but only the RGB video is provided to you.

## Tools Available

You have access to analysis tools for the recording. Start with **`list_available_analyses()`** and **`get_recording_metadata()`** when quantitative analyzer data could help.

Core workflow:

- **`scene_context(type)`** - call this once after identifying the scene type; it returns scene-specific analysis instructions you must follow
- **`scene_emphasis(start_time, end_time, description)`** - mark a notable moment (7-20 seconds); provide a description of what is happening and why it is notable

Analyzer data tools:

- **Segmentation** - use `segmentation_list_identified_objects()`, `segmentation_get_objects_at_time(t)`, `segmentation_get_object_track(object_id, ...)`, and `segmentation_get_relative_position(object_1, object_2, ...)` for semantic objects, object positions, and relative image-space layout; pass `include_geometry=true` to `segmentation_get_objects_at_time` only when centroid/bbox geometry is needed
- **Motion capture** - use `get_motioncap_tracks(start_time, end_time, segmentation=false)`, `motioncap_list_tracks(...)`, `motioncap_get_track_summary(...)`, `motioncap_get_moving_objects(...)`, `motioncap_find_extrema(...)`, and `motioncap_estimate_period(...)` for object motion, oscillation, speed, and trajectory; set `segmentation=true` when semantic segmentation centroid tracks are more useful than RAFT/motioncap tracks
- **Pongtown** - use `pongtown_get_table_pose_at_time(t)`, `pongtown_get_ball_trajectory(...)`, `pongtown_get_table_bounces(...)`, and `pongtown_get_ball_speed(...)` for ping-pong table pose, ball path, bounces, responder-relative side labels, and speed
- **SLAM** - use `slam_get_map(...)`, `slam_get_pose_at_time(t)`, `slam_get_velocity_at_time(t)`, `slam_get_position_on_road(t)`, `slam_get_road_width_at_time(t)`, `slam_get_lap_progress(t)`, and `slam_get_motion_between(...)` for ego motion, road position, road width, lap progress, and map-level reasoning

Do not assume an analyzer exists; call `list_available_analyses()` first for analyzer-specific questions. If a tool reports that data is unavailable, timed out, or returned a server error, do not retry that same tool call; continue from the video evidence and state the analyzer/tool output was unavailable.

**Always call `scene_context` after classifying the scene, and follow any additional instructions it returns.**

For **experiment-pendulum** scenes, do not stop after inspecting raw motion tracks. After `scene_context(type="experiment-pendulum")` and `get_motioncap_tracks(...)`, you must call `motioncap_find_extrema(...)` and `motioncap_estimate_period(...)` on at least one likely pendulum track before producing your final answer. If RAFT/motioncap tracks are ambiguous, also inspect segmentation-derived tracks with `segmentation=true` and run the same extrema/period tools on the best semantic pendulum or bob track. The final answer must include a numeric period estimate, extremum timestamps, and amplitude estimate when any tool output is available; use "unavailable" only if the required tool calls returned unavailable/error results.

---

Please analyze this video and answer the following:

## 1. Scene Description

Describe what is happening in the video. What activity is taking place? Who or what objects are present? What is the spatial layout of the environment? Describe the lighting, movement, and any notable visual characteristics.

## 2. Scene Type Classification

Identify which of these scene types best describes this recording (choose one, or say "none" if none match):

- **sport-karting** — go-kart racing or karting activity, an indoor or outdoor track with karts visible
- **sport-running** — running, jogging, or athletics on a track, trail, road, or treadmill
- **sport-chess** — a chess game being played, a chess board with pieces in active use
- **sport-snooker** — snooker or cue-sport play, a snooker/pool table, cue, balls, pockets, or shot setup
- **sport-pingpong** — table tennis or ping-pong, a table, net, paddles, players, ball rallies, serves, or bounces
- **sport-bike** — motor bike riding from a rider/passenger perspective, road/track travel, turns, speed changes, or traffic/obstacle interactions
- **experiment-pendulum** — a pendulum experiment, a freely swinging object, or a physics apparatus

Explain your classification reasoning in one or two sentences. Then call `scene_context(type)` with your classification and follow any instructions returned.

## 3. Temporal Highlights

Identify up to **3 notable moments** in the video where something interesting, unusual, or clearly transitional occurs — for example: a sudden change in scene, a key action, a notable object appearing, or a significant camera movement.

For each highlight call `scene_emphasis(start_time, end_time, description)` with:
- **start_time** and **end_time** in seconds from the beginning of the video (each segment should be **7–20 seconds**)
- A description of what happens and why it is notable
