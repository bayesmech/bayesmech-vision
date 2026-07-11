use std::fmt;

use serde::{Deserialize, Serialize};

use crate::{error::StreamlogError, Result};

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct RecordingId(String);

impl RecordingId {
    pub fn parse(value: impl AsRef<str>) -> Result<Self> {
        let raw = value.as_ref().trim();
        if raw.is_empty() {
            return Err(StreamlogError::invalid_request("recording ID is empty"));
        }
        if raw.len() > 160 {
            return Err(StreamlogError::invalid_request("recording ID is too long"));
        }
        if raw == "." || raw == ".." || raw.contains('/') || raw.contains('\\') {
            return Err(StreamlogError::invalid_request(
                "recording ID must not be a path",
            ));
        }
        if !raw
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
        {
            return Err(StreamlogError::invalid_request(
                "recording ID contains unsupported characters",
            ));
        }
        Ok(Self(raw.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_string(self) -> String {
        self.0
    }
}

impl fmt::Display for RecordingId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

pub fn sanitize_key(value: &str) -> Result<String> {
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed == "."
        || trimmed == ".."
        || trimmed.contains('/')
        || trimmed.contains('\\')
    {
        return Err(StreamlogError::invalid_request("invalid logical key"));
    }
    if !trimmed
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
    {
        return Err(StreamlogError::invalid_request(
            "logical key contains unsupported characters",
        ));
    }
    Ok(trimmed.to_ascii_lowercase().replace('-', "_"))
}

pub fn display_key(value: &str) -> Result<String> {
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed == "."
        || trimmed == ".."
        || trimmed.contains('/')
        || trimmed.contains('\\')
    {
        return Err(StreamlogError::invalid_request("invalid logical key"));
    }
    Ok(trimmed.to_owned())
}
