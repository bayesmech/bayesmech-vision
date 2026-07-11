use std::{collections::HashMap, path::PathBuf, sync::Arc};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tokio::{fs, process::Command, sync::RwLock};
use uuid::Uuid;

use crate::{
    config::{project_root, Config, PipelineConfig},
    error::StreamlogError,
    ids::RecordingId,
    store::RecordingStore,
    Result,
};

#[derive(Clone)]
pub struct AnalyzerState {
    config: Config,
    runs: Arc<RwLock<HashMap<Uuid, AnalyzerRun>>>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AnalyzerRun {
    pub run_id: Uuid,
    pub recording_id: String,
    pub pipeline: String,
    pub parameters: serde_json::Value,
    pub resource_labels: Vec<String>,
    pub status: AnalyzerStatus,
    pub progress: Option<f32>,
    pub started_at: Option<DateTime<Utc>>,
    pub ended_at: Option<DateTime<Utc>>,
    pub log_path: PathBuf,
    pub produced_artifacts: Vec<String>,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AnalyzerStatus {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StartRunRequest {
    pub pipeline: String,
    #[serde(default)]
    pub parameters: serde_json::Value,
}

impl AnalyzerState {
    pub fn new(config: Config) -> Self {
        Self {
            config,
            runs: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub fn pipelines(&self) -> Vec<PipelineInfo> {
        self.config
            .pipelines
            .iter()
            .map(|(name, pipeline)| PipelineInfo {
                name: name.clone(),
                accepted_inputs: vec!["recording".to_owned()],
                produced_artifacts: produced_artifacts_for(name),
                resource_labels: pipeline.resources.clone(),
                configurable_parameters: serde_json::json!({}),
                debug_video_output_supported: pipeline.debug_video_supported,
            })
            .collect()
    }

    pub async fn start_run(
        &self,
        store: RecordingStore,
        recording_id: RecordingId,
        request: StartRunRequest,
    ) -> Result<AnalyzerRun> {
        let Some(pipeline) = self.config.pipelines.get(&request.pipeline).cloned() else {
            return Err(StreamlogError::invalid_request(format!(
                "unknown analyzer pipeline {:?}",
                request.pipeline
            )));
        };
        let source_path = store.source_path_for(&recording_id);
        if !source_path.exists() {
            return Err(StreamlogError::RecordingNotFound {
                recording_id: recording_id.into_string(),
            });
        }

        let run_id = Uuid::new_v4();
        let log_path = self
            .config
            .logs_root
            .join("analyzers")
            .join(format!("{run_id}.log"));
        let run = AnalyzerRun {
            run_id,
            recording_id: recording_id.as_str().to_owned(),
            pipeline: request.pipeline.clone(),
            parameters: request.parameters,
            resource_labels: pipeline.resources.clone(),
            status: AnalyzerStatus::Queued,
            progress: None,
            started_at: None,
            ended_at: None,
            log_path: log_path.clone(),
            produced_artifacts: Vec::new(),
            error: None,
        };
        self.runs.write().await.insert(run_id, run.clone());

        let state = self.clone();
        let recording_name = recording_id.as_str().to_owned();
        tokio::spawn(async move {
            state
                .run_process(run_id, pipeline, source_path, recording_name)
                .await;
        });

        Ok(run)
    }

    pub async fn list_runs(
        &self,
        recording_id: Option<String>,
        pipeline: Option<String>,
    ) -> Vec<AnalyzerRun> {
        self.runs
            .read()
            .await
            .values()
            .filter(|run| {
                recording_id
                    .as_ref()
                    .is_none_or(|id| &run.recording_id == id)
                    && pipeline.as_ref().is_none_or(|name| &run.pipeline == name)
            })
            .cloned()
            .collect()
    }

    pub async fn get_run(&self, run_id: Uuid) -> Result<AnalyzerRun> {
        self.runs.read().await.get(&run_id).cloned().ok_or_else(|| {
            StreamlogError::ArtifactNotFound {
                message: format!("analyzer run not found: {run_id}"),
            }
        })
    }

    pub async fn logs(&self, run_id: Uuid) -> Result<String> {
        let run = self.get_run(run_id).await?;
        Ok(fs::read_to_string(run.log_path).await.unwrap_or_default())
    }

    pub async fn cancel(&self, run_id: Uuid) -> Result<AnalyzerRun> {
        let mut runs = self.runs.write().await;
        let Some(run) = runs.get_mut(&run_id) else {
            return Err(StreamlogError::ArtifactNotFound {
                message: format!("analyzer run not found: {run_id}"),
            });
        };
        if matches!(run.status, AnalyzerStatus::Queued | AnalyzerStatus::Running) {
            run.status = AnalyzerStatus::Cancelled;
            run.ended_at = Some(Utc::now());
        }
        Ok(run.clone())
    }

    pub async fn retry(&self, store: RecordingStore, run_id: Uuid) -> Result<AnalyzerRun> {
        let previous = self.get_run(run_id).await?;
        self.start_run(
            store,
            RecordingId::parse(previous.recording_id)?,
            StartRunRequest {
                pipeline: previous.pipeline,
                parameters: previous.parameters,
            },
        )
        .await
    }

    async fn run_process(
        &self,
        run_id: Uuid,
        pipeline: PipelineConfig,
        recording_path: PathBuf,
        recording_name: String,
    ) {
        self.update_run(run_id, |run| {
            run.status = AnalyzerStatus::Running;
            run.started_at = Some(Utc::now());
        })
        .await;

        let command = pipeline
            .command
            .iter()
            .map(|part| {
                part.replace("{recording_path}", &recording_path.to_string_lossy())
                    .replace("{recording_id}", &recording_name)
            })
            .collect::<Vec<_>>();
        let Some((program, args)) = command.split_first() else {
            self.fail_run(run_id, "empty analyzer command").await;
            return;
        };

        let output = Command::new(program)
            .args(args)
            .current_dir(project_root().join("server"))
            .output()
            .await;

        match output {
            Ok(output) => {
                let log = format!(
                    "$ {}\n\n--- stdout ---\n{}\n\n--- stderr ---\n{}",
                    command.join(" "),
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
                if let Some(run) = self.runs.read().await.get(&run_id) {
                    let _ = fs::write(&run.log_path, log).await;
                }
                if output.status.success() {
                    self.update_run(run_id, |run| {
                        run.status = AnalyzerStatus::Succeeded;
                        run.ended_at = Some(Utc::now());
                        run.produced_artifacts = produced_artifacts_for(&run.pipeline);
                    })
                    .await;
                } else {
                    self.fail_run(run_id, format!("process exited with {}", output.status))
                        .await;
                }
            }
            Err(err) => self.fail_run(run_id, err.to_string()).await,
        }
    }

    async fn fail_run(&self, run_id: Uuid, message: impl Into<String>) {
        let message = message.into();
        self.update_run(run_id, |run| {
            run.status = AnalyzerStatus::Failed;
            run.ended_at = Some(Utc::now());
            run.error = Some(message);
        })
        .await;
    }

    async fn update_run(&self, run_id: Uuid, f: impl FnOnce(&mut AnalyzerRun)) {
        let mut runs = self.runs.write().await;
        if let Some(run) = runs.get_mut(&run_id) {
            if run.status != AnalyzerStatus::Cancelled {
                f(run);
            }
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct PipelineInfo {
    pub name: String,
    pub accepted_inputs: Vec<String>,
    pub produced_artifacts: Vec<String>,
    pub resource_labels: Vec<String>,
    pub configurable_parameters: serde_json::Value,
    pub debug_video_output_supported: bool,
}

fn produced_artifacts_for(name: &str) -> Vec<String> {
    match name {
        "segmentation" => vec!["segmentation/proto".to_owned()],
        "motioncap" => vec!["motioncap/proto".to_owned(), "motioncap/video".to_owned()],
        "idoslam" => vec!["idoslam/proto".to_owned()],
        "reconstruction" => vec![
            "reconstruction/proto".to_owned(),
            "reconstruction/splat".to_owned(),
        ],
        "pongtown" => vec!["pongtown/proto".to_owned()],
        "genspark" => vec!["genspark/proto".to_owned()],
        _ => Vec::new(),
    }
}
