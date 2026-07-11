use axum::extract::Multipart;
use reqwest::multipart::{Form, Part};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    error::StreamlogError,
    state::{AppState, TranscriptionStatus},
    Result,
};

#[derive(Clone, Debug, Serialize)]
pub struct TranscriptionResponse {
    pub transcription_id: Uuid,
    pub status: String,
    pub text: Option<String>,
    pub provider: Option<String>,
    pub language: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct TranscriptionQuery {
    pub synchronous: Option<bool>,
}

pub async fn transcribe_multipart(
    state: AppState,
    mut multipart: Multipart,
) -> Result<TranscriptionResponse> {
    let mut file_name = "audio.m4a".to_owned();
    let mut content_type = "audio/mp4".to_owned();
    let mut audio = Vec::new();

    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|err| StreamlogError::invalid_request(format!("invalid multipart body: {err}")))?
    {
        if field.name() != Some("file") {
            continue;
        }
        if let Some(name) = field.file_name() {
            file_name = name.to_owned();
        }
        if let Some(ct) = field.content_type() {
            content_type = ct.to_owned();
        }
        audio = field
            .bytes()
            .await
            .map_err(|err| {
                StreamlogError::invalid_request(format!("failed to read upload: {err}"))
            })?
            .to_vec();
        break;
    }

    transcribe_bytes(state, file_name, content_type, audio).await
}

pub async fn transcribe_bytes(
    state: AppState,
    file_name: String,
    content_type: String,
    audio: Vec<u8>,
) -> Result<TranscriptionResponse> {
    if audio.is_empty() {
        return Err(StreamlogError::invalid_request(
            "uploaded audio file is empty",
        ));
    }
    if audio.len() > state.config.limits.max_transcription_upload_size {
        return Err(StreamlogError::invalid_request(
            "uploaded audio exceeds size limit",
        ));
    }
    if !content_type.starts_with("audio/") {
        return Err(StreamlogError::UnsupportedMediaType {
            message: format!("expected audio/* content type, got {content_type}"),
        });
    }
    let api_key =
        std::env::var("OPENAI_API_KEY").map_err(|_| StreamlogError::ProviderUnavailable {
            message: "OPENAI_API_KEY is not configured".to_owned(),
        })?;

    let transcription_id = Uuid::new_v4();
    state.transcriptions.write().await.insert(
        transcription_id,
        TranscriptionStatus {
            transcription_id,
            status: "running".to_owned(),
            text: None,
            provider: Some("openai".to_owned()),
            language: None,
            error: None,
        },
    );

    let part = Part::bytes(audio)
        .file_name(file_name)
        .mime_str(&content_type)
        .map_err(|err| StreamlogError::invalid_request(format!("invalid content type: {err}")))?;
    let form = Form::new()
        .part("file", part)
        .text("model", "gpt-4o-mini-transcribe")
        .text("response_format", "json");

    let response = reqwest::Client::new()
        .post("https://api.openai.com/v1/audio/transcriptions")
        .bearer_auth(api_key)
        .multipart(form)
        .send()
        .await
        .map_err(|err| StreamlogError::TranscriptionFailed {
            message: format!("provider request failed: {err}"),
        })?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        state.transcriptions.write().await.insert(
            transcription_id,
            TranscriptionStatus {
                transcription_id,
                status: "failed".to_owned(),
                text: None,
                provider: Some("openai".to_owned()),
                language: None,
                error: Some(format!("{status}: {body}")),
            },
        );
        return Err(StreamlogError::TranscriptionFailed {
            message: format!("provider returned {status}"),
        });
    }
    let payload: serde_json::Value =
        response
            .json()
            .await
            .map_err(|err| StreamlogError::TranscriptionFailed {
                message: format!("invalid provider response: {err}"),
            })?;
    let text = payload
        .get("text")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .trim()
        .to_owned();
    if text.is_empty() {
        return Err(StreamlogError::TranscriptionFailed {
            message: "provider returned an empty transcript".to_owned(),
        });
    }
    let status = TranscriptionStatus {
        transcription_id,
        status: "succeeded".to_owned(),
        text: Some(text.clone()),
        provider: Some("openai".to_owned()),
        language: payload
            .get("language")
            .and_then(|value| value.as_str())
            .map(str::to_owned),
        error: None,
    };
    state
        .transcriptions
        .write()
        .await
        .insert(transcription_id, status.clone());
    Ok(TranscriptionResponse {
        transcription_id,
        status: status.status,
        text: status.text,
        provider: status.provider,
        language: status.language,
    })
}
