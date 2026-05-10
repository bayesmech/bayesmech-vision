use std::{collections::HashMap, path::Path, time::UNIX_EPOCH};

use axum::{
    body::Body,
    extract::{ws::WebSocketUpgrade, Multipart, Path as AxumPath, Query, State},
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post, put},
    Json, Router,
};
use prost::Message as ProstMessage;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_util::io::ReaderStream;
use tower_http::services::ServeDir;
use uuid::Uuid;

use crate::{
    analyzers::StartRunRequest,
    artifacts::{
        self, artifact_path, build_analysis_index, build_analysis_metadata, encode_record_slice,
        resolve_analysis, resolve_artifact, ArtifactProto, ProtoEncoding, RecordSliceFilter,
    },
    config::Plane,
    dashboard,
    error::StreamlogError,
    ids::RecordingId,
    ingest,
    proto::{
        ChatHistory, ChatTurn, DataList, GensparkResponse, GensparkSummary, HighlightSegment,
        InsightVideoResponse, ListRecordingsResponse, PerceiverDataFrame, VideoFrame,
    },
    protoio,
    state::AppState,
    store::{FrameSelector, RangeSelector, TimestampMatch},
    transcription, Result,
};

pub fn router(state: AppState) -> Router {
    let mut router = Router::new()
        .route("/streamlog/health", get(global_health))
        .route("/streamlog/outstream/health", get(outstream_health))
        .route("/streamlog/instream/health", get(instream_health))
        .route("/streamlog/insightgen/health", get(insightgen_health))
        .route("/streamlog/analyzers/health", get(analyzers_health))
        .route(
            "/streamlog/outstream/recordings/:id/source",
            get(download_source),
        )
        .route(
            "/streamlog/outstream/recordings/:id/frames/:frame_index",
            get(get_frame_by_index),
        )
        .route(
            "/streamlog/outstream/recordings/:id/frames:resolve",
            get(resolve_frame),
        )
        .route(
            "/streamlog/outstream/recordings/:id/frames",
            get(get_frame_range),
        )
        .route(
            "/streamlog/outstream/recordings/:id/layers/:layer/frames:resolve",
            get(resolve_layer_frame),
        )
        .route(
            "/streamlog/outstream/recordings/:id/layers/:layer/frames",
            get(get_layer_range),
        )
        .route(
            "/streamlog/outstream/recordings/:id/annotations/:kind",
            get(annotation_dispatch),
        )
        .route(
            "/streamlog/outstream/recordings/:id/trajectory",
            get(outstream_trajectory),
        )
        .route(
            "/streamlog/outstream/recordings/:id/sensors",
            get(outstream_sensors),
        )
        .route(
            "/streamlog/outstream/recordings/:id/analyses",
            get(analysis_recording_index_new),
        )
        .route(
            "/streamlog/outstream/recordings/:id/analyses/:analysis",
            get(analysis_recording_detail_new),
        )
        .route(
            "/streamlog/outstream/recordings/:id/analyses/:analysis/artifacts/:artifact",
            get(analysis_artifact_download_new),
        )
        .route(
            "/streamlog/outstream/recordings/:id/analyses/:analysis/records",
            get(analysis_records_new),
        )
        .route(
            "/streamlog/outstream/recordings/:id/analyses/:analysis/views/:view",
            get(analysis_view_new),
        )
        .route("/streamlog/outstream/dashboard/ws", get(dashboard_ws))
        .route("/streamlog/dashboard/ws", get(dashboard_ws))
        .route("/streamlog/instream/imports", post(start_import))
        .route(
            "/streamlog/instream/imports/:import_id/content",
            put(put_import_content),
        )
        .route(
            "/streamlog/instream/imports/:import_id/chunks/:chunk_index",
            put(put_import_chunk),
        )
        .route(
            "/streamlog/instream/imports/:import_op",
            get(get_import).post(import_action),
        )
        .route("/streamlog/instream/live/:id", get(live_ws))
        .route(
            "/streamlog/instream/transcriptions",
            post(transcription_create),
        )
        .route(
            "/streamlog/instream/transcriptions/:id",
            get(transcription_get),
        )
        .route(
            "/streamlog/insightgen/recordings",
            get(insight_recordings_json),
        )
        .route(
            "/streamlog/insightgen/recordings/:id",
            get(insight_recording_detail),
        )
        .route(
            "/streamlog/insightgen/recordings/:id/thumbnail",
            get(insight_thumbnail),
        )
        .route(
            "/streamlog/insightgen/recordings/:id/summary",
            get(insight_summary_new),
        )
        .route(
            "/streamlog/insightgen/recordings/:id/video",
            get(insight_video_new),
        )
        .route(
            "/streamlog/insightgen/recordings/:id/chat",
            get(insight_chat_new).post(insight_chat_post_new),
        )
        .route("/streamlog/analyzers/pipelines", get(pipelines))
        .route(
            "/streamlog/analyzers/recordings/:id/runs",
            post(start_analyzer_run),
        )
        .route("/streamlog/analyzers/runs", get(list_analyzer_runs))
        .route(
            "/streamlog/analyzers/runs/:run_id/logs",
            get(get_analyzer_logs),
        )
        .route(
            "/streamlog/analyzers/runs/:run_op",
            get(get_analyzer_run).post(analyzer_run_action),
        )
        .route("/api/health", get(global_health))
        .route("/api/stream", get(legacy_stream_stats))
        .route("/api/recordings", get(legacy_recordings))
        .route("/api/playback/start", post(legacy_playback_start))
        .route("/api/playback/stop", post(legacy_playback_stop))
        .route("/api/playback/live", post(legacy_playback_live))
        .route("/api/playback/status", get(legacy_playback_status))
        .route("/api/upload_recording", post(legacy_upload_recording))
        .route("/api/transcribe", post(legacy_transcribe))
        .route("/api/idoslam", get(legacy_idoslam))
        .route("/api/analysis/recordings/:id", get(legacy_analysis_index))
        .route(
            "/api/analysis/playback",
            get(legacy_analysis_playback_index),
        )
        .route(
            "/api/analysis/recordings/:id/analyses/:analysis",
            get(legacy_analysis_detail),
        )
        .route(
            "/api/analysis/playback/analyses/:analysis",
            get(legacy_playback_analysis_detail),
        )
        .route(
            "/api/analysis/recordings/:id/analyses/:analysis/artifacts/:artifact",
            get(legacy_analysis_artifact),
        )
        .route(
            "/api/analysis/playback/analyses/:analysis/artifacts/:artifact",
            get(legacy_playback_analysis_artifact),
        )
        .route(
            "/api/analysis/recordings/:id/analyses/:analysis/records",
            get(legacy_analysis_records),
        )
        .route(
            "/api/analysis/playback/analyses/:analysis/records",
            get(legacy_playback_analysis_records),
        )
        .route(
            "/api/analysis/recordings/:id/analyses/motioncap/views/tracks",
            get(legacy_motioncap_tracks),
        )
        .route(
            "/api/insightgen/recordings",
            post(legacy_insightgen_recordings),
        )
        .route("/api/insightgen/insight", get(legacy_insightgen_summary))
        .route("/api/insightgen/video", get(legacy_insightgen_video))
        .route(
            "/api/insightgen/chat",
            get(legacy_insightgen_chat).post(legacy_insightgen_chat_post),
        )
        .route(
            "/api/insightgen/regenerate",
            post(legacy_insightgen_regenerate),
        )
        .route("/ws/dashboard", get(dashboard_ws))
        .route("/ar-stream", get(legacy_ar_stream_ws))
        .with_state(state.clone());

    if state.config.dashboard_static_root.exists() {
        router = router.fallback_service(ServeDir::new(state.config.dashboard_static_root.clone()));
    }
    router
}

