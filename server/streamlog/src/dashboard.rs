use axum::extract::ws::{Message, WebSocket};
use prost::Message as ProstMessage;
use serde_json::json;

use crate::{
    annotations,
    artifacts::{decode_record_key, ArtifactProto},
    ids::RecordingId,
    proto::PerceiverDataFrame,
    protoio,
    state::AppState,
    store::RangeSelector,
    Result,
};

const PREFIX_FRAME: u8 = 0x01;
const PREFIX_ANNOTATION: u8 = 0x02;
const PREFIX_PONGTOWN: u8 = 0x03;

pub async fn handle_dashboard_ws(state: AppState, mut socket: WebSocket) {
    let mut live_rx = state.live_frames_tx.subscribe();
    loop {
        tokio::select! {
            live = live_rx.recv() => {
                let Ok(live) = live else { continue; };
                let playback = state.playback.read().await;
                if playback.source == "live"
                    && playback.current_recording_id.as_deref() == Some(live.recording_id.as_str())
                {
                    let mut payload = vec![PREFIX_FRAME];
                    payload.extend(protoio::encode_delimited_raw([live.raw]));
                    if socket.send(Message::Binary(payload)).await.is_err() {
                        break;
                    }
                }
            }
            message = socket.recv() => {
                match message {
                    Some(Ok(Message::Text(text))) => {
                        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
                            if handle_dashboard_message(&state, &mut socket, value).await.is_err() {
                                let _ = socket.send(Message::Text(json!({"type":"error"}).to_string())).await;
                            }
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(_)) => break,
                    _ => {}
                }
            }
        }
    }
}

async fn handle_dashboard_message(
    state: &AppState,
    socket: &mut WebSocket,
    msg: serde_json::Value,
) -> Result<()> {
    let action = msg
        .get("action")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    match action {
        "get_stats" => {
            socket
                .send(Message::Text(
                    json!({"type":"stats"})
                        .merge(playback_stats(state).await)
                        .to_string(),
                ))
                .await
                .ok();
        }
        "seek" => {
            let start = msg
                .get("start")
                .and_then(|value| value.as_u64())
                .unwrap_or(0) as usize;
            let end = msg
                .get("end")
                .and_then(|value| value.as_u64())
                .unwrap_or((start + 1) as u64) as usize;
            let Some(recording_id) = current_recording(state).await else {
                return Ok(());
            };
            let (payload, metas) = state
                .store
                .read_range_bytes(
                    recording_id.clone(),
                    RangeSelector {
                        start_frame_index: Some(start),
                        end_frame_index: Some(end),
                        limit: Some(end.saturating_sub(start)),
                        ..Default::default()
                    },
                )
                .await?;
            if !payload.is_empty() {
                let mut prefixed = vec![PREFIX_FRAME];
                prefixed.extend(payload);
                socket.send(Message::Binary(prefixed)).await.ok();
            }
            send_matching_annotations(state, socket, &recording_id, &metas).await?;
            send_matching_pongtown(state, socket, &recording_id, &metas).await?;
        }
        "get_trajectory" => {
            if let Some(recording_id) = current_recording(state).await {
                let positions = compute_trajectory(state, recording_id).await?;
                socket
                    .send(Message::Text(
                        json!({"type":"trajectory", "positions": positions}).to_string(),
                    ))
                    .await
                    .ok();
            }
        }
        "get_sensor_data" => {
            if let Some(recording_id) = current_recording(state).await {
                let frames = compute_sensor_data(state, recording_id).await?;
                socket
                    .send(Message::Text(
                        json!({"type":"sensor_data", "frames": frames}).to_string(),
                    ))
                    .await
                    .ok();
            }
        }
        "get_annotations" => {
            if let Some(recording_id) = current_recording(state).await {
                let seg_path = state
                    .store
                    .artifact_path_for(&recording_id, "segmentation.pb");
                if let Some(frame_number) = msg.get("frame_number").and_then(|value| value.as_u64())
                {
                    if let Some(raw) = annotations::resolve_segmentation(
                        &seg_path,
                        state.config.limits.max_record_size,
                        crate::store::FrameSelector::FrameNumber(frame_number as u32),
                    )? {
                        send_prefixed(socket, PREFIX_ANNOTATION, vec![raw]).await;
                    }
                } else {
                    let records = annotations::all_segmentations(
                        &seg_path,
                        state.config.limits.max_record_size,
                    )?;
                    send_prefixed(socket, PREFIX_ANNOTATION, records).await;
                }
            }
        }
        "get_pongtown" => {
            if let Some(recording_id) = current_recording(state).await {
                let frame_numbers = if let Some(frame_number) =
                    msg.get("frame_number").and_then(|value| value.as_u64())
                {
                    vec![frame_number as u32]
                } else {
                    let start = msg
                        .get("start")
                        .and_then(|value| value.as_u64())
                        .unwrap_or(0) as usize;
                    let end = msg
                        .get("end")
                        .and_then(|value| value.as_u64())
                        .unwrap_or(start as u64) as usize;
                    let index = state.store.ensure_index(recording_id.clone()).await?;
                    annotations::frame_numbers_for_range(&index, start, end)
                };
                let records = pongtown_for_frames(state, &recording_id, &frame_numbers)?;
                send_prefixed(socket, PREFIX_PONGTOWN, records).await;
            }
        }
        _ => {}
    }
    Ok(())
}

