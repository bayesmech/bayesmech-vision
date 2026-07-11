use std::path::{Path, PathBuf};

use prost::Message;
use serde::Serialize;
use serde_json::{json, Value};

use crate::{
    error::StreamlogError,
    ids::{sanitize_key, RecordingId},
    proto::{
        ChatHistory, GensparkResponse, IdoSlamResponse, MotionCaptureResponse, PongtownResponse,
        ReconstructionResponse, SegmentationResponse, SnookerResponse,
    },
    protoio,
    store::{recording_file, RecordingStore},
    Result,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum ProtoEncoding {
    LengthDelimited,
    Single,
    Binary,
    Directory,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum ArtifactProto {
    Segmentation,
    Motioncap,
    IdoSlam,
    Genspark,
    Chat,
    Reconstruction,
    Snookertown,
    Pongtown,
}

#[derive(Clone, Debug, Serialize)]
pub struct ArtifactSpec {
    pub name: &'static str,
    pub title: &'static str,
    pub suffix: &'static str,
    pub media_type: Option<&'static str>,
    pub kind: &'static str,
    pub encoding: ProtoEncoding,
    pub aliases: &'static [&'static str],
    pub proto_message_type: Option<&'static str>,
    pub proto: Option<ArtifactProto>,
    pub sliceable: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct ViewSpec {
    pub name: &'static str,
    pub title: &'static str,
    pub media_type: &'static str,
    pub query_parameters: &'static [&'static str],
}

#[derive(Clone, Debug, Serialize)]
pub struct AnalysisSpec {
    pub name: &'static str,
    pub title: &'static str,
    pub aliases: &'static [&'static str],
    pub artifacts: &'static [ArtifactSpec],
    pub views: &'static [ViewSpec],
}

#[derive(Clone, Debug, Serialize)]
pub struct AnalysisIndex {
    pub recording: String,
    pub source: String,
    pub analyses: Vec<AnalysisMetadata>,
}

