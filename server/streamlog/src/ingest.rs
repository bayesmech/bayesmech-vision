use std::path::PathBuf;

use axum::{
    extract::ws::{Message, WebSocket},
    http::StatusCode,
};
use bytes::Bytes;
use prost::Message as ProstMessage;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::{fs, io::AsyncWriteExt};
use uuid::Uuid;

use crate::{
    ids::RecordingId,
    proto::PerceiverDataFrame,
    protoio,
    state::{AppState, ImportSession, LiveFrame, PlaybackState},
    store::build_recording_index,
    Result,
};

#[derive(Clone, Debug, Deserialize)]
pub struct StartImportRequest {
    pub recording_id: Option<String>,
    pub file_name: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ImportResponse {
    pub import_id: Uuid,
    pub recording_id: String,
    pub status: String,
    pub received_bytes: u64,
    pub sha256: Option<String>,
}

pub async fn start_import(state: AppState, request: StartImportRequest) -> Result<ImportResponse> {
    let base = request
        .recording_id
        .or_else(|| {
            request
                .file_name
                .map(|name| name.trim_end_matches(".vis.pb").to_owned())
        })
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let recording_id = RecordingId::parse(base)?;
    let import_id = Uuid::new_v4();
    let tmp_dir = state
        .config
        .logs_root
        .join("instream")
        .join(format!("import-{import_id}"));
    fs::create_dir_all(&tmp_dir).await?;
    let content_path = tmp_dir.join("content.vis.pb");
    let session = ImportSession {
        import_id,
        recording_id: recording_id.as_str().to_owned(),
        status: "open".to_owned(),
        tmp_dir,
        content_path,
        received_bytes: 0,
        error: None,
    };
    state.imports.write().await.insert(import_id, session);
    Ok(ImportResponse {
        import_id,
        recording_id: recording_id.into_string(),
        status: "open".to_owned(),
        received_bytes: 0,
        sha256: None,
    })
}

pub async fn put_import_content(
    state: AppState,
    import_id: Uuid,
    body: Bytes,
) -> Result<ImportResponse> {
    if body.len() > state.config.limits.max_upload_size {
        return Err(crate::error::StreamlogError::invalid_request(
            "upload exceeds max size",
        ));
    }
    let mut imports = state.imports.write().await;
    let session = imports
        .get_mut(&import_id)
        .ok_or_else(|| crate::error::StreamlogError::invalid_request("import session not found"))?;
    fs::write(&session.content_path, &body).await?;
    session.received_bytes = body.len() as u64;
    session.status = "uploaded".to_owned();
    Ok(ImportResponse {
        import_id,
        recording_id: session.recording_id.clone(),
        status: session.status.clone(),
        received_bytes: session.received_bytes,
        sha256: Some(format!("{:x}", Sha256::digest(&body))),
    })
}

pub async fn put_import_chunk(
    state: AppState,
    import_id: Uuid,
    chunk_index: usize,
    body: Bytes,
) -> Result<ImportResponse> {
    if body.len() > state.config.limits.max_upload_chunk_size {
        return Err(crate::error::StreamlogError::invalid_request(
            "chunk exceeds max size",
        ));
    }
    let mut imports = state.imports.write().await;
    let session = imports
        .get_mut(&import_id)
        .ok_or_else(|| crate::error::StreamlogError::invalid_request("import session not found"))?;
    let chunk_path = session.tmp_dir.join(format!("chunk-{chunk_index:08}.part"));
    fs::write(chunk_path, &body).await?;
    session.received_bytes += body.len() as u64;
    session.status = "chunking".to_owned();
    Ok(ImportResponse {
        import_id,
        recording_id: session.recording_id.clone(),
        status: session.status.clone(),
        received_bytes: session.received_bytes,
        sha256: None,
    })
}

pub async fn complete_import(state: AppState, import_id: Uuid) -> Result<ImportResponse> {
    let session = {
        let imports = state.imports.read().await;
        imports.get(&import_id).cloned().ok_or_else(|| {
            crate::error::StreamlogError::invalid_request("import session not found")
        })?
    };
    assemble_chunks_if_needed(&session).await?;
    let content = fs::read(&session.content_path).await?;
    if content.len() > state.config.limits.max_upload_size {
        return Err(crate::error::StreamlogError::invalid_request(
            "upload exceeds max size",
        ));
    }
    let sha256 = format!("{:x}", Sha256::digest(&content));
    let recording_id = RecordingId::parse(&session.recording_id)?;
    let dest = state.store.source_path_for(&recording_id);
    validate_recording(&session.content_path, state.config.limits.max_record_size).await?;
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).await?;
    }
    let tmp_publish = dest.with_extension("vis.pb.tmp");
    fs::write(&tmp_publish, content).await?;
    fs::rename(&tmp_publish, &dest).await?;
    state.store.invalidate(&recording_id).await;
    let manifest = state.store.manifest(recording_id.clone()).await?;
    {
        let mut imports = state.imports.write().await;
        if let Some(session) = imports.get_mut(&import_id) {
            session.status = "complete".to_owned();
            session.received_bytes = manifest.source_size_bytes;
        }
    }
    Ok(ImportResponse {
        import_id,
        recording_id: recording_id.into_string(),
        status: "complete".to_owned(),
        received_bytes: manifest.source_size_bytes,
        sha256: Some(sha256),
    })
}