async fn global_health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "running",
        "version": env!("CARGO_PKG_VERSION"),
        "enabled_planes": state.config.enabled_planes,
        "logs_root": state.config.logs_root,
        "recordings_root": state.config.recordings_root,
        "config_hash": state.config.sanitized_hash(),
        "listeners": state.config.listeners.iter().map(|listener| json!({
            "name": listener.name,
            "base_url": listener.base_url,
            "bind_host": listener.bind_host,
            "bind_port": listener.bind_port,
            "planes": listener.planes,
            "base_path": listener.base_path,
            "health": "ok",
        })).collect::<Vec<_>>(),
    }))
}

async fn outstream_health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "plane": Plane::Outstream,
        "max_range_frames": state.config.limits.max_outstream_range_frames,
        "max_response_bytes": state.config.limits.max_outstream_response_bytes,
    }))
}

async fn instream_health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "plane": Plane::Instream,
        "max_upload_size": state.config.limits.max_upload_size,
        "max_upload_chunk_size": state.config.limits.max_upload_chunk_size,
        "max_transcription_upload_size": state.config.limits.max_transcription_upload_size,
        "open_imports": state.imports.read().await.len(),
    }))
}

async fn insightgen_health(State(_state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "plane": Plane::Insightgen,
        "provider": {
            "gemini_configured": std::env::var("GEMINI_API_KEY").is_ok(),
        }
    }))
}

async fn analyzers_health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "plane": Plane::Analyzers,
        "pipelines": state.analyzers.pipelines(),
    }))
}

async fn download_source(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Response> {
    let id = RecordingId::parse(id)?;
    let path = state.store.source_path_for(&id);
    if !path.exists() {
        return Err(StreamlogError::RecordingNotFound {
            recording_id: id.into_string(),
        });
    }
    stream_file(path, "application/x-protobuf").await
}

async fn get_frame_by_index(
    State(state): State<AppState>,
    AxumPath((id, frame_index)): AxumPath<(String, usize)>,
) -> Result<Response> {
    serve_frame(
        state,
        RecordingId::parse(id)?,
        FrameSelector::Index(frame_index),
    )
    .await
}

async fn resolve_frame(
    State(state): State<AppState>,
    AxumPath((id, _action)): AxumPath<(String, String)>,
    Query(query): Query<FrameResolveQuery>,
) -> Result<Response> {
    serve_frame(state, RecordingId::parse(id)?, query.selector()?).await
}

async fn serve_frame(
    state: AppState,
    recording_id: RecordingId,
    selector: FrameSelector,
) -> Result<Response> {
    let (index, entry, meta) = state.store.resolve_frame(recording_id, selector).await?;
    let bytes = state.store.read_frame_bytes(index, &entry).await?;
    protobuf_response(bytes, [frame_headers(&meta)].concat())
}

async fn get_frame_range(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Query(range): Query<RangeSelector>,
) -> Result<Response> {
    let (payload, metas) = state
        .store
        .read_range_bytes(RecordingId::parse(id)?, range)
        .await?;
    protobuf_response(
        payload,
        vec![
            (
                "x-streamlog-frame-count".to_owned(),
                metas.len().to_string(),
            ),
            (
                "x-streamlog-frame-metadata".to_owned(),
                serde_json::to_string(&metas).unwrap_or_default(),
            ),
        ],
    )
}

async fn resolve_layer_frame(
    State(state): State<AppState>,
    AxumPath((id, layer, _action)): AxumPath<(String, String, String)>,
    Query(query): Query<FrameResolveQuery>,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    let selector = query.selector()?;
    render_layer_response(state, recording_id, &layer, selector).await
}

async fn get_layer_range(
    State(state): State<AppState>,
    AxumPath((id, layer)): AxumPath<(String, String)>,
    Query(range): Query<RangeSelector>,
) -> Result<Response> {
    if layer != "raw" && layer != "understanding" {
        return Err(StreamlogError::invalid_request("unknown layer"));
    }
    let (payload, metas) = state
        .store
        .read_range_bytes(RecordingId::parse(id)?, range)
        .await?;
    Ok((
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_static("application/x-protobuf"),
            ),
            (
                header::HeaderName::from_static("x-streamlog-frame-count"),
                HeaderValue::from_str(&metas.len().to_string()).unwrap(),
            ),
        ],
        payload,
    )
        .into_response())
}

async fn render_layer_response(
    state: AppState,
    recording_id: RecordingId,
    layer: &str,
    selector: FrameSelector,
) -> Result<Response> {
    if layer != "raw" && layer != "understanding" {
        return Err(StreamlogError::invalid_request("unknown layer"));
    }
    let (index, entry, meta) = state.store.resolve_frame(recording_id, selector).await?;
    let raw = state.store.read_frame_bytes(index, &entry).await?;
    let frame = PerceiverDataFrame::decode(raw.as_slice())?;
    let Some(rgb) = frame.rgb_frame else {
        return Err(StreamlogError::frame_not_found(
            "frame has no RGB payload",
            json!({}),
        ));
    };
    let media_type = if rgb.format == 4 {
        "image/jpeg"
    } else {
        "application/octet-stream"
    };
    Ok((
        headers_from_pairs(
            [
                ("content-type".to_owned(), media_type.to_owned()),
                (
                    "x-streamlog-frame-metadata".to_owned(),
                    serde_json::to_string(&meta).unwrap_or_default(),
                ),
            ]
            .into_iter()
            .collect(),
        ),
        rgb.data,
    )
        .into_response())
}

async fn annotation_dispatch(
    State(state): State<AppState>,
    AxumPath((id, kind)): AxumPath<(String, String)>,
    Query(query): Query<FrameResolveQuery>,
) -> Result<Response> {
    if let Some(kind) = kind.strip_suffix(":resolve") {
        return resolve_annotation_impl(state, id, kind.to_owned(), query).await;
    }
    annotation_range_impl(state, id, kind, RecordSliceQuery::default()).await
}

