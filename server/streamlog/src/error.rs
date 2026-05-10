use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde_json::{json, Value};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum StreamlogError {
    #[error("invalid request: {message}")]
    InvalidRequest { message: String, details: Value },
    #[error("invalid selector: {message}")]
    InvalidSelector { message: String, details: Value },
    #[error("recording not found: {recording_id}")]
    RecordingNotFound { recording_id: String },
    #[error("frame not found: {message}")]
    FrameNotFound { message: String, details: Value },
    #[error("artifact not found: {message}")]
    ArtifactNotFound { message: String },
    #[error("unsupported media type: {message}")]
    UnsupportedMediaType { message: String },
    #[error("corrupt recording: {message}")]
    CorruptRecording { message: String, details: Value },
    #[error("upload validation failed: {message}")]
    UploadValidationFailed { message: String, details: Value },
    #[error("transcription failed: {message}")]
    TranscriptionFailed { message: String },
    #[error("provider unavailable: {message}")]
    ProviderUnavailable { message: String },
    #[error("analyzer run failed: {message}")]
    AnalyzerRunFailed { message: String },
    #[error("rate limited: {message}")]
    RateLimited { message: String },
    #[error("unauthorized: {message}")]
    Unauthorized { message: String },
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    ProstDecode(#[from] prost::DecodeError),
    #[error(transparent)]
    SerdeJson(#[from] serde_json::Error),
    #[error(transparent)]
    Anyhow(#[from] anyhow::Error),
}

impl StreamlogError {
    pub fn invalid_request(message: impl Into<String>) -> Self {
        Self::InvalidRequest {
            message: message.into(),
            details: json!({}),
        }
    }

    pub fn invalid_selector(message: impl Into<String>) -> Self {
        Self::InvalidSelector {
            message: message.into(),
            details: json!({}),
        }
    }

    pub fn frame_not_found(message: impl Into<String>, details: Value) -> Self {
        Self::FrameNotFound {
            message: message.into(),
            details,
        }
    }

    fn code(&self) -> &'static str {
        match self {
            Self::InvalidRequest { .. } => "invalid_request",
            Self::InvalidSelector { .. } => "invalid_selector",
            Self::RecordingNotFound { .. } => "recording_not_found",
            Self::FrameNotFound { .. } => "frame_not_found",
            Self::ArtifactNotFound { .. } => "artifact_not_found",
            Self::UnsupportedMediaType { .. } => "unsupported_media_type",
            Self::CorruptRecording { .. } => "corrupt_recording",
            Self::UploadValidationFailed { .. } => "upload_validation_failed",
            Self::TranscriptionFailed { .. } => "transcription_failed",
            Self::ProviderUnavailable { .. } => "provider_unavailable",
            Self::AnalyzerRunFailed { .. } => "analyzer_run_failed",
            Self::RateLimited { .. } => "rate_limited",
            Self::Unauthorized { .. } => "unauthorized",
            Self::Io(_) | Self::ProstDecode(_) | Self::SerdeJson(_) | Self::Anyhow(_) => {
                "internal_error"
            }
        }
    }

    fn status(&self) -> StatusCode {
        match self {
            Self::InvalidRequest { .. } | Self::InvalidSelector { .. } => StatusCode::BAD_REQUEST,
            Self::RecordingNotFound { .. }
            | Self::FrameNotFound { .. }
            | Self::ArtifactNotFound { .. } => StatusCode::NOT_FOUND,
            Self::UnsupportedMediaType { .. } => StatusCode::UNSUPPORTED_MEDIA_TYPE,
            Self::CorruptRecording { .. } | Self::UploadValidationFailed { .. } => {
                StatusCode::UNPROCESSABLE_ENTITY
            }
            Self::ProviderUnavailable { .. } => StatusCode::SERVICE_UNAVAILABLE,
            Self::TranscriptionFailed { .. } | Self::AnalyzerRunFailed { .. } => {
                StatusCode::BAD_GATEWAY
            }
            Self::RateLimited { .. } => StatusCode::TOO_MANY_REQUESTS,
            Self::Unauthorized { .. } => StatusCode::UNAUTHORIZED,
            Self::Io(_) | Self::ProstDecode(_) | Self::SerdeJson(_) | Self::Anyhow(_) => {
                StatusCode::INTERNAL_SERVER_ERROR
            }
        }
    }

    fn details(&self) -> Value {
        match self {
            Self::InvalidRequest { details, .. }
            | Self::InvalidSelector { details, .. }
            | Self::FrameNotFound { details, .. }
            | Self::CorruptRecording { details, .. }
            | Self::UploadValidationFailed { details, .. } => details.clone(),
            Self::RecordingNotFound { recording_id } => json!({ "recording_id": recording_id }),
            _ => json!({}),
        }
    }
}

impl IntoResponse for StreamlogError {
    fn into_response(self) -> Response {
        let status = self.status();
        let body = Json(json!({
            "error": {
                "code": self.code(),
                "message": self.to_string(),
                "details": self.details(),
            }
        }));
        (status, body).into_response()
    }
}