pub async fn cancel_import(state: AppState, import_id: Uuid) -> Result<ImportResponse> {
    let session = state
        .imports
        .write()
        .await
        .remove(&import_id)
        .ok_or_else(|| crate::error::StreamlogError::invalid_request("import session not found"))?;
    let _ = fs::remove_dir_all(&session.tmp_dir).await;
    Ok(ImportResponse {
        import_id,
        recording_id: session.recording_id,
        status: "cancelled".to_owned(),
        received_bytes: session.received_bytes,
        sha256: None,
    })
}

pub async fn live_ingest_ws(state: AppState, recording_id: RecordingId, mut socket: WebSocket) {
    while let Some(message) = socket.recv().await {
        match message {
            Ok(Message::Binary(bytes)) => {
                for raw in split_live_payload(&bytes, state.config.limits.max_record_size) {
                    if let Ok(raw) = raw {
                        if state
                            .store
                            .append_live_frame(recording_id.clone(), raw.clone())
                            .await
                            .is_ok()
                        {
                            let _ = state.live_frames_tx.send(LiveFrame {
                                recording_id: recording_id.clone(),
                                raw,
                            });
                        }
                    }
                }
            }
            Ok(Message::Close(_)) | Err(_) => break,
            _ => {}
        }
    }
}

pub async fn legacy_ar_stream_ws(state: AppState, socket: WebSocket) {
    let recording_id = RecordingId::parse("live").expect("static ID is valid");
    *state.playback.write().await = PlaybackState {
        source: "live".to_owned(),
        current_recording_id: Some("live".to_owned()),
        ..PlaybackState::default()
    };
    live_ingest_ws(state, recording_id, socket).await;
}

pub async fn upload_legacy_multipart(
    state: AppState,
    mut multipart: axum::extract::Multipart,
) -> Result<serde_json::Value> {
    while let Some(field) = multipart.next_field().await.map_err(|err| {
        crate::error::StreamlogError::invalid_request(format!("invalid multipart body: {err}"))
    })? {
        if field.name() != Some("file") {
            continue;
        }
        let file_name = field.file_name().unwrap_or("upload.vis.pb").to_owned();
        if !file_name.ends_with(".vis.pb") {
            return Err(crate::error::StreamlogError::invalid_request(
                "expected a .vis.pb file",
            ));
        }
        let recording_id = RecordingId::parse(file_name.trim_end_matches(".vis.pb"))?;
        let bytes = field.bytes().await.map_err(|err| {
            crate::error::StreamlogError::invalid_request(format!("failed to read file: {err}"))
        })?;
        let import = start_import(
            state.clone(),
            StartImportRequest {
                recording_id: Some(recording_id.as_str().to_owned()),
                file_name: Some(file_name),
            },
        )
        .await?;
        put_import_content(state.clone(), import.import_id, bytes).await?;
        let complete = complete_import(state.clone(), import.import_id).await?;
        set_playback_to_recording(state.clone(), RecordingId::parse(&complete.recording_id)?)
            .await?;
        return Ok(serde_json::json!({
            "status": "uploaded_and_playing",
            "name": complete.recording_id,
            "size": complete.received_bytes,
        }));
    }
    Err(crate::error::StreamlogError::invalid_request(
        "multipart body has no file field",
    ))
}

pub async fn set_playback_to_recording(state: AppState, recording_id: RecordingId) -> Result<()> {
    let manifest = state.store.manifest(recording_id.clone()).await?;
    *state.playback.write().await = PlaybackState {
        source: "file".to_owned(),
        current_recording_id: Some(recording_id.into_string()),
        frame_count: manifest.frame_count,
        first_timestamp_ns: manifest.first_timestamp_ns,
        last_timestamp_ns: manifest.last_timestamp_ns,
        recording_fps: manifest.estimated_fps,
        is_replaying: false,
    };
    Ok(())
}

async fn assemble_chunks_if_needed(session: &ImportSession) -> Result<()> {
    if session.content_path.exists() {
        return Ok(());
    }
    let mut chunks = fs::read_dir(&session.tmp_dir).await?;
    let mut paths = Vec::<PathBuf>::new();
    while let Some(entry) = chunks.next_entry().await? {
        let path = entry.path();
        if path.extension().is_some_and(|ext| ext == "part") {
            paths.push(path);
        }
    }
    paths.sort();
    let mut out = fs::File::create(&session.content_path).await?;
    for path in paths {
        let bytes = fs::read(path).await?;
        out.write_all(&bytes).await?;
    }
    out.flush().await?;
    Ok(())
}

async fn validate_recording(path: &PathBuf, max_record_size: u32) -> Result<()> {
    let path = path.clone();
    tokio::task::spawn_blocking(move || {
        let id = RecordingId::parse("validation")?;
        let _ = build_recording_index(id, path, max_record_size)?;
        Ok(())
    })
    .await
    .map_err(|err| anyhow::anyhow!("validation task failed: {err}"))?
}

fn split_live_payload(bytes: &[u8], max_record_size: u32) -> Vec<Result<Vec<u8>>> {
    if let Ok(frames) =
        protoio::decode_delimited_buffer::<PerceiverDataFrame>(bytes, max_record_size)
    {
        if !frames.is_empty() {
            let mut offset = 0usize;
            let mut raw = Vec::new();
            while offset + 4 <= bytes.len() {
                let len =
                    u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
                offset += 4;
                if offset + len > bytes.len() {
                    break;
                }
                raw.push(Ok(bytes[offset..offset + len].to_vec()));
                offset += len;
            }
            return raw;
        }
    }
    match PerceiverDataFrame::decode(bytes) {
        Ok(_) => vec![Ok(bytes.to_vec())],
        Err(err) => vec![Err(err.into())],
    }
}

pub fn status_no_content() -> StatusCode {
    StatusCode::NO_CONTENT
}