async fn resolve_annotation_impl(
    state: AppState,
    id: String,
    kind: String,
    query: FrameResolveQuery,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    if kind != "segmentation" {
        return Err(StreamlogError::ArtifactNotFound {
            message: format!("unsupported annotation kind {kind:?}"),
        });
    }
    let path = state
        .store
        .artifact_path_for(&recording_id, "segmentation.pb");
    let raw = crate::annotations::resolve_segmentation(
        &path,
        state.config.limits.max_record_size,
        query.selector()?,
    )?
    .ok_or_else(|| StreamlogError::frame_not_found("annotation not found", json!({})))?;
    protobuf_response(raw, vec![])
}

async fn annotation_range_impl(
    state: AppState,
    id: String,
    kind: String,
    filter: RecordSliceQuery,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    if kind != "segmentation" {
        return Err(StreamlogError::ArtifactNotFound {
            message: format!("unsupported annotation kind {kind:?}"),
        });
    }
    let analysis = resolve_analysis("segmentation")?;
    let artifact = resolve_artifact(analysis, "proto")?;
    let path = artifact_path(&state.store, &recording_id, artifact);
    let (payload, count) = encode_record_slice(
        artifact,
        &path,
        filter.into(),
        state.config.limits.max_record_size,
    )?;
    protobuf_response(
        payload,
        vec![("x-streamlog-record-count".to_owned(), count.to_string())],
    )
}

async fn outstream_trajectory(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let recording_id = RecordingId::parse(id)?;
    let positions = trajectory_json(&state, recording_id).await?;
    Ok(Json(json!({ "positions": positions })))
}

async fn outstream_sensors(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let frames = sensors_json(&state, RecordingId::parse(id)?).await?;
    Ok(Json(json!({ "frames": frames })))
}

async fn analysis_recording_index_new(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let id = RecordingId::parse(id)?;
    let _ = state.store.manifest(id.clone()).await?;
    Ok(Json(serde_json::to_value(build_analysis_index(
        &state.store,
        &id,
        "recording",
        &format!("/streamlog/outstream/recordings/:id"),
    ))?))
}

async fn analysis_recording_detail_new(
    State(state): State<AppState>,
    AxumPath((id, analysis)): AxumPath<(String, String)>,
) -> Result<Json<Value>> {
    analysis_detail_json(state, id, analysis, "/streamlog/outstream").await
}

async fn analysis_artifact_download_new(
    State(state): State<AppState>,
    AxumPath((id, analysis, artifact)): AxumPath<(String, String, String)>,
) -> Result<Response> {
    artifact_download(state, id, analysis, artifact).await
}

async fn analysis_records_new(
    State(state): State<AppState>,
    AxumPath((id, analysis)): AxumPath<(String, String)>,
    Query(filter): Query<RecordSliceQuery>,
) -> Result<Response> {
    artifact_records(state, id, analysis, filter).await
}

async fn analysis_view_new(
    State(state): State<AppState>,
    AxumPath((id, analysis, view)): AxumPath<(String, String, String)>,
    Query(query): Query<HashMap<String, String>>,
) -> Result<Response> {
    analysis_view(state, id, analysis, view, query).await
}

async fn start_import(
    State(state): State<AppState>,
    Json(request): Json<ingest::StartImportRequest>,
) -> Result<Json<ingest::ImportResponse>> {
    Ok(Json(ingest::start_import(state, request).await?))
}

async fn put_import_content(
    State(state): State<AppState>,
    AxumPath(import_id): AxumPath<Uuid>,
    body: bytes::Bytes,
) -> Result<Json<ingest::ImportResponse>> {
    Ok(Json(
        ingest::put_import_content(state, import_id, body).await?,
    ))
}

async fn put_import_chunk(
    State(state): State<AppState>,
    AxumPath((import_id, chunk_index)): AxumPath<(Uuid, usize)>,
    body: bytes::Bytes,
) -> Result<Json<ingest::ImportResponse>> {
    Ok(Json(
        ingest::put_import_chunk(state, import_id, chunk_index, body).await?,
    ))
}

async fn import_action(
    State(state): State<AppState>,
    AxumPath(import_op): AxumPath<String>,
) -> Result<Json<ingest::ImportResponse>> {
    if let Some(raw_id) = import_op.strip_suffix(":complete") {
        return Ok(Json(
            ingest::complete_import(state, parse_uuid(raw_id)?).await?,
        ));
    }
    if let Some(raw_id) = import_op.strip_suffix(":cancel") {
        return Ok(Json(
            ingest::cancel_import(state, parse_uuid(raw_id)?).await?,
        ));
    }
    Err(StreamlogError::invalid_request("unknown import action"))
}

async fn get_import(
    State(state): State<AppState>,
    AxumPath(import_op): AxumPath<String>,
) -> Result<Json<Value>> {
    let import_id = parse_uuid(&import_op)?;
    let imports = state.imports.read().await;
    let session = imports
        .get(&import_id)
        .ok_or_else(|| StreamlogError::invalid_request("import session not found"))?;
    Ok(Json(serde_json::to_value(session)?))
}

async fn live_ws(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    ws: WebSocketUpgrade,
) -> Result<impl IntoResponse> {
    let recording_id = RecordingId::parse(id)?;
    Ok(ws.on_upgrade(move |socket| ingest::live_ingest_ws(state, recording_id, socket)))
}

async fn transcription_create(
    State(state): State<AppState>,
    multipart: Multipart,
) -> Result<Json<transcription::TranscriptionResponse>> {
    Ok(Json(
        transcription::transcribe_multipart(state, multipart).await?,
    ))
}

async fn transcription_get(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<Uuid>,
) -> Result<Json<Value>> {
    let status = state
        .transcriptions
        .read()
        .await
        .get(&id)
        .cloned()
        .ok_or_else(|| StreamlogError::invalid_request("transcription not found"))?;
    Ok(Json(serde_json::to_value(status)?))
}

async fn insight_recordings_json(State(state): State<AppState>) -> Result<Json<Value>> {
    let recordings = build_recording_list_json(&state).await?;
    Ok(Json(json!({ "recordings": recordings })))
}

async fn insight_recording_detail(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let id = RecordingId::parse(id)?;
    let manifest = state.store.manifest(id.clone()).await?;
    let analyses = build_analysis_index(
        &state.store,
        &id,
        "recording",
        &format!("/streamlog/outstream/recordings/:id"),
    );
    Ok(Json(json!({ "manifest": manifest, "analysis": analyses })))
}