#[derive(Clone, Debug, Serialize)]
pub struct AnalysisMetadata {
    pub name: &'static str,
    pub title: &'static str,
    pub available: bool,
    pub artifacts: Vec<ArtifactMetadata>,
    pub views: Vec<ViewMetadata>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ArtifactMetadata {
    pub name: &'static str,
    pub title: &'static str,
    pub available: bool,
    pub kind: &'static str,
    pub encoding: &'static str,
    pub media_type: Option<&'static str>,
    pub is_directory: bool,
    pub downloadable: bool,
    pub sliceable: bool,
    pub proto_message_type: Option<&'static str>,
    pub relative_path: Option<String>,
    pub size_bytes: Option<u64>,
    pub download_url: Option<String>,
    pub records_url: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ViewMetadata {
    pub name: &'static str,
    pub title: &'static str,
    pub media_type: &'static str,
    pub query_parameters: &'static [&'static str],
    pub url: String,
}

#[derive(Clone, Debug, Default)]
pub struct RecordSliceFilter {
    pub start_timestamp_ns: Option<i64>,
    pub end_timestamp_ns: Option<i64>,
    pub start_frame_number: Option<u32>,
    pub end_frame_number: Option<u32>,
    pub limit: Option<usize>,
    pub include_summary: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct ArtifactRecordKey {
    pub timestamp_ns: Option<i64>,
    pub frame_number: Option<u32>,
    pub is_summary: bool,
}

const SEGMENTATION_ARTIFACTS: &[ArtifactSpec] = &[ArtifactSpec {
    name: "proto",
    title: "Segmentation Protobuf",
    suffix: "segmentation.pb",
    media_type: Some("application/x-protobuf"),
    kind: "protobuf",
    encoding: ProtoEncoding::LengthDelimited,
    aliases: &["pb"],
    proto_message_type: Some("bayesmech.vision.SegmentationResponse"),
    proto: Some(ArtifactProto::Segmentation),
    sliceable: true,
}];

const MOTIONCAP_ARTIFACTS: &[ArtifactSpec] = &[
    ArtifactSpec {
        name: "proto",
        title: "Motion Capture Protobuf",
        suffix: "motioncap.pb",
        media_type: Some("application/x-protobuf"),
        kind: "protobuf",
        encoding: ProtoEncoding::LengthDelimited,
        aliases: &["pb"],
        proto_message_type: Some("bayesmech.vision.MotionCaptureResponse"),
        proto: Some(ArtifactProto::Motioncap),
        sliceable: true,
    },
    ArtifactSpec {
        name: "video",
        title: "Motion Capture Video",
        suffix: "motioncap.mp4",
        media_type: Some("video/mp4"),
        kind: "video",
        encoding: ProtoEncoding::Binary,
        aliases: &[],
        proto_message_type: None,
        proto: None,
        sliceable: false,
    },
];

const IDOSLAM_ARTIFACTS: &[ArtifactSpec] = &[ArtifactSpec {
    name: "proto",
    title: "IdoSlam Response",
    suffix: "idoslam.pb",
    media_type: Some("application/x-protobuf"),
    kind: "protobuf",
    encoding: ProtoEncoding::LengthDelimited,
    aliases: &["pb", "slam"],
    proto_message_type: Some("bayesmech.vision.IdoSlamResponse"),
    proto: Some(ArtifactProto::IdoSlam),
    sliceable: false,
}];

const GENSPARK_ARTIFACTS: &[ArtifactSpec] = &[ArtifactSpec {
    name: "proto",
    title: "Genspark Response",
    suffix: "genspark.pb",
    media_type: Some("application/x-protobuf"),
    kind: "protobuf",
    encoding: ProtoEncoding::Single,
    aliases: &["pb"],
    proto_message_type: Some("bayesmech.vision.GensparkResponse"),
    proto: Some(ArtifactProto::Genspark),
    sliceable: false,
}];

const CHAT_ARTIFACTS: &[ArtifactSpec] = &[ArtifactSpec {
    name: "proto",
    title: "Chat History",
    suffix: "chat.pb",
    media_type: Some("application/x-protobuf"),
    kind: "protobuf",
    encoding: ProtoEncoding::Single,
    aliases: &["pb"],
    proto_message_type: Some("bayesmech.vision.ChatHistory"),
    proto: Some(ArtifactProto::Chat),
    sliceable: false,
}];

const RECONSTRUCTION_ARTIFACTS: &[ArtifactSpec] = &[
    ArtifactSpec {
        name: "proto",
        title: "Reconstruction Summary",
        suffix: "recon.pb",
        media_type: Some("application/x-protobuf"),
        kind: "protobuf",
        encoding: ProtoEncoding::LengthDelimited,
        aliases: &["pb"],
        proto_message_type: Some("bayesmech.vision.ReconstructionResponse"),
        proto: Some(ArtifactProto::Reconstruction),
        sliceable: false,
    },
    ArtifactSpec {
        name: "splat",
        title: "Gaussian Splat PLY",
        suffix: "splat.ply",
        media_type: Some("application/octet-stream"),
        kind: "point-cloud",
        encoding: ProtoEncoding::Binary,
        aliases: &["splat_ply"],
        proto_message_type: None,
        proto: None,
        sliceable: false,
    },
    ArtifactSpec {
        name: "workspace",
        title: "Reconstruction Workspace",
        suffix: "recon",
        media_type: None,
        kind: "workspace",
        encoding: ProtoEncoding::Directory,
        aliases: &[],
        proto_message_type: None,
        proto: None,
        sliceable: false,
    },
];

const SNOOK_ARTIFACTS: &[ArtifactSpec] = &[
    ArtifactSpec {
        name: "proto",
        title: "Snookestown Protobuf",
        suffix: "snook.pb",
        media_type: Some("application/x-protobuf"),
        kind: "protobuf",
        encoding: ProtoEncoding::LengthDelimited,
        aliases: &["pb"],
        proto_message_type: Some("bayesmech.vision.SnookerResponse"),
        proto: Some(ArtifactProto::Snookertown),
        sliceable: true,
    },
    ArtifactSpec {
        name: "video",
        title: "Top-down Video",
        suffix: "snook.mp4",
        media_type: Some("video/mp4"),
        kind: "video",
        encoding: ProtoEncoding::Binary,
        aliases: &[],
        proto_message_type: None,
        proto: None,
        sliceable: false,
    },
];

const PONGTOWN_ARTIFACTS: &[ArtifactSpec] = &[ArtifactSpec {
    name: "proto",
    title: "Pongtown Protobuf",
    suffix: "pongtown.pb",
    media_type: Some("application/x-protobuf"),
    kind: "protobuf",
    encoding: ProtoEncoding::LengthDelimited,
    aliases: &["pb"],
    proto_message_type: Some("bayesmech.vision.PongtownResponse"),
    proto: Some(ArtifactProto::Pongtown),
    sliceable: true,
}];

const MOTIONCAP_VIEWS: &[ViewSpec] = &[
    ViewSpec {
        name: "tracks",
        title: "Track Legend",
        media_type: "application/json",
        query_parameters: &[],
    },
    ViewSpec {
        name: "heatmap",
        title: "Rendered Heatmap",
        media_type: "application/octet-stream",
        query_parameters: &["frame_index", "timestamp_ns"],
    },
];

const ANALYSES: &[AnalysisSpec] = &[
    AnalysisSpec {
        name: "segmentation",
        title: "Segmentation",
        aliases: &[],
        artifacts: SEGMENTATION_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "motioncap",
        title: "Motion Capture",
        aliases: &["motion_cap"],
        artifacts: MOTIONCAP_ARTIFACTS,
        views: MOTIONCAP_VIEWS,
    },
    AnalysisSpec {
        name: "idoslam",
        title: "Localization and Mapping",
        aliases: &["slam", "localization_mapping"],
        artifacts: IDOSLAM_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "genspark",
        title: "AI Analysis",
        aliases: &["insightgen"],
        artifacts: GENSPARK_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "chat",
        title: "Follow-up Chat",
        aliases: &[],
        artifacts: CHAT_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "reconstruction",
        title: "3D Reconstruction",
        aliases: &["reconstruct"],
        artifacts: RECONSTRUCTION_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "snookestown",
        title: "Snookestown",
        aliases: &["snooker"],
        artifacts: SNOOK_ARTIFACTS,
        views: &[],
    },
    AnalysisSpec {
        name: "pongtown",
        title: "Pongtown",
        aliases: &["table_tennis", "ping_pong", "sport_understanding"],
        artifacts: PONGTOWN_ARTIFACTS,
        views: &[],
    },
];

pub fn all_analyses() -> &'static [AnalysisSpec] {
    ANALYSES
}

pub fn resolve_analysis(name: &str) -> Result<&'static AnalysisSpec> {
    let key = sanitize_key(name)?;
    ANALYSES
        .iter()
        .find(|analysis| {
            sanitize_key(analysis.name).ok().as_deref() == Some(key.as_str())
                || analysis
                    .aliases
                    .iter()
                    .any(|alias| sanitize_key(alias).ok().as_deref() == Some(key.as_str()))
        })
        .ok_or_else(|| StreamlogError::ArtifactNotFound {
            message: format!("unknown analysis {name:?}"),
        })
}

