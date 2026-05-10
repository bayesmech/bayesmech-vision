use std::{collections::HashMap, path::PathBuf, sync::Arc};

use serde::{Deserialize, Serialize};
use tokio::sync::{broadcast, RwLock};
use uuid::Uuid;

use crate::{analyzers::AnalyzerState, config::Config, ids::RecordingId, store::RecordingStore};

#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub store: RecordingStore,
    pub playback: Arc<RwLock<PlaybackState>>,
    pub imports: Arc<RwLock<HashMap<Uuid, ImportSession>>>,
    pub transcriptions: Arc<RwLock<HashMap<Uuid, TranscriptionStatus>>>,
    pub analyzers: AnalyzerState,
    pub live_frames_tx: broadcast::Sender<LiveFrame>,
}

#[derive(Clone, Debug)]
pub struct LiveFrame {
    pub recording_id: RecordingId,
    pub raw: Vec<u8>,
}

#[derive(Clone, Debug, Serialize)]
pub struct PlaybackState {
    pub source: String,
    pub current_recording_id: Option<String>,
    pub frame_count: usize,
    pub first_timestamp_ns: i64,
    pub last_timestamp_ns: i64,
    pub recording_fps: f64,
    pub is_replaying: bool,
}

impl Default for PlaybackState {
    fn default() -> Self {
        Self {
            source: "none".to_owned(),
            current_recording_id: None,
            frame_count: 0,
            first_timestamp_ns: 0,
            last_timestamp_ns: 0,
            recording_fps: 30.0,
            is_replaying: false,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ImportSession {
    pub import_id: Uuid,
    pub recording_id: String,
    pub status: String,
    pub tmp_dir: PathBuf,
    pub content_path: PathBuf,
    pub received_bytes: u64,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TranscriptionStatus {
    pub transcription_id: Uuid,
    pub status: String,
    pub text: Option<String>,
    pub provider: Option<String>,
    pub language: Option<String>,
    pub error: Option<String>,
}

impl AppState {
    pub async fn new(config: Config) -> anyhow::Result<Self> {
        let (live_frames_tx, _) = broadcast::channel(256);
        Ok(Self {
            store: RecordingStore::new(config.clone()),
            analyzers: AnalyzerState::new(config.clone()),
            config,
            playback: Arc::new(RwLock::new(PlaybackState::default())),
            imports: Arc::new(RwLock::new(HashMap::new())),
            transcriptions: Arc::new(RwLock::new(HashMap::new())),
            live_frames_tx,
        })
    }
}
