use std::{
    collections::BTreeMap,
    env,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::fs;
use tracing::info;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Config {
    #[serde(default = "default_listeners")]
    pub listeners: Vec<ListenerConfig>,
    #[serde(default)]
    pub recordings_root: PathBuf,
    #[serde(default)]
    pub artifact_root: Option<PathBuf>,
    #[serde(default)]
    pub logs_root: PathBuf,
    #[serde(default = "default_dashboard_static_root")]
    pub dashboard_static_root: PathBuf,
    #[serde(default)]
    pub limits: Limits,
    #[serde(default)]
    pub media: MediaConfig,
    #[serde(default = "default_enabled_planes")]
    pub enabled_planes: Vec<Plane>,
    #[serde(default)]
    pub pipelines: BTreeMap<String, PipelineConfig>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ListenerConfig {
    pub name: String,
    pub bind_host: String,
    pub bind_port: u16,
    pub base_url: String,
    pub planes: Vec<Plane>,
    #[serde(default = "default_base_path")]
    pub base_path: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum Plane {
    Outstream,
    Instream,
    Insightgen,
    Analyzers,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Limits {
    #[serde(default = "default_max_record_size")]
    pub max_record_size: u32,
    #[serde(default = "default_max_outstream_range_frames")]
    pub max_outstream_range_frames: usize,
    #[serde(default = "default_max_outstream_response_bytes")]
    pub max_outstream_response_bytes: usize,
    #[serde(default = "default_max_upload_size")]
    pub max_upload_size: usize,
    #[serde(default = "default_max_upload_chunk_size")]
    pub max_upload_chunk_size: usize,
    #[serde(default = "default_max_transcription_upload_size")]
    pub max_transcription_upload_size: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_record_size: default_max_record_size(),
            max_outstream_range_frames: default_max_outstream_range_frames(),
            max_outstream_response_bytes: default_max_outstream_response_bytes(),
            max_upload_size: default_max_upload_size(),
            max_upload_chunk_size: default_max_upload_chunk_size(),
            max_transcription_upload_size: default_max_transcription_upload_size(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MediaConfig {
    #[serde(default = "default_jpeg_quality")]
    pub jpeg_quality: u8,
    #[serde(default = "default_media_max_width")]
    pub max_width: u32,
    #[serde(default = "default_overlay_alpha")]
    pub overlay_alpha: f32,
    #[serde(default = "default_motion_tail_length")]
    pub motion_tail_length: usize,
    #[serde(default = "default_highlights_only")]
    pub highlights_only: bool,
}

impl Default for MediaConfig {
    fn default() -> Self {
        Self {
            jpeg_quality: default_jpeg_quality(),
            max_width: default_media_max_width(),
            overlay_alpha: default_overlay_alpha(),
            motion_tail_length: default_motion_tail_length(),
            highlights_only: default_highlights_only(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PipelineConfig {
    pub command: Vec<String>,
    #[serde(default)]
    pub resources: Vec<String>,
    #[serde(default)]
    pub debug_video_supported: bool,
}

impl Config {
    pub fn load() -> anyhow::Result<Self> {
        let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let project_root = project_root();
        let config_path = env::var_os("STREAMLOG_CONFIG")
            .map(PathBuf::from)
            .unwrap_or_else(|| crate_dir.join("config.yaml"));

        let mut cfg = if config_path.exists() {
            let raw = std::fs::read_to_string(&config_path)?;
            serde_yaml::from_str::<Config>(&raw)?
        } else {
            Config::default()
        };

        if cfg.recordings_root.as_os_str().is_empty() {
            cfg.recordings_root = project_root.join("recordings");
        }
        if cfg.logs_root.as_os_str().is_empty() {
            cfg.logs_root = project_root.join("logs");
        }
        cfg.recordings_root = absolutize(&cfg.recordings_root, &project_root);
        cfg.logs_root = absolutize(&cfg.logs_root, &project_root);
        cfg.dashboard_static_root = absolutize(&cfg.dashboard_static_root, &project_root);
        cfg.artifact_root = cfg
            .artifact_root
            .map(|path| absolutize(&path, &project_root));
        if cfg.pipelines.is_empty() {
            cfg.pipelines = default_pipelines();
        }

        Ok(cfg)
    }

    pub async fn ensure_dirs(&self) -> anyhow::Result<()> {
        fs::create_dir_all(&self.recordings_root).await?;
        fs::create_dir_all(&self.logs_root).await?;
        fs::create_dir_all(self.logs_root.join("analyzers")).await?;
        fs::create_dir_all(self.logs_root.join("instream")).await?;
        fs::create_dir_all(self.logs_root.join("outstream")).await?;
        fs::create_dir_all(self.logs_root.join("insightgen")).await?;
        Ok(())
    }

    pub fn default_listener(&self) -> &ListenerConfig {
        self.listeners
            .iter()
            .find(|listener| listener.name == "streamlog")
            .unwrap_or(&self.listeners[0])
    }

    pub fn artifact_root(&self) -> &Path {
        self.artifact_root
            .as_deref()
            .unwrap_or(self.recordings_root.as_path())
    }

    pub fn sanitized_hash(&self) -> String {
        let value = serde_json::to_value(self).unwrap_or_default();
        let raw = serde_json::to_vec(&value).unwrap_or_default();
        let hash = Sha256::digest(raw);
        format!("{hash:x}")
    }

    pub fn log_sanitized(&self) {
        info!(
            config_hash = self.sanitized_hash(),
            recordings_root = %self.recordings_root.display(),
            logs_root = %self.logs_root.display(),
            listeners = ?self.listeners,
            "streamlog effective configuration"
        );
    }
}

impl Default for Config {
    fn default() -> Self {
        Self {
            listeners: default_listeners(),
            recordings_root: PathBuf::new(),
            artifact_root: None,
            logs_root: PathBuf::new(),
            dashboard_static_root: default_dashboard_static_root(),
            limits: Limits::default(),
            media: MediaConfig::default(),
            enabled_planes: default_enabled_planes(),
            pipelines: default_pipelines(),
        }
    }
}

pub fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn absolutize(path: &Path, base: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    }
}

fn default_base_path() -> String {
    "/streamlog".to_owned()
}

fn default_listeners() -> Vec<ListenerConfig> {
    vec![ListenerConfig {
        name: "streamlog".to_owned(),
        bind_host: "0.0.0.0".to_owned(),
        bind_port: 8080,
        base_url: "http://localhost:8080/streamlog".to_owned(),
        planes: default_enabled_planes(),
        base_path: default_base_path(),
    }]
}

fn default_enabled_planes() -> Vec<Plane> {
    vec![
        Plane::Outstream,
        Plane::Instream,
        Plane::Insightgen,
        Plane::Analyzers,
    ]
}

fn default_dashboard_static_root() -> PathBuf {
    PathBuf::from("analysis/dashboard/dist")
}

fn default_max_record_size() -> u32 {
    512 * 1024 * 1024
}

fn default_max_outstream_range_frames() -> usize {
    240
}

fn default_max_outstream_response_bytes() -> usize {
    256 * 1024 * 1024
}

fn default_max_upload_size() -> usize {
    8 * 1024 * 1024 * 1024
}

fn default_max_upload_chunk_size() -> usize {
    128 * 1024 * 1024
}

fn default_max_transcription_upload_size() -> usize {
    50 * 1024 * 1024
}

fn default_jpeg_quality() -> u8 {
    75
}

fn default_media_max_width() -> u32 {
    480
}

fn default_overlay_alpha() -> f32 {
    0.5
}

fn default_motion_tail_length() -> usize {
    30
}

fn default_highlights_only() -> bool {
    true
}

fn default_pipelines() -> BTreeMap<String, PipelineConfig> {
    [
        (
            "segmentation",
            vec![
                "uv",
                "run",
                "python",
                "segmentation/main.py",
                "{recording_path}",
            ],
            vec!["gpu", "memory"],
            false,
        ),
        (
            "motioncap",
            vec![
                "uv",
                "run",
                "python",
                "motioncap/main.py",
                "{recording_path}",
            ],
            vec!["gpu", "cpu", "memory"],
            true,
        ),
        (
            "idoslam",
            vec!["uv", "run", "python", "idoslam/main.py", "{recording_path}"],
            vec!["cpu"],
            false,
        ),
        (
            "reconstruction",
            vec![
                "uv",
                "run",
                "python",
                "reconstruct/main.py",
                "{recording_path}",
            ],
            vec!["gpu", "disk", "memory"],
            false,
        ),
        (
            "pongtown",
            vec![
                "uv",
                "run",
                "python",
                "pongtown/main.py",
                "{recording_path}",
            ],
            vec!["cpu"],
            false,
        ),
        (
            "genspark",
            vec![
                "uv",
                "run",
                "python",
                "genspark/main.py",
                "{recording_path}",
            ],
            vec!["external-provider"],
            false,
        ),
    ]
    .into_iter()
    .map(|(name, command, resources, debug_video_supported)| {
        (
            name.to_owned(),
            PipelineConfig {
                command: command.into_iter().map(str::to_owned).collect(),
                resources: resources.into_iter().map(str::to_owned).collect(),
                debug_video_supported,
            },
        )
    })
    .collect()
}