async fn current_recording(state: &AppState) -> Option<RecordingId> {
    let playback = state.playback.read().await;
    playback
        .current_recording_id
        .as_ref()
        .and_then(|id| RecordingId::parse(id).ok())
}

async fn playback_stats(state: &AppState) -> serde_json::Value {
    let playback = state.playback.read().await.clone();
    json!({
        "source": playback.source,
        "device_id": null,
        "frame_count": playback.frame_count,
        "buffered_frames": playback.frame_count,
        "fps": playback.recording_fps,
        "recording_fps": playback.recording_fps,
        "is_replaying": playback.is_replaying,
        "first_timestamp_ns": playback.first_timestamp_ns,
        "last_timestamp_ns": playback.last_timestamp_ns,
        "intrinsics": null,
    })
}

async fn send_matching_annotations(
    state: &AppState,
    socket: &mut WebSocket,
    recording_id: &RecordingId,
    metas: &[crate::store::ResolvedFrame],
) -> Result<()> {
    let seg_path = state
        .store
        .artifact_path_for(recording_id, "segmentation.pb");
    let mut records = Vec::new();
    for meta in metas {
        if let Some(raw) = annotations::resolve_segmentation(
            &seg_path,
            state.config.limits.max_record_size,
            crate::store::FrameSelector::FrameNumber(meta.frame_number),
        )? {
            records.push(raw);
        }
    }
    send_prefixed(socket, PREFIX_ANNOTATION, records).await;
    Ok(())
}

async fn send_matching_pongtown(
    state: &AppState,
    socket: &mut WebSocket,
    recording_id: &RecordingId,
    metas: &[crate::store::ResolvedFrame],
) -> Result<()> {
    let frame_numbers = metas
        .iter()
        .map(|meta| meta.frame_number)
        .collect::<Vec<_>>();
    let records = pongtown_for_frames(state, recording_id, &frame_numbers)?;
    send_prefixed(socket, PREFIX_PONGTOWN, records).await;
    Ok(())
}

fn pongtown_for_frames(
    state: &AppState,
    recording_id: &RecordingId,
    frame_numbers: &[u32],
) -> Result<Vec<Vec<u8>>> {
    let path = state.store.artifact_path_for(recording_id, "pongtown.pb");
    if !path.exists() {
        return Ok(Vec::new());
    }
    let wanted = frame_numbers
        .iter()
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    let mut records = Vec::new();
    for raw in protoio::read_raw_records(&path, state.config.limits.max_record_size)? {
        let key = decode_record_key(ArtifactProto::Pongtown, &raw.data)?;
        if key
            .frame_number
            .is_some_and(|frame| wanted.contains(&frame))
        {
            records.push(raw.data);
        }
    }
    Ok(records)
}