pub fn resolve_artifact(
    analysis: &'static AnalysisSpec,
    name: &str,
) -> Result<&'static ArtifactSpec> {
    let key = sanitize_key(name)?;
    analysis
        .artifacts
        .iter()
        .find(|artifact| {
            sanitize_key(artifact.name).ok().as_deref() == Some(key.as_str())
                || artifact
                    .aliases
                    .iter()
                    .any(|alias| sanitize_key(alias).ok().as_deref() == Some(key.as_str()))
        })
        .ok_or_else(|| StreamlogError::ArtifactNotFound {
            message: format!("unknown artifact {name:?} for analysis {:?}", analysis.name),
        })
}

pub fn artifact_path(
    store: &RecordingStore,
    recording_id: &RecordingId,
    artifact: &ArtifactSpec,
) -> PathBuf {
    store.artifact_path_for(recording_id, artifact.suffix)
}

pub fn build_analysis_index(
    store: &RecordingStore,
    recording_id: &RecordingId,
    source: &str,
    scope_prefix: &str,
) -> AnalysisIndex {
    AnalysisIndex {
        recording: recording_id.as_str().to_owned(),
        source: source.to_owned(),
        analyses: ANALYSES
            .iter()
            .map(|analysis| build_analysis_metadata(store, recording_id, analysis, scope_prefix))
            .collect(),
    }
}