async fn insight_thumbnail(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Response> {
    let id = RecordingId::parse(id)?;
    let thumbnail = extract_thumbnail(&state, &id)
        .await?
        .ok_or_else(|| StreamlogError::frame_not_found("thumbnail unavailable", json!({})))?;
    Ok((
        [(header::CONTENT_TYPE, HeaderValue::from_static("image/jpeg"))],
        thumbnail,
    )
        .into_response())
}

async fn insight_summary_new(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Response> {
    summary_response(state, RecordingId::parse(id)?).await
}

async fn insight_video_new(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Query(query): Query<InsightVideoQuery>,
) -> Result<Response> {
    insight_video_response(
        state,
        RecordingId::parse(id)?,
        query.layer.unwrap_or_else(|| "raw".to_owned()),
    )
    .await
}

async fn insight_chat_new(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Query(query): Query<ChatQuery>,
) -> Result<Response> {
    chat_history_response(
        state,
        RecordingId::parse(id)?,
        query.since_timestamp_ns.unwrap_or(0),
    )
    .await
}

async fn insight_chat_post_new() -> Result<Response> {
    Err(StreamlogError::ProviderUnavailable {
        message: "provider-backed follow-up chat is not implemented in the Rust runtime yet"
            .to_owned(),
    })
}

async fn pipelines(State(state): State<AppState>) -> Json<Value> {
    Json(json!({ "pipelines": state.analyzers.pipelines() }))
}

async fn start_analyzer_run(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    Json(request): Json<StartRunRequest>,
) -> Result<Json<Value>> {
    let run = state
        .analyzers
        .start_run(state.store.clone(), RecordingId::parse(id)?, request)
        .await?;
    Ok(Json(serde_json::to_value(run)?))
}

async fn list_analyzer_runs(
    State(state): State<AppState>,
    Query(query): Query<AnalyzerRunQuery>,
) -> Json<Value> {
    Json(json!({
        "runs": state.analyzers.list_runs(query.recording_id, query.pipeline).await
    }))
}

async fn get_analyzer_run(
    State(state): State<AppState>,
    AxumPath(run_id): AxumPath<Uuid>,
) -> Result<Json<Value>> {
    Ok(Json(serde_json::to_value(
        state.analyzers.get_run(run_id).await?,
    )?))
}

async fn get_analyzer_logs(
    State(state): State<AppState>,
    AxumPath(run_id): AxumPath<Uuid>,
) -> Result<Response> {
    Ok((
        [(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/plain; charset=utf-8"),
        )],
        state.analyzers.logs(run_id).await?,
    )
        .into_response())
}

async fn analyzer_run_action(
    State(state): State<AppState>,
    AxumPath(run_op): AxumPath<String>,
) -> Result<Json<Value>> {
    if let Some(raw_id) = run_op.strip_suffix(":cancel") {
        return Ok(Json(serde_json::to_value(
            state.analyzers.cancel(parse_uuid(raw_id)?).await?,
        )?));
    }
    if let Some(raw_id) = run_op.strip_suffix(":retry") {
        return Ok(Json(serde_json::to_value(
            state
                .analyzers
                .retry(state.store.clone(), parse_uuid(raw_id)?)
                .await?,
        )?));
    }
    Err(StreamlogError::invalid_request(
        "unknown analyzer run action",
    ))
}

async fn legacy_stream_stats(State(state): State<AppState>) -> Json<Value> {
    Json(playback_stats_value(&state).await)
}

async fn legacy_recordings(State(state): State<AppState>) -> Result<Json<Value>> {
    Ok(Json(
        json!({ "recordings": build_recording_list_json(&state).await? }),
    ))
}

async fn legacy_playback_start(
    State(state): State<AppState>,
    Json(body): Json<Value>,
) -> Result<Json<Value>> {
    let name = body
        .get("name")
        .and_then(|value| value.as_str())
        .ok_or_else(|| StreamlogError::invalid_request("missing name"))?;
    let recording_id = RecordingId::parse(name)?;
    ingest::set_playback_to_recording(state.clone(), recording_id.clone()).await?;
    let playback = state.playback.read().await.clone();
    Ok(Json(
        json!({ "status": "started", "name": recording_id, "frames": playback.frame_count }),
    ))
}

async fn legacy_playback_stop(State(state): State<AppState>) -> Json<Value> {
    *state.playback.write().await = crate::state::PlaybackState::default();
    Json(json!({ "status": "stopped" }))
}

async fn legacy_playback_live(State(state): State<AppState>) -> Json<Value> {
    *state.playback.write().await = crate::state::PlaybackState {
        source: "live".to_owned(),
        current_recording_id: Some("live".to_owned()),
        ..Default::default()
    };
    Json(json!({ "status": "live" }))
}

async fn legacy_playback_status(State(state): State<AppState>) -> Json<Value> {
    let playback = state.playback.read().await.clone();
    Json(json!({ "is_replaying": playback.is_replaying, "source": playback.source }))
}

async fn legacy_upload_recording(
    State(state): State<AppState>,
    multipart: Multipart,
) -> Result<Json<Value>> {
    Ok(Json(
        ingest::upload_legacy_multipart(state, multipart).await?,
    ))
}

async fn legacy_transcribe(
    State(state): State<AppState>,
    multipart: Multipart,
) -> Result<Json<Value>> {
    let response = transcription::transcribe_multipart(state, multipart).await?;
    Ok(Json(json!({ "text": response.text.unwrap_or_default() })))
}

async fn legacy_idoslam(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Result<Response> {
    let recording_id = match query.file {
        Some(file) => RecordingId::parse(
            file.trim_end_matches(".vis.pb")
                .trim_end_matches(".idoslam.pb"),
        )?,
        None => current_recording_id(&state).await?,
    };
    let path = state.store.artifact_path_for(&recording_id, "idoslam.pb");
    if !path.exists() {
        return Ok(StatusCode::NOT_FOUND.into_response());
    }
    let raw_records = protoio::read_raw_records(&path, state.config.limits.max_record_size)?;
    let Some(last) = raw_records.last() else {
        return Ok(StatusCode::NOT_FOUND.into_response());
    };
    protobuf_response(last.data.clone(), vec![])
}

async fn legacy_analysis_index(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let id = RecordingId::parse(id)?;
    Ok(Json(serde_json::to_value(build_analysis_index(
        &state.store,
        &id,
        "recording",
        &format!("/api/analysis/recordings/:id"),
    ))?))
}

async fn legacy_analysis_playback_index(State(state): State<AppState>) -> Result<Json<Value>> {
    let Ok(id) = current_recording_id(&state).await else {
        return Ok(Json(
            json!({ "recording": null, "source": state.playback.read().await.source, "analyses": [] }),
        ));
    };
    Ok(Json(serde_json::to_value(build_analysis_index(
        &state.store,
        &id,
        "playback",
        "/api/analysis/playback",
    ))?))
}

async fn legacy_analysis_detail(
    State(state): State<AppState>,
    AxumPath((id, analysis)): AxumPath<(String, String)>,
) -> Result<Json<Value>> {
    analysis_detail_json(state, id, analysis, "/api/analysis/recordings").await
}

async fn legacy_playback_analysis_detail(
    State(state): State<AppState>,
    AxumPath(analysis): AxumPath<String>,
) -> Result<Json<Value>> {
    let id = current_recording_id(&state).await?;
    analysis_detail_json(state, id.into_string(), analysis, "/api/analysis/playback").await
}

async fn legacy_analysis_artifact(
    State(state): State<AppState>,
    AxumPath((id, analysis, artifact)): AxumPath<(String, String, String)>,
) -> Result<Response> {
    artifact_download(state, id, analysis, artifact).await
}

async fn legacy_playback_analysis_artifact(
    State(state): State<AppState>,
    AxumPath((analysis, artifact)): AxumPath<(String, String)>,
) -> Result<Response> {
    let id = current_recording_id(&state).await?;
    artifact_download(state, id.into_string(), analysis, artifact).await
}

async fn legacy_analysis_records(
    State(state): State<AppState>,
    AxumPath((id, analysis)): AxumPath<(String, String)>,
    Query(filter): Query<RecordSliceQuery>,
) -> Result<Response> {
    artifact_records(state, id, analysis, filter).await
}

async fn legacy_playback_analysis_records(
    State(state): State<AppState>,
    AxumPath(analysis): AxumPath<String>,
    Query(filter): Query<RecordSliceQuery>,
) -> Result<Response> {
    let id = current_recording_id(&state).await?;
    artifact_records(state, id.into_string(), analysis, filter).await
}

async fn legacy_motioncap_tracks(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
) -> Result<Json<Value>> {
    let recording_id = RecordingId::parse(id)?;
    let path = state.store.artifact_path_for(&recording_id, "motioncap.pb");
    Ok(Json(artifacts::summarize_motioncap_tracks(
        &path,
        state.config.limits.max_record_size,
    )?))
}

async fn legacy_insightgen_recordings(State(state): State<AppState>) -> Result<Response> {
    let ids = state.store.list_recording_ids().await?;
    let mut response = ListRecordingsResponse {
        recordings: Vec::new(),
    };
    for id in ids {
        let item = insight_data_list(&state, &id).await?;
        response.recordings.push(item);
    }
    let bytes = response.encode_to_vec();
    protobuf_response(bytes, vec![])
}

async fn legacy_insightgen_summary(
    State(state): State<AppState>,
    Query(query): Query<FileQuery>,
) -> Result<Response> {
    let Some(file) = query.file else {
        return Err(StreamlogError::invalid_request("missing file"));
    };
    summary_response(state, RecordingId::parse(file)?).await
}

async fn legacy_insightgen_video(
    State(state): State<AppState>,
    Query(query): Query<InsightVideoQuery>,
) -> Result<Response> {
    let Some(file) = query.file else {
        return Err(StreamlogError::invalid_request("missing file"));
    };
    insight_video_response(
        state,
        RecordingId::parse(file)?,
        query.layer.unwrap_or_else(|| "raw".to_owned()),
    )
    .await
}

async fn legacy_insightgen_chat(
    State(state): State<AppState>,
    Query(query): Query<FileQueryWithSince>,
) -> Result<Response> {
    let Some(file) = query.file else {
        return Err(StreamlogError::invalid_request("missing file"));
    };
    chat_history_response(
        state,
        RecordingId::parse(file)?,
        query.since_timestamp_ns.unwrap_or(0),
    )
    .await
}

async fn legacy_insightgen_chat_post() -> Result<Response> {
    Err(StreamlogError::ProviderUnavailable {
        message: "provider-backed follow-up chat is not implemented in the Rust runtime yet"
            .to_owned(),
    })
}

async fn legacy_insightgen_regenerate(
    State(state): State<AppState>,
    Json(body): Json<Value>,
) -> Result<Json<Value>> {
    let file = body
        .get("file")
        .and_then(|value| value.as_str())
        .ok_or_else(|| StreamlogError::invalid_request("missing file"))?;
    let run = state
        .analyzers
        .start_run(
            state.store.clone(),
            RecordingId::parse(file)?,
            StartRunRequest {
                pipeline: "genspark".to_owned(),
                parameters: json!({}),
            },
        )
        .await?;
    Ok(Json(json!({ "status": "queued", "run_id": run.run_id })))
}

async fn dashboard_ws(State(state): State<AppState>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| dashboard::handle_dashboard_ws(state, socket))
}

async fn legacy_ar_stream_ws(
    State(state): State<AppState>,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| ingest::legacy_ar_stream_ws(state, socket))
}

async fn analysis_detail_json(
    state: AppState,
    id: String,
    analysis: String,
    prefix: &str,
) -> Result<Json<Value>> {
    let id = RecordingId::parse(id)?;
    let analysis = resolve_analysis(&analysis)?;
    let scope = if prefix.ends_with("/playback") {
        prefix.to_owned()
    } else if prefix.ends_with("/outstream") {
        format!("{prefix}/recordings/:id")
    } else {
        format!("{prefix}/:id")
    };
    Ok(Json(serde_json::to_value(build_analysis_metadata(
        &state.store,
        &id,
        analysis,
        &scope,
    ))?))
}

async fn artifact_download(
    state: AppState,
    id: String,
    analysis: String,
    artifact: String,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    let analysis = resolve_analysis(&analysis)?;
    let artifact = resolve_artifact(analysis, &artifact)?;
    if artifact.encoding == ProtoEncoding::Directory {
        return Err(StreamlogError::invalid_request(
            "directory artifacts are not directly downloadable",
        ));
    }
    let path = artifact_path(&state.store, &recording_id, artifact);
    if !path.exists() {
        return Err(StreamlogError::ArtifactNotFound {
            message: format!("artifact not available: {}", artifact.name),
        });
    }
    stream_file(
        path,
        artifact.media_type.unwrap_or("application/octet-stream"),
    )
    .await
}

async fn artifact_records(
    state: AppState,
    id: String,
    analysis: String,
    filter: RecordSliceQuery,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    let analysis = resolve_analysis(&analysis)?;
    let artifact = resolve_artifact(analysis, filter.artifact.as_deref().unwrap_or("proto"))?;
    let path = artifact_path(&state.store, &recording_id, artifact);
    if !path.exists() {
        return Err(StreamlogError::ArtifactNotFound {
            message: format!("artifact not available: {}", artifact.name),
        });
    }
    let encoding = artifacts::encoding_name(artifact.encoding).to_owned();
    let media_type = artifact.media_type.unwrap_or("application/octet-stream");
    let (payload, count) = encode_record_slice(
        artifact,
        &path,
        filter.into(),
        state.config.limits.max_record_size,
    )?;
    Ok((
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_str(media_type).unwrap(),
            ),
            (
                header::HeaderName::from_static("x-bayesmech-record-count"),
                HeaderValue::from_str(&count.to_string()).unwrap(),
            ),
            (
                header::HeaderName::from_static("x-bayesmech-encoding"),
                HeaderValue::from_str(&encoding).unwrap(),
            ),
        ],
        payload,
    )
        .into_response())
}

