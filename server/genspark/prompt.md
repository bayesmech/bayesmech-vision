You are analyzing a video recorded from a mobile AR (Augmented Reality) data capture system running on Android using ARCore. The video is captured from a **first-person perspective** using the phone's rear camera. In addition to the RGB frames shown here, the system simultaneously captures depth maps, IMU (accelerometer + gyroscope) data, GPS coordinates, and 3D geometry (planes and point clouds) — but only the RGB video is provided to you.

Please analyze this video and answer the following:

## 1. Scene Description

Describe what is happening in the video. What activity is taking place? Who or what objects are present? What is the spatial layout of the environment? Describe the lighting, movement, and any notable visual characteristics.

## 2. Scene Type Classification

Identify which of these scene types best describes this recording (choose one, or say "none" if none match):

- **sport-karting** — go-kart racing or karting activity, an indoor or outdoor track with karts visible
- **sport-running** — running, jogging, or athletics on a track, trail, road, or treadmill
- **sport-chess** — a chess game being played, a chess board with pieces in active use
- **experiment-pendulum** — a pendulum experiment, a freely swinging object, or a physics apparatus

Explain your classification reasoning in one or two sentences.

## 3. Temporal Highlights

Identify up to **5 notable moments** in the video where something interesting, unusual, or clearly transitional occurs — for example: a sudden change in scene, a key action, a notable object appearing, or a significant camera movement.

For each highlight provide:
- **start_time** and **end_time** in seconds from the beginning of the video (each segment must be **10 seconds or shorter**)
- A one-sentence description of what happens

Format highlights as a numbered list, for example:
1. [0.0s – 3.5s] The camera pans across a chess board showing an end-game position with few pieces remaining.
2. [11.2s – 18.0s] A kart enters the frame from the left at high speed and overtakes another kart.