pub fn build_analysis_metadata(
    store: &RecordingStore,
    recording_id: &RecordingId,
    analysis: &'static AnalysisSpec,
    scope_prefix: &str,
) -> AnalysisMetadata {
    let artifacts = analysis
        .artifacts
        .iter()
        .map(|artifact| {
            build_artifact_metadata(store, recording_id, analysis, artifact, scope_prefix)
        })
        .collect::<Vec<_>>();
    let views = analysis
        .views
        .iter()
        .map(|view| ViewMetadata {
            name: view.name,
            title: view.title,
            media_type: view.media_type,
            query_parameters: view.query_parameters,
            url: format!(
                "{scope_prefix}/analyses/{}/views/{}",
                analysis.name, view.name
            ),
        })
        .collect();
    AnalysisMetadata {
        name: analysis.name,
        title: analysis.title,
        available: artifacts.iter().any(|artifact| artifact.available),
        artifacts,
        views,
    }
}

pub fn build_artifact_metadata(
    store: &RecordingStore,
    recording_id: &RecordingId,
    analysis: &'static AnalysisSpec,
    artifact: &'static ArtifactSpec,
    scope_prefix: &str,
) -> ArtifactMetadata {
    let path = artifact_path(store, recording_id, artifact);
    let available = path.exists();
    let is_directory = artifact.encoding == ProtoEncoding::Directory;
    ArtifactMetadata {
        name: artifact.name,
        title: artifact.title,
        available,
        kind: artifact.kind,
        encoding: encoding_name(artifact.encoding),
        media_type: artifact.media_type,
        is_directory,
        downloadable: available && !is_directory,
        sliceable: artifact.sliceable,
        proto_message_type: artifact.proto_message_type,
        relative_path: available.then(|| {
            path.strip_prefix(store_root_for_relative(store, &path))
                .unwrap_or(path.as_path())
                .to_string_lossy()
                .to_string()
        }),
        size_bytes: available
            .then(|| {
                path.metadata()
                    .ok()
                    .filter(|m| m.is_file())
                    .map(|m| m.len())
            })
            .flatten(),
        download_url: (available && !is_directory).then(|| {
            format!(
                "{scope_prefix}/analyses/{}/artifacts/{}",
                analysis.name, artifact.name
            )
        }),
        records_url: (available && artifact.sliceable).then(|| {
            format!(
                "{scope_prefix}/analyses/{}/records?artifact={}",
                analysis.name, artifact.name
            )
        }),
    }
}

fn store_root_for_relative<'a>(store: &'a RecordingStore, path: &Path) -> &'a Path {
    let artifact_root = store.config_artifact_root();
    if path.starts_with(artifact_root) {
        artifact_root
    } else {
        store.config_recordings_root()
    }
}

pub fn encoding_name(encoding: ProtoEncoding) -> &'static str {
    match encoding {
        ProtoEncoding::LengthDelimited => "length-delimited-protobuf",
        ProtoEncoding::Single => "protobuf",
        ProtoEncoding::Binary => "binary",
        ProtoEncoding::Directory => "directory",
    }
}