async fn analysis_view(
    state: AppState,
    id: String,
    analysis: String,
    view: String,
    query: HashMap<String, String>,
) -> Result<Response> {
    let recording_id = RecordingId::parse(id)?;
    let analysis = resolve_analysis(&analysis)?;
    if analysis.name == "motioncap" && view == "tracks" {
        let path = state.store.artifact_path_for(&recording_id, "motioncap.pb");
        return Ok(Json(artifacts::summarize_motioncap_tracks(
            &path,
            state.config.limits.max_record_size,
        )?)
        .into_response());
    }
    if analysis.name == "motioncap" && view == "heatmap" {
        let path = state.store.artifact_path_for(&recording_id, "motioncap.pb");
        let frame_index = query
            .get("frame_index")
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(0);
        let heatmap =
            nth_motioncap_heatmap(&path, state.config.limits.max_record_size, frame_index)?;
        return Ok((
            [(
                header::CONTENT_TYPE,
                HeaderValue::from_static("application/octet-stream"),
            )],
            heatmap,
        )
            .into_response());
    }
    Err(StreamlogError::ArtifactNotFound {
        message: format!("unknown view {}", view),
    })
}

async fn build_recording_list_json(state: &AppState) -> Result<Vec<Value>> {
    let ids = state.store.list_recording_ids().await?;
    let mut rows = Vec::new();
    for id in ids {
        let path = state.store.source_path_for(&id);
        let metadata = tokio::fs::metadata(&path).await?;
        let analyses = artifacts::all_analyses()
            .iter()
            .filter(|analysis| {
                analysis
                    .artifacts
                    .iter()
                    .any(|artifact| artifact_path(&state.store, &id, artifact).exists())
            })
            .map(|analysis| analysis.name)
            .collect::<Vec<_>>();
        rows.push(json!({
            "name": id.as_str(),
            "title": parse_title(id.as_str()),
            "size_mb": ((metadata.len() as f64 / 1024.0 / 1024.0) * 100.0).round() / 100.0,
            "recorded_at": parse_recording_timestamp(id.as_str(), &metadata),
            "has_segmentation": state.store.artifact_path_for(&id, "segmentation.pb").exists(),
            "has_idoslam": state.store.artifact_path_for(&id, "idoslam.pb").exists(),
            "has_motioncap": state.store.artifact_path_for(&id, "motioncap.pb").exists()
                || state.store.artifact_path_for(&id, "motioncap.mp4").exists(),
            "has_pongtown": state.store.artifact_path_for(&id, "pongtown.pb").exists(),
            "available_analyses": analyses,
            "analysis_url": format!("/api/analysis/recordings/{}", id.as_str()),
        }));
    }
    Ok(rows)
}