async fn send_prefixed(socket: &mut WebSocket, prefix: u8, records: Vec<Vec<u8>>) {
    if records.is_empty() {
        return;
    }
    let mut payload = vec![prefix];
    payload.extend(protoio::encode_delimited_raw(records));
    let _ = socket.send(Message::Binary(payload)).await;
}

async fn compute_trajectory(
    state: &AppState,
    recording_id: RecordingId,
) -> Result<Vec<serde_json::Value>> {
    let index = state.store.ensure_index(recording_id).await?;
    let source_path = index.source_path.clone();
    tokio::task::spawn_blocking(move || {
        let mut positions = Vec::new();
        for entry in &index.entries {
            let raw = protoio::read_record_at(&source_path, entry.offset, entry.byte_length)?;
            let frame = PerceiverDataFrame::decode(raw.as_slice())?;
            let position = frame
                .camera_pose
                .and_then(|pose| pose.position)
                .map(|p| json!({"x": round6(p.x), "y": round6(p.z)}))
                .unwrap_or_else(|| json!({"x": 0.0, "y": 0.0}));
            positions.push(position);
        }
        Ok(positions)
    })
    .await
    .map_err(|err| anyhow::anyhow!("trajectory task failed: {err}"))?
}

async fn compute_sensor_data(
    state: &AppState,
    recording_id: RecordingId,
) -> Result<Vec<serde_json::Value>> {
    let index = state.store.ensure_index(recording_id).await?;
    let source_path = index.source_path.clone();
    tokio::task::spawn_blocking(move || {
        let mut frames = Vec::new();
        for entry in &index.entries {
            let raw = protoio::read_record_at(&source_path, entry.offset, entry.byte_length)?;
            let frame = PerceiverDataFrame::decode(raw.as_slice())?;
            let mut value = json!({
                "fn": entry.frame_number,
                "ts": entry.timestamp_ns,
            });
            if let Some(imu) = frame.imu_data {
                value["imu"] = json!({
                    "linear_acceleration": imu.linear_acceleration.map(|v| json!({"x": v.x, "y": v.y, "z": v.z})),
                    "angular_velocity": imu.angular_velocity.map(|v| json!({"x": v.x, "y": v.y, "z": v.z})),
                    "gravity": imu.gravity.map(|v| json!({"x": v.x, "y": v.y, "z": v.z})),
                    "magnetic_field": imu.magnetic_field.map(|v| json!({"x": v.x, "y": v.y, "z": v.z})),
                });
            }
            if let Some(gps) = frame.gps_location {
                if gps.latitude != 0.0 || gps.longitude != 0.0 {
                    value["gps"] = json!({
                        "latitude": gps.latitude,
                        "longitude": gps.longitude,
                        "altitude": gps.altitude,
                        "accuracy": gps.accuracy,
                        "bearing": gps.bearing,
                        "speed": gps.speed,
                        "timestamp_ms": gps.timestamp_ms,
                    });
                }
            }
            frames.push(value);
        }
        Ok(frames)
    })
    .await
    .map_err(|err| anyhow::anyhow!("sensor-data task failed: {err}"))?
}

fn round6(value: f32) -> f32 {
    (value * 1_000_000.0).round() / 1_000_000.0
}

trait JsonMerge {
    fn merge(self, other: serde_json::Value) -> serde_json::Value;
}

impl JsonMerge for serde_json::Value {
    fn merge(mut self, other: serde_json::Value) -> serde_json::Value {
        if let (Some(left), Some(right)) = (self.as_object_mut(), other.as_object()) {
            left.extend(right.clone());
        }
        self
    }
}