pub fn decode_record_key(kind: ArtifactProto, raw: &[u8]) -> Result<ArtifactRecordKey> {
    match kind {
        ArtifactProto::Segmentation => {
            let record = SegmentationResponse::decode(raw)?;
            let fid = record.frame_identifier;
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary: false,
            })
        }
        ArtifactProto::Motioncap => {
            let record = MotionCaptureResponse::decode(raw)?;
            let fid = record.frame_identifier;
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary: !record.tracks.is_empty()
                    || !record.segmentation_trajectories.is_empty()
                    || record.total_frames != 0,
            })
        }
        ArtifactProto::Pongtown => {
            let record = PongtownResponse::decode(raw)?;
            let fid = record.frame_identifier;
            let is_summary = fid
                .as_ref()
                .is_none_or(|fid| fid.timestamp_ns == 0 && fid.frame_number == 0);
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary,
            })
        }
        ArtifactProto::Snookertown => {
            let record = SnookerResponse::decode(raw)?;
            let fid = record.frame_identifier;
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary: !record.tracks.is_empty() || record.total_frames != 0,
            })
        }
        ArtifactProto::IdoSlam => {
            let record = IdoSlamResponse::decode(raw)?;
            let fid = record.first_frame_id;
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary: false,
            })
        }
        ArtifactProto::Reconstruction => {
            let record = ReconstructionResponse::decode(raw)?;
            let fid = record.frame_identifier;
            Ok(ArtifactRecordKey {
                timestamp_ns: fid.as_ref().map(|fid| fid.timestamp_ns),
                frame_number: fid.as_ref().map(|fid| fid.frame_number),
                is_summary: false,
            })
        }
        ArtifactProto::Genspark => {
            let _ = GensparkResponse::decode(raw)?;
            Ok(ArtifactRecordKey {
                timestamp_ns: None,
                frame_number: None,
                is_summary: true,
            })
        }
        ArtifactProto::Chat => {
            let _ = ChatHistory::decode(raw)?;
            Ok(ArtifactRecordKey {
                timestamp_ns: None,
                frame_number: None,
                is_summary: true,
            })
        }
    }
}

pub fn encode_record_slice(
    artifact: &ArtifactSpec,
    path: &Path,
    filter: RecordSliceFilter,
    max_record_size: u32,
) -> Result<(Vec<u8>, usize)> {
    if !artifact.sliceable || artifact.encoding != ProtoEncoding::LengthDelimited {
        return Err(StreamlogError::invalid_request(format!(
            "artifact {:?} does not support record slicing",
            artifact.name
        )));
    }
    let Some(proto_kind) = artifact.proto else {
        return Err(StreamlogError::invalid_request(
            "sliceable artifact has no proto kind",
        ));
    };
    validate_filter(&filter)?;
    let mut selected = Vec::new();
    let mut summaries = Vec::new();
    for raw in protoio::read_raw_records(path, max_record_size)? {
        let key = decode_record_key(proto_kind, &raw.data)?;
        if key.is_summary {
            if filter.include_summary {
                summaries.push(raw.data);
            }
            continue;
        }
        if !record_matches_filter(key, &filter) {
            continue;
        }
        selected.push(raw.data);
        if filter.limit.is_some_and(|limit| selected.len() >= limit) {
            break;
        }
    }
    if filter.include_summary {
        selected.extend(summaries);
    }
    let count = selected.len();
    Ok((protoio::encode_delimited_raw(selected), count))
}

fn validate_filter(filter: &RecordSliceFilter) -> Result<()> {
    if filter.limit == Some(0) {
        return Err(StreamlogError::invalid_request("limit must be >= 1"));
    }
    if filter
        .start_timestamp_ns
        .zip(filter.end_timestamp_ns)
        .is_some_and(|(start, end)| start > end)
    {
        return Err(StreamlogError::invalid_request(
            "start_timestamp_ns must be <= end_timestamp_ns",
        ));
    }
    if filter
        .start_frame_number
        .zip(filter.end_frame_number)
        .is_some_and(|(start, end)| start > end)
    {
        return Err(StreamlogError::invalid_request(
            "start_frame_number must be <= end_frame_number",
        ));
    }
    Ok(())
}