async fn insight_data_list(state: &AppState, id: &RecordingId) -> Result<DataList> {
    let genspark = state.store.artifact_path_for(id, "genspark.pb");
    let chat = state.store.artifact_path_for(id, "chat.pb");
    let (title, tags, preview_text) =
        genspark_metadata(&genspark).unwrap_or_else(|_| (None, Vec::new(), String::new()));
    let mut item = DataList {
        file_name: id.as_str().to_owned(),
        is_segmentation_available: state
            .store
            .artifact_path_for(id, "segmentation.pb")
            .exists(),
        is_genspark_available: genspark.exists(),
        is_motioncap_available: state.store.artifact_path_for(id, "motioncap.pb").exists()
            || state.store.artifact_path_for(id, "motioncap.mp4").exists(),
        image_frame: extract_thumbnail(state, id)
            .await?
            .unwrap_or_default()
            .into(),
        title: title.unwrap_or_else(|| parse_title(id.as_str())),
        tags,
        chat_message_count: chat_turn_count(&chat) as i32,
        preview_text,
    };
    if item.title == item.file_name {
        item.title = parse_title(&item.file_name);
    }
    Ok(item)
}

async fn summary_response(state: AppState, recording_id: RecordingId) -> Result<Response> {
    let path = state.store.artifact_path_for(&recording_id, "genspark.pb");
    if !path.exists() {
        return Ok(StatusCode::NOT_FOUND.into_response());
    }
    let bytes = tokio::fs::read(path).await?;
    let response = GensparkResponse::decode(bytes.as_slice())?;
    let Some(summary) = response.summary else {
        return Ok(StatusCode::NOT_FOUND.into_response());
    };
    protobuf_response(summary.encode_to_vec(), vec![])
}

async fn insight_video_response(
    state: AppState,
    recording_id: RecordingId,
    layer: String,
) -> Result<Response> {
    if layer != "raw" && layer != "understanding" {
        return Err(StreamlogError::invalid_request(
            "unknown insight video layer",
        ));
    }
    let index = state.store.ensure_index(recording_id.clone()).await?;
    let frame_indices = if state.config.media.highlights_only {
        let highlights =
            extract_highlights(&state.store.artifact_path_for(&recording_id, "genspark.pb"))?;
        clip_indices(
            &index
                .entries
                .iter()
                .map(|entry| entry.timestamp_ns)
                .collect::<Vec<_>>(),
            &highlights,
        )
    } else {
        (0..index.entries.len()).collect()
    };
    let mut response = InsightVideoResponse {
        frames: Vec::new(),
        fps: index.summary.estimated_fps as f32,
        segments: extract_highlights(&state.store.artifact_path_for(&recording_id, "genspark.pb"))?,
    };
    for frame_index in frame_indices {
        let Some(entry) = index.entries.get(frame_index) else {
            continue;
        };
        let raw = state.store.read_frame_bytes(index.clone(), entry).await?;
        let frame = PerceiverDataFrame::decode(raw.as_slice())?;
        if let Some(rgb) = frame.rgb_frame {
            if rgb.format == 4 {
                response.frames.push(VideoFrame {
                    timestamp_ns: entry.timestamp_ns as u64,
                    jpeg_data: rgb.data,
                });
            }
        }
    }
    protobuf_response(response.encode_to_vec(), vec![])
}

async fn chat_history_response(
    state: AppState,
    recording_id: RecordingId,
    since_timestamp_ns: i64,
) -> Result<Response> {
    let genspark_path = state.store.artifact_path_for(&recording_id, "genspark.pb");
    let chat_path = state.store.artifact_path_for(&recording_id, "chat.pb");
    let mut response = ChatHistory {
        file_name: recording_id.as_str().to_owned(),
        turns: Vec::new(),
        gemini_cache_name: String::new(),
        thread_created_timestamp_ns: file_mtime_ns(&genspark_path).unwrap_or(0),
        initial_turn: None,
    };
    if since_timestamp_ns < response.thread_created_timestamp_ns && genspark_path.exists() {
        if let Ok(bytes) = std::fs::read(&genspark_path) {
            if let Ok(genspark) = GensparkResponse::decode(bytes.as_slice()) {
                if let Some(summary) = genspark.summary {
                    let text = summary_markdown(&summary);
                    if !text.is_empty() {
                        response.initial_turn = Some(ChatTurn {
                            role: "model".to_owned(),
                            text,
                            timestamp_ns: response.thread_created_timestamp_ns,
                        });
                    }
                }
            }
        }
    }
    if chat_path.exists() {
        let bytes = tokio::fs::read(chat_path).await?;
        let persisted = ChatHistory::decode(bytes.as_slice())?;
        response.gemini_cache_name = persisted.gemini_cache_name;
        response.turns = persisted
            .turns
            .into_iter()
            .filter(|turn| turn.timestamp_ns > since_timestamp_ns)
            .collect();
    }
    protobuf_response(response.encode_to_vec(), vec![])
}

