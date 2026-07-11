pub mod analyzers;
pub mod annotations;
pub mod artifacts;
pub mod config;
pub mod dashboard;
pub mod error;
pub mod ids;
pub mod ingest;
pub mod proto;
pub mod protoio;
pub mod routes;
pub mod state;
pub mod store;
pub mod transcription;

pub type Result<T> = std::result::Result<T, error::StreamlogError>;