fn record_matches_filter(key: ArtifactRecordKey, filter: &RecordSliceFilter) -> bool {
    if filter
        .start_timestamp_ns
        .is_some_and(|start| key.timestamp_ns.is_none_or(|ts| ts < start))
    {
        return false;
    }
    if filter
        .end_timestamp_ns
        .is_some_and(|end| key.timestamp_ns.is_none_or(|ts| ts > end))
    {
        return false;
    }
    if filter
        .start_frame_number
        .is_some_and(|start| key.frame_number.is_none_or(|frame| frame < start))
    {
        return false;
    }
    if filter
        .end_frame_number
        .is_some_and(|end| key.frame_number.is_none_or(|frame| frame > end))
    {
        return false;
    }
    true
}

pub fn path_for_recording_suffix(root: &Path, recording_id: &RecordingId, suffix: &str) -> PathBuf {
    recording_file(root, recording_id.as_str(), suffix)
}

pub fn summarize_motioncap_tracks(path: &Path, max_record_size: u32) -> Result<Value> {
    if !path.exists() {
        return Ok(json!({ "available": false, "tracks": [], "segmentation_trajectories": [] }));
    }
    let mut tracks = Vec::new();
    let mut segmentation_trajectories = Vec::new();
    for raw in protoio::read_raw_records(path, max_record_size)? {
        let record = MotionCaptureResponse::decode(raw.data.as_slice())?;
        if !record.tracks.is_empty() {
            tracks = record
                .tracks
                .iter()
                .map(|track| {
                    json!({
                        "track_id": track.track_id,
                        "label": track.label,
                        "color": color_for_track(track.track_id, false),
                        "detected_frames": track.detected_frames,
                        "total_positions": track.total_positions,
                        "presence_fraction": track.presence_fraction,
                        "positions": track.positions.iter().map(|pos| json!({
                            "frame_idx": pos.frame_idx,
                            "cx": pos.cx,
                            "cy": pos.cy,
                            "area": pos.area,
                            "interpolated": pos.interpolated,
                        })).collect::<Vec<_>>(),
                    })
                })
                .collect();
        }
        if !record.segmentation_trajectories.is_empty() {
            segmentation_trajectories = record
                .segmentation_trajectories
                .iter()
                .map(|track| {
                    json!({
                        "track_id": track.track_id,
                        "label": track.label,
                        "color": color_for_track(track.track_id, true),
                        "detected_frames": track.detected_frames,
                        "total_positions": track.total_positions,
                        "presence_fraction": track.presence_fraction,
                        "positions": track.positions.iter().map(|pos| json!({
                            "frame_idx": pos.frame_idx,
                            "cx": pos.cx,
                            "cy": pos.cy,
                            "area": pos.area,
                            "interpolated": pos.interpolated,
                        })).collect::<Vec<_>>(),
                    })
                })
                .collect();
        }
    }
    Ok(json!({
        "available": true,
        "tracks": tracks,
        "segmentation_trajectories": segmentation_trajectories,
    }))
}

fn color_for_track(track_id: u32, segmentation: bool) -> [u8; 3] {
    const COLORS: [[u8; 3]; 10] = [
        [255, 200, 0],
        [50, 255, 50],
        [80, 80, 255],
        [200, 50, 255],
        [0, 220, 255],
        [255, 100, 100],
        [200, 255, 0],
        [255, 0, 200],
        [0, 180, 255],
        [255, 128, 0],
    ];
    let offset = if segmentation { 5 } else { 0 };
    COLORS[(track_id as usize + offset) % COLORS.len()]
}