async fn extract_thumbnail(
    state: &AppState,
    recording_id: &RecordingId,
) -> Result<Option<Vec<u8>>> {
    let index = state.store.ensure_index(recording_id.clone()).await?;
    let target = index.summary.first_timestamp_ns + 20_000_000_000;
    let entry = index
        .entries
        .iter()
        .find(|entry| entry.timestamp_ns >= target)
        .or_else(|| index.entries.last());
    let Some(entry) = entry else {
        return Ok(None);
    };
    let raw = state.store.read_frame_bytes(index.clone(), entry).await?;
    let frame = PerceiverDataFrame::decode(raw.as_slice())?;
    Ok(frame
        .rgb_frame
        .filter(|rgb| rgb.format == 4 && !rgb.data.is_empty())
        .map(|rgb| rgb.data.to_vec()))
}

async fn trajectory_json(state: &AppState, recording_id: RecordingId) -> Result<Vec<Value>> {
    let index = state.store.ensure_index(recording_id).await?;
    let mut positions = Vec::new();
    for entry in &index.entries {
        let raw = state.store.read_frame_bytes(index.clone(), entry).await?;
        let frame = PerceiverDataFrame::decode(raw.as_slice())?;
        let position = frame
            .camera_pose
            .and_then(|pose| pose.position)
            .map(|p| json!({"x": p.x, "y": p.z}))
            .unwrap_or_else(|| json!({"x": 0.0, "y": 0.0}));
        positions.push(position);
    }
    Ok(positions)
}

async fn sensors_json(state: &AppState, recording_id: RecordingId) -> Result<Vec<Value>> {
    let index = state.store.ensure_index(recording_id).await?;
    let mut frames = Vec::new();
    for entry in &index.entries {
        let raw = state.store.read_frame_bytes(index.clone(), entry).await?;
        let frame = PerceiverDataFrame::decode(raw.as_slice())?;
        let mut value = json!({ "fn": entry.frame_number, "ts": entry.timestamp_ns });
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
}

async fn current_recording_id(state: &AppState) -> Result<RecordingId> {
    state
        .playback
        .read()
        .await
        .current_recording_id
        .clone()
        .and_then(|id| RecordingId::parse(id).ok())
        .ok_or_else(|| StreamlogError::RecordingNotFound {
            recording_id: "playback".to_owned(),
        })
}

async fn playback_stats_value(state: &AppState) -> Value {
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

async fn stream_file(path: impl AsRef<Path>, media_type: &str) -> Result<Response> {
    let path = path.as_ref().to_path_buf();
    let file = tokio::fs::File::open(&path).await?;
    let size = file.metadata().await?.len();
    let stream = ReaderStream::new(file);
    Ok(Response::builder()
        .header(header::CONTENT_TYPE, media_type)
        .header(header::CONTENT_LENGTH, size.to_string())
        .body(Body::from_stream(stream))
        .unwrap())
}

fn protobuf_response(bytes: Vec<u8>, headers: Vec<(String, String)>) -> Result<Response> {
    let mut response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/x-protobuf")
        .body(Body::from(bytes))
        .unwrap();
    for (key, value) in headers {
        response.headers_mut().insert(
            header::HeaderName::from_bytes(key.as_bytes()).map_err(|err| {
                StreamlogError::invalid_request(format!("invalid response header: {err}"))
            })?,
            HeaderValue::from_str(&value).map_err(|err| {
                StreamlogError::invalid_request(format!("invalid response header value: {err}"))
            })?,
        );
    }
    Ok(response)
}

fn headers_from_pairs(pairs: Vec<(String, String)>) -> HeaderMap {
    let mut headers = HeaderMap::new();
    for (key, value) in pairs {
        if let (Ok(name), Ok(value)) = (
            header::HeaderName::from_bytes(key.as_bytes()),
            HeaderValue::from_str(&value),
        ) {
            headers.insert(name, value);
        }
    }
    headers
}

fn frame_headers(meta: &crate::store::ResolvedFrame) -> Vec<(String, String)> {
    vec![
        (
            "x-streamlog-recording-id".to_owned(),
            meta.recording_id.clone(),
        ),
        (
            "x-streamlog-frame-index".to_owned(),
            meta.frame_index.to_string(),
        ),
        (
            "x-streamlog-frame-number".to_owned(),
            meta.frame_number.to_string(),
        ),
        (
            "x-streamlog-timestamp-ns".to_owned(),
            meta.timestamp_ns.to_string(),
        ),
        (
            "x-streamlog-relative-timestamp-ns".to_owned(),
            meta.relative_timestamp_ns.to_string(),
        ),
        (
            "x-streamlog-selector-match-delta-ns".to_owned(),
            meta.selector_match_delta_ns.to_string(),
        ),
        (
            "x-streamlog-byte-length".to_owned(),
            meta.byte_length.to_string(),
        ),
    ]
}

fn nth_motioncap_heatmap(path: &Path, max_record_size: u32, frame_index: usize) -> Result<Vec<u8>> {
    let mut index = 0usize;
    for raw in protoio::read_raw_records(path, max_record_size)? {
        let key = artifacts::decode_record_key(ArtifactProto::Motioncap, &raw.data)?;
        if key.is_summary {
            continue;
        }
        if index == frame_index {
            let record = crate::proto::MotionCaptureResponse::decode(raw.data.as_slice())?;
            return Ok(record
                .heatmap
                .map(|heatmap| heatmap.heatmap_data.to_vec())
                .unwrap_or_default());
        }
        index += 1;
    }
    Err(StreamlogError::frame_not_found(
        "motioncap heatmap not found",
        json!({ "frame_index": frame_index }),
    ))
}

fn parse_title(recording_id: &str) -> String {
    let parts = recording_id.split('_').collect::<Vec<_>>();
    if parts.len() > 2 {
        let mut title = parts[2..].join(" ");
        if let Some(first) = title.get_mut(0..1) {
            first.make_ascii_uppercase();
        }
        title
    } else {
        recording_id.to_owned()
    }
}

fn parse_recording_timestamp(recording_id: &str, metadata: &std::fs::Metadata) -> f64 {
    if recording_id.len() >= 15 {
        let date = &recording_id[0..8];
        let time = &recording_id[9..15];
        if date.chars().all(|c| c.is_ascii_digit())
            && time.chars().all(|c| c.is_ascii_digit())
            && &recording_id[8..9] == "_"
        {
            let value = format!("{date}{time}");
            if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(&value, "%Y%m%d%H%M%S") {
                return dt.and_utc().timestamp() as f64;
            }
        }
    }
    metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_secs_f64())
        .unwrap_or_default()
}

fn genspark_metadata(path: &Path) -> Result<(Option<String>, Vec<String>, String)> {
    if !path.exists() {
        return Ok((None, Vec::new(), String::new()));
    }
    let bytes = std::fs::read(path)?;
    let response = GensparkResponse::decode(bytes.as_slice())?;
    let title = response
        .summary
        .as_ref()
        .map(|summary| summary.title.trim().to_owned())
        .filter(|title| !title.is_empty());
    let preview = response
        .summary
        .as_ref()
        .map(|summary| first_preview_line(&summary.text))
        .unwrap_or_default();
    Ok((title, Vec::new(), preview))
}

fn first_preview_line(markdown: &str) -> String {
    let mut preview = markdown
        .lines()
        .map(|line| line.trim().trim_start_matches('#').trim())
        .find(|line| !line.is_empty())
        .unwrap_or_default()
        .replace('*', "");
    if preview.len() > 160 {
        preview.truncate(160);
    }
    preview
}

fn chat_turn_count(path: &Path) -> usize {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| ChatHistory::decode(bytes.as_slice()).ok())
        .map(|history| history.turns.len())
        .unwrap_or(0)
}

fn file_mtime_ns(path: &Path) -> Option<i64> {
    std::fs::metadata(path)
        .ok()?
        .modified()
        .ok()?
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|duration| duration.as_nanos() as i64)
}

fn summary_markdown(summary: &GensparkSummary) -> String {
    let mut parts = Vec::new();
    if !summary.title.trim().is_empty() {
        parts.push(format!("## {}", summary.title.trim()));
    }
    if !summary.text.trim().is_empty() {
        parts.push(summary.text.trim().to_owned());
    }
    if !summary.parameters.is_empty() {
        let mut table = "| Parameter | Value | Unit |\n|:---|:---|:---|".to_owned();
        for parameter in &summary.parameters {
            table.push_str(&format!(
                "\n| {} | {} | {} |",
                parameter.name, parameter.value, parameter.unit
            ));
        }
        parts.push(table);
    }
    parts.join("\n\n")
}

fn extract_highlights(path: &Path) -> Result<Vec<HighlightSegment>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let bytes = std::fs::read(path)?;
    let response = GensparkResponse::decode(bytes.as_slice())?;
    let mut segments = Vec::new();
    for turn in response.turns {
        for tool_call in turn.tool_calls {
            if tool_call.tool_name != "scene_emphasis" {
                continue;
            }
            if let Ok(args) = serde_json::from_str::<Value>(&tool_call.arguments_json) {
                segments.push(HighlightSegment {
                    start_time: args
                        .get("start_time")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0) as f32,
                    end_time: args.get("end_time").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32,
                    description: args
                        .get("description")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_owned(),
                });
            }
        }
    }
    Ok(segments)
}

fn clip_indices(timestamps_ns: &[i64], highlights: &[HighlightSegment]) -> Vec<usize> {
    if timestamps_ns.is_empty() {
        return Vec::new();
    }
    if highlights.is_empty() {
        return (0..timestamps_ns.len()).collect();
    }
    let first = timestamps_ns[0];
    timestamps_ns
        .iter()
        .enumerate()
        .filter_map(|(idx, ts)| {
            let t_s = (*ts - first) as f32 / 1_000_000_000.0;
            highlights
                .iter()
                .any(|seg| seg.start_time <= t_s && t_s <= seg.end_time)
                .then_some(idx)
        })
        .collect()
}

#[derive(Debug, Deserialize)]
struct FrameResolveQuery {
    frame_number: Option<u32>,
    timestamp_ns: Option<i64>,
    relative_timestamp_ns: Option<i64>,
    #[serde(rename = "match")]
    match_mode: Option<TimestampMatch>,
    tolerance_ns: Option<i64>,
}

impl FrameResolveQuery {
    fn selector(&self) -> Result<FrameSelector> {
        let provided = self.frame_number.is_some() as u8
            + self.timestamp_ns.is_some() as u8
            + self.relative_timestamp_ns.is_some() as u8;
        if provided != 1 {
            return Err(StreamlogError::invalid_selector(
                "provide exactly one of frame_number, timestamp_ns, or relative_timestamp_ns",
            ));
        }
        if let Some(frame_number) = self.frame_number {
            Ok(FrameSelector::FrameNumber(frame_number))
        } else if let Some(timestamp_ns) = self.timestamp_ns {
            Ok(FrameSelector::Timestamp {
                timestamp_ns,
                mode: self.match_mode.unwrap_or(TimestampMatch::Exact),
                tolerance_ns: self.tolerance_ns,
            })
        } else {
            Ok(FrameSelector::RelativeTimestamp {
                relative_timestamp_ns: self.relative_timestamp_ns.unwrap(),
                mode: self.match_mode.unwrap_or(TimestampMatch::Exact),
                tolerance_ns: self.tolerance_ns,
            })
        }
    }
}

#[derive(Debug, Deserialize)]
struct RecordSliceQuery {
    artifact: Option<String>,
    start_timestamp_ns: Option<i64>,
    end_timestamp_ns: Option<i64>,
    start_frame_number: Option<u32>,
    end_frame_number: Option<u32>,
    limit: Option<usize>,
    include_summary: Option<bool>,
}

impl Default for RecordSliceQuery {
    fn default() -> Self {
        Self {
            artifact: None,
            start_timestamp_ns: None,
            end_timestamp_ns: None,
            start_frame_number: None,
            end_frame_number: None,
            limit: None,
            include_summary: None,
        }
    }
}

impl From<RecordSliceQuery> for RecordSliceFilter {
    fn from(value: RecordSliceQuery) -> Self {
        Self {
            start_timestamp_ns: value.start_timestamp_ns,
            end_timestamp_ns: value.end_timestamp_ns,
            start_frame_number: value.start_frame_number,
            end_frame_number: value.end_frame_number,
            limit: value.limit,
            include_summary: value.include_summary.unwrap_or(false),
        }
    }
}

#[derive(Debug, Deserialize)]
struct FileQuery {
    file: Option<String>,
}

#[derive(Debug, Deserialize)]
struct FileQueryWithSince {
    file: Option<String>,
    since_timestamp_ns: Option<i64>,
}

#[derive(Debug, Deserialize)]
struct ChatQuery {
    since_timestamp_ns: Option<i64>,
}

#[derive(Debug, Deserialize)]
struct InsightVideoQuery {
    file: Option<String>,
    layer: Option<String>,
}

#[derive(Debug, Deserialize)]
struct AnalyzerRunQuery {
    recording_id: Option<String>,
    pipeline: Option<String>,
}

fn parse_uuid(value: &str) -> Result<Uuid> {
    Uuid::parse_str(value)
        .map_err(|err| StreamlogError::invalid_request(format!("invalid UUID: {err}")))
}
