use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    path::{Path, PathBuf},
    sync::Arc,
};

use prost::Message;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::sync::RwLock;

use crate::{
    config::Config, error::StreamlogError, ids::RecordingId, proto::PerceiverDataFrame, protoio,
    Result,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TimestampMatch {
    Exact,
    Floor,
    Ceil,
    Nearest,
}

impl Default for TimestampMatch {
    fn default() -> Self {
        Self::Exact
    }
}

#[derive(Clone, Copy, Debug)]
pub enum FrameSelector {
    Index(usize),
    FrameNumber(u32),
    Timestamp {
        timestamp_ns: i64,
        mode: TimestampMatch,
        tolerance_ns: Option<i64>,
    },
    RelativeTimestamp {
        relative_timestamp_ns: i64,
        mode: TimestampMatch,
        tolerance_ns: Option<i64>,
    },
}

#[derive(Clone, Debug, Serialize)]
pub struct SignalFlags {
    pub rgb: bool,
    pub depth: bool,
    pub pose: bool,
    pub imu: bool,
    pub gps: bool,
    pub geometry: bool,
    pub intrinsics: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct FrameIndexEntry {
    pub frame_index: usize,
    pub frame_number: u32,
    pub timestamp_ns: i64,
    pub relative_timestamp_ns: i64,
    pub offset: u64,
    pub byte_length: u32,
    pub device_id: Option<String>,
    pub signals: SignalFlags,
}

#[derive(Clone, Debug, Serialize)]
pub struct IndexSummary {
    pub first_timestamp_ns: i64,
    pub last_timestamp_ns: i64,
    pub duration_ns: i64,
    pub estimated_fps: f64,
    pub frame_count: usize,
    pub device_ids: Vec<String>,
    pub first_intrinsics: Option<CameraIntrinsicsSummary>,
    pub source_size_bytes: u64,
    pub source_modified_unix_ns: i128,
}

#[derive(Clone, Debug, Serialize)]
pub struct CameraIntrinsicsSummary {
    pub fx: f32,
    pub fy: f32,
    pub cx: f32,
    pub cy: f32,
    pub image_width: f32,
    pub image_height: f32,
    pub depth_width: f32,
    pub depth_height: f32,
}

#[derive(Clone, Debug)]
pub struct RecordingIndex {
    pub recording_id: RecordingId,
    pub source_path: PathBuf,
    pub entries: Vec<FrameIndexEntry>,
    pub summary: IndexSummary,
    by_frame_number: BTreeMap<u32, Vec<usize>>,
    timestamps: Vec<(i64, usize)>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ResolvedFrame {
    pub recording_id: String,
    pub frame_index: usize,
    pub frame_number: u32,
    pub timestamp_ns: i64,
    pub relative_timestamp_ns: i64,
    pub selector_match_delta_ns: i64,
    pub payload_media_type: String,
    pub byte_length: u32,
}

#[derive(Clone, Debug, Serialize)]
pub struct RecordingManifest {
    pub recording_id: String,
    pub source_size_bytes: u64,
    pub status: String,
    pub frame_count: usize,
    pub first_timestamp_ns: i64,
    pub last_timestamp_ns: i64,
    pub duration_ns: i64,
    pub estimated_fps: f64,
    pub device_ids: Vec<String>,
    pub first_intrinsics: Option<CameraIntrinsicsSummary>,
}

#[derive(Clone)]
pub struct RecordingStore {
    config: Config,
    indexes: Arc<RwLock<HashMap<String, Arc<RecordingIndex>>>>,
}

impl RecordingStore {
    pub fn new(config: Config) -> Self {
        Self {
            config,
            indexes: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub fn source_path_for(&self, recording_id: &RecordingId) -> PathBuf {
        recording_file(
            &self.config.recordings_root,
            recording_id.as_str(),
            "vis.pb",
        )
    }

    pub fn artifact_path_for(&self, recording_id: &RecordingId, suffix: &str) -> PathBuf {
        recording_file(self.config.artifact_root(), recording_id.as_str(), suffix)
    }

    pub fn config_recordings_root(&self) -> &Path {
        &self.config.recordings_root
    }

    pub fn config_artifact_root(&self) -> &Path {
        self.config.artifact_root()
    }

    pub fn max_record_size(&self) -> u32 {
        self.config.limits.max_record_size
    }

    pub async fn list_recording_ids(&self) -> Result<Vec<RecordingId>> {
        let mut dirs = tokio::fs::read_dir(&self.config.recordings_root).await?;
        let mut ids = Vec::new();
        while let Some(entry) = dirs.next_entry().await? {
            if !entry.file_type().await?.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if RecordingId::parse(&name).is_ok() {
                let id = RecordingId::parse(name)?;
                if self.source_path_for(&id).exists() {
                    ids.push(id);
                }
            }
        }
        ids.sort_by(|a, b| b.as_str().cmp(a.as_str()));
        Ok(ids)
    }

    pub async fn ensure_index(&self, recording_id: RecordingId) -> Result<Arc<RecordingIndex>> {
        if let Some(index) = self
            .indexes
            .read()
            .await
            .get(recording_id.as_str())
            .cloned()
        {
            if index_is_current(&index) {
                return Ok(index);
            }
        }

        let source_path = self.source_path_for(&recording_id);
        if !source_path.exists() {
            return Err(StreamlogError::RecordingNotFound {
                recording_id: recording_id.into_string(),
            });
        }
        let max_record_size = self.config.limits.max_record_size;
        let id_for_build = recording_id.clone();
        let source_for_build = source_path.clone();
        let index = tokio::task::spawn_blocking(move || {
            build_recording_index(id_for_build, source_for_build, max_record_size)
        })
        .await
        .map_err(|err| anyhow::anyhow!("index task failed: {err}"))??;
        let index = Arc::new(index);
        self.indexes
            .write()
            .await
            .insert(recording_id.into_string(), index.clone());
        Ok(index)
    }

    pub async fn invalidate(&self, recording_id: &RecordingId) {
        self.indexes.write().await.remove(recording_id.as_str());
    }

    pub async fn manifest(&self, recording_id: RecordingId) -> Result<RecordingManifest> {
        let index = self.ensure_index(recording_id.clone()).await?;
        Ok(RecordingManifest {
            recording_id: recording_id.into_string(),
            source_size_bytes: index.summary.source_size_bytes,
            status: "ready".to_owned(),
            frame_count: index.summary.frame_count,
            first_timestamp_ns: index.summary.first_timestamp_ns,
            last_timestamp_ns: index.summary.last_timestamp_ns,
            duration_ns: index.summary.duration_ns,
            estimated_fps: index.summary.estimated_fps,
            device_ids: index.summary.device_ids.clone(),
            first_intrinsics: index.summary.first_intrinsics.clone(),
        })
    }

    pub async fn resolve_frame(
        &self,
        recording_id: RecordingId,
        selector: FrameSelector,
    ) -> Result<(Arc<RecordingIndex>, FrameIndexEntry, ResolvedFrame)> {
        let index = self.ensure_index(recording_id.clone()).await?;
        let (entry, delta) = index.resolve(selector)?;
        let metadata = ResolvedFrame {
            recording_id: recording_id.into_string(),
            frame_index: entry.frame_index,
            frame_number: entry.frame_number,
            timestamp_ns: entry.timestamp_ns,
            relative_timestamp_ns: entry.relative_timestamp_ns,
            selector_match_delta_ns: delta,
            payload_media_type: "application/x-protobuf".to_owned(),
            byte_length: entry.byte_length,
        };
        Ok((index, entry, metadata))
    }

    pub async fn read_frame_bytes(
        &self,
        index: Arc<RecordingIndex>,
        entry: &FrameIndexEntry,
    ) -> Result<Vec<u8>> {
        let source_path = index.source_path.clone();
        let offset = entry.offset;
        let length = entry.byte_length;
        tokio::task::spawn_blocking(move || protoio::read_record_at(&source_path, offset, length))
            .await
            .map_err(|err| anyhow::anyhow!("read frame task failed: {err}"))?
    }

    pub async fn read_range_bytes(
        &self,
        recording_id: RecordingId,
        range: RangeSelector,
    ) -> Result<(Vec<u8>, Vec<ResolvedFrame>)> {
        let index = self.ensure_index(recording_id.clone()).await?;
        let entries = index.range(range)?;
        if entries.len() > self.config.limits.max_outstream_range_frames {
            return Err(StreamlogError::invalid_request(format!(
                "range exceeds max frame count {}",
                self.config.limits.max_outstream_range_frames
            )));
        }

        let max_bytes = self.config.limits.max_outstream_response_bytes;
        let source_path = index.source_path.clone();
        let metas = entries
            .iter()
            .map(|entry| ResolvedFrame {
                recording_id: recording_id.as_str().to_owned(),
                frame_index: entry.frame_index,
                frame_number: entry.frame_number,
                timestamp_ns: entry.timestamp_ns,
                relative_timestamp_ns: entry.relative_timestamp_ns,
                selector_match_delta_ns: 0,
                payload_media_type: "application/x-protobuf".to_owned(),
                byte_length: entry.byte_length,
            })
            .collect::<Vec<_>>();
        let payload = tokio::task::spawn_blocking(move || {
            let mut total = 0usize;
            let mut raw = Vec::with_capacity(entries.len());
            for entry in entries {
                total += entry.byte_length as usize + 4;
                if total > max_bytes {
                    return Err(StreamlogError::invalid_request(
                        "range response exceeds byte limit",
                    ));
                }
                raw.push(protoio::read_record_at(
                    &source_path,
                    entry.offset,
                    entry.byte_length,
                )?);
            }
            Ok(protoio::encode_delimited_raw(raw))
        })
        .await
        .map_err(|err| anyhow::anyhow!("read range task failed: {err}"))??;
        Ok((payload, metas))
    }

    pub async fn append_live_frame(
        &self,
        recording_id: RecordingId,
        raw_frame: Vec<u8>,
    ) -> Result<(FrameIndexEntry, PathBuf)> {
        if raw_frame.len() > self.config.limits.max_record_size as usize {
            return Err(StreamlogError::invalid_request(
                "frame exceeds max record size",
            ));
        }
        let frame = PerceiverDataFrame::decode(raw_frame.as_slice())?;
        let source_path = self.source_path_for(&recording_id);
        let path_for_append = source_path.clone();
        let raw_for_append = raw_frame.clone();
        let (offset, length) = tokio::task::spawn_blocking(move || {
            protoio::append_delimited_raw(&path_for_append, &raw_for_append)
        })
        .await
        .map_err(|err| anyhow::anyhow!("append live frame task failed: {err}"))??;

        let mut indexes = self.indexes.write().await;
        if let Some(index) = indexes.get_mut(recording_id.as_str()) {
            let mut clone = (**index).clone();
            clone.append_frame(raw_to_entry(
                clone.entries.len(),
                offset,
                length,
                &frame,
                clone.summary.first_timestamp_ns,
            )?);
            clone.refresh_summary_from_entries()?;
            *index = Arc::new(clone);
        }
        let first_ts = indexes
            .get(recording_id.as_str())
            .and_then(|index| index.entries.first())
            .map(|entry| entry.timestamp_ns)
            .unwrap_or_else(|| {
                frame
                    .frame_identifier
                    .as_ref()
                    .map(|fid| fid.timestamp_ns)
                    .unwrap_or_default()
            });
        let entry = raw_to_entry(
            indexes
                .get(recording_id.as_str())
                .map(|index| index.entries.len().saturating_sub(1))
                .unwrap_or(0),
            offset,
            length,
            &frame,
            first_ts,
        )?;
        Ok((entry, source_path))
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize)]
pub struct RangeSelector {
    pub start_frame_index: Option<usize>,
    pub end_frame_index: Option<usize>,
    pub start_frame_number: Option<u32>,
    pub end_frame_number: Option<u32>,
    pub start_timestamp_ns: Option<i64>,
    pub end_timestamp_ns: Option<i64>,
    pub start_relative_timestamp_ns: Option<i64>,
    pub end_relative_timestamp_ns: Option<i64>,
    pub limit: Option<usize>,
    pub stride: Option<usize>,
}

impl RecordingIndex {
    pub fn resolve(&self, selector: FrameSelector) -> Result<(FrameIndexEntry, i64)> {
        match selector {
            FrameSelector::Index(index) => self
                .entries
                .get(index)
                .cloned()
                .map(|entry| (entry, 0))
                .ok_or_else(|| {
                    StreamlogError::frame_not_found(
                        "frame index is out of range",
                        json!({ "frame_index": index, "frame_count": self.entries.len() }),
                    )
                }),
            FrameSelector::FrameNumber(frame_number) => self
                .by_frame_number
                .get(&frame_number)
                .and_then(|indices| indices.first())
                .and_then(|idx| self.entries.get(*idx))
                .cloned()
                .map(|entry| (entry, 0))
                .ok_or_else(|| {
                    StreamlogError::frame_not_found(
                        "frame number not found",
                        json!({ "frame_number": frame_number }),
                    )
                }),
            FrameSelector::Timestamp {
                timestamp_ns,
                mode,
                tolerance_ns,
            } => self.resolve_timestamp(timestamp_ns, mode, tolerance_ns, false),
            FrameSelector::RelativeTimestamp {
                relative_timestamp_ns,
                mode,
                tolerance_ns,
            } => {
                let target = self.summary.first_timestamp_ns + relative_timestamp_ns;
                self.resolve_timestamp(target, mode, tolerance_ns, true)
            }
        }
    }

    pub fn range(&self, selector: RangeSelector) -> Result<Vec<FrameIndexEntry>> {
        let stride = selector.stride.unwrap_or(1);
        if stride == 0 {
            return Err(StreamlogError::invalid_selector("stride must be >= 1"));
        }
        let mut entries: Vec<FrameIndexEntry> =
            if selector.start_frame_index.is_some() || selector.end_frame_index.is_some() {
                let start = selector.start_frame_index.unwrap_or(0);
                let end = selector.end_frame_index.unwrap_or(self.entries.len());
                if start > end {
                    return Err(StreamlogError::invalid_selector(
                        "start_frame_index must be <= end_frame_index",
                    ));
                }
                self.entries
                    .iter()
                    .skip(start)
                    .take(end.saturating_sub(start))
                    .cloned()
                    .collect()
            } else if selector.start_frame_number.is_some() || selector.end_frame_number.is_some() {
                let start = selector.start_frame_number.unwrap_or(0);
                let end = selector.end_frame_number.unwrap_or(u32::MAX);
                if start > end {
                    return Err(StreamlogError::invalid_selector(
                        "start_frame_number must be <= end_frame_number",
                    ));
                }
                self.entries
                    .iter()
                    .filter(|entry| entry.frame_number >= start && entry.frame_number <= end)
                    .cloned()
                    .collect()
            } else if selector.start_timestamp_ns.is_some() || selector.end_timestamp_ns.is_some() {
                let start = selector.start_timestamp_ns.unwrap_or(i64::MIN);
                let end = selector.end_timestamp_ns.unwrap_or(i64::MAX);
                if start > end {
                    return Err(StreamlogError::invalid_selector(
                        "start_timestamp_ns must be <= end_timestamp_ns",
                    ));
                }
                self.entries
                    .iter()
                    .filter(|entry| entry.timestamp_ns >= start && entry.timestamp_ns <= end)
                    .cloned()
                    .collect()
            } else if selector.start_relative_timestamp_ns.is_some()
                || selector.end_relative_timestamp_ns.is_some()
            {
                let start = selector.start_relative_timestamp_ns.unwrap_or(i64::MIN);
                let end = selector.end_relative_timestamp_ns.unwrap_or(i64::MAX);
                if start > end {
                    return Err(StreamlogError::invalid_selector(
                        "start_relative_timestamp_ns must be <= end_relative_timestamp_ns",
                    ));
                }
                self.entries
                    .iter()
                    .filter(|entry| {
                        entry.relative_timestamp_ns >= start && entry.relative_timestamp_ns <= end
                    })
                    .cloned()
                    .collect()
            } else {
                self.entries.clone()
            };

        if stride > 1 {
            entries = entries
                .into_iter()
                .enumerate()
                .filter_map(|(i, entry)| (i % stride == 0).then_some(entry))
                .collect();
        }
        if let Some(limit) = selector.limit {
            entries.truncate(limit);
        }
        Ok(entries)
    }

    fn resolve_timestamp(
        &self,
        target_ns: i64,
        mode: TimestampMatch,
        tolerance_ns: Option<i64>,
        relative_delta: bool,
    ) -> Result<(FrameIndexEntry, i64)> {
        if self.timestamps.is_empty() {
            return Err(StreamlogError::frame_not_found(
                "recording has no frames",
                json!({}),
            ));
        }
        let candidate_index = match mode {
            TimestampMatch::Exact => self
                .timestamps
                .binary_search_by(|(ts, idx)| (*ts, *idx).cmp(&(target_ns, 0)))
                .ok()
                .or_else(|| self.timestamps.iter().position(|(ts, _)| *ts == target_ns)),
            TimestampMatch::Floor => {
                let pos = self.timestamps.partition_point(|(ts, _)| *ts <= target_ns);
                pos.checked_sub(1)
            }
            TimestampMatch::Ceil => {
                let pos = self.timestamps.partition_point(|(ts, _)| *ts < target_ns);
                (pos < self.timestamps.len()).then_some(pos)
            }
            TimestampMatch::Nearest => {
                let pos = self.timestamps.partition_point(|(ts, _)| *ts < target_ns);
                let before = pos.checked_sub(1);
                let after = (pos < self.timestamps.len()).then_some(pos);
                [before, after]
                    .into_iter()
                    .flatten()
                    .min_by_key(|idx| (self.timestamps[*idx].0 - target_ns).abs())
            }
        };

        let Some(candidate_index) = candidate_index else {
            return Err(StreamlogError::frame_not_found(
                "no frame satisfies timestamp selector",
                json!({ "timestamp_ns": target_ns }),
            ));
        };
        let (matched_ts, frame_index) = self.timestamps[candidate_index];
        let delta = matched_ts - target_ns;
        if tolerance_ns.is_some_and(|tol| delta.abs() > tol) {
            return Err(StreamlogError::frame_not_found(
                "no frame within tolerance",
                json!({
                    if relative_delta { "relative_timestamp_ns" } else { "timestamp_ns" }: target_ns,
                    "tolerance_ns": tolerance_ns,
                    "selector_match_delta_ns": delta,
                }),
            ));
        }
        Ok((self.entries[frame_index].clone(), delta))
    }

    fn append_frame(&mut self, entry: FrameIndexEntry) {
        self.by_frame_number
            .entry(entry.frame_number)
            .or_default()
            .push(entry.frame_index);
        self.timestamps
            .push((entry.timestamp_ns, entry.frame_index));
        self.timestamps.sort_unstable();
        self.entries.push(entry);
    }

    fn refresh_summary_from_entries(&mut self) -> Result<()> {
        let meta = std::fs::metadata(&self.source_path)?;
        self.summary = build_summary(&self.entries, &meta, self.summary.first_intrinsics.clone())?;
        Ok(())
    }
}

pub fn build_recording_index(
    recording_id: RecordingId,
    source_path: PathBuf,
    max_record_size: u32,
) -> Result<RecordingIndex> {
    let raw_records = protoio::read_raw_records(&source_path, max_record_size)?;
    if raw_records.is_empty() {
        return Err(StreamlogError::CorruptRecording {
            message: "recording contains no frames".to_owned(),
            details: json!({ "recording_id": recording_id.as_str() }),
        });
    }

    let first_frame = PerceiverDataFrame::decode(raw_records[0].data.as_slice())?;
    let first_ts = first_frame
        .frame_identifier
        .as_ref()
        .ok_or_else(|| StreamlogError::CorruptRecording {
            message: "first frame is missing frame_identifier".to_owned(),
            details: json!({ "offset": raw_records[0].offset }),
        })?
        .timestamp_ns;

    let mut entries = Vec::with_capacity(raw_records.len());
    let mut first_intrinsics = first_frame
        .camera_intrinsics
        .as_ref()
        .map(camera_intrinsics_summary);
    for (frame_index, raw) in raw_records.iter().enumerate() {
        let frame = PerceiverDataFrame::decode(raw.data.as_slice()).map_err(|err| {
            StreamlogError::CorruptRecording {
                message: format!("failed to decode frame: {err}"),
                details: json!({ "offset": raw.offset, "length": raw.length }),
            }
        })?;
        if first_intrinsics.is_none() {
            first_intrinsics = frame
                .camera_intrinsics
                .as_ref()
                .map(camera_intrinsics_summary);
        }
        entries.push(raw_to_entry(
            frame_index,
            raw.offset,
            raw.length,
            &frame,
            first_ts,
        )?);
    }

    let mut by_frame_number: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    let mut timestamps = Vec::with_capacity(entries.len());
    for entry in &entries {
        by_frame_number
            .entry(entry.frame_number)
            .or_default()
            .push(entry.frame_index);
        timestamps.push((entry.timestamp_ns, entry.frame_index));
    }
    timestamps.sort_unstable();
    let meta = std::fs::metadata(&source_path)?;
    let summary = build_summary(&entries, &meta, first_intrinsics)?;
    Ok(RecordingIndex {
        recording_id,
        source_path,
        entries,
        summary,
        by_frame_number,
        timestamps,
    })
}

fn raw_to_entry(
    frame_index: usize,
    offset: u64,
    byte_length: u32,
    frame: &PerceiverDataFrame,
    first_timestamp_ns: i64,
) -> Result<FrameIndexEntry> {
    let fid = frame
        .frame_identifier
        .as_ref()
        .ok_or_else(|| StreamlogError::CorruptRecording {
            message: "frame is missing frame_identifier".to_owned(),
            details: json!({ "frame_index": frame_index, "offset": offset }),
        })?;
    Ok(FrameIndexEntry {
        frame_index,
        frame_number: fid.frame_number,
        timestamp_ns: fid.timestamp_ns,
        relative_timestamp_ns: fid.timestamp_ns - first_timestamp_ns,
        offset,
        byte_length,
        device_id: (!fid.device_id.is_empty()).then(|| fid.device_id.clone()),
        signals: SignalFlags {
            rgb: frame
                .rgb_frame
                .as_ref()
                .is_some_and(|rgb| !rgb.data.is_empty()),
            depth: frame
                .depth_frame
                .as_ref()
                .is_some_and(|depth| !depth.data.is_empty()),
            pose: frame.camera_pose.is_some(),
            imu: frame.imu_data.is_some(),
            gps: frame.gps_location.is_some(),
            geometry: frame.inferred_geometry.as_ref().is_some_and(|geometry| {
                !geometry.planes.is_empty() || !geometry.point_cloud.is_empty()
            }),
            intrinsics: frame.camera_intrinsics.is_some(),
        },
    })
}

fn build_summary(
    entries: &[FrameIndexEntry],
    meta: &std::fs::Metadata,
    first_intrinsics: Option<CameraIntrinsicsSummary>,
) -> Result<IndexSummary> {
    let first_timestamp_ns = entries.first().map(|entry| entry.timestamp_ns).unwrap_or(0);
    let last_timestamp_ns = entries.last().map(|entry| entry.timestamp_ns).unwrap_or(0);
    let duration_ns = last_timestamp_ns.saturating_sub(first_timestamp_ns);
    let estimated_fps = if entries.len() > 1 && duration_ns > 0 {
        (entries.len().saturating_sub(1)) as f64 / (duration_ns as f64 / 1_000_000_000.0)
    } else {
        30.0
    };
    let device_ids = entries
        .iter()
        .filter_map(|entry| entry.device_id.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    Ok(IndexSummary {
        first_timestamp_ns,
        last_timestamp_ns,
        duration_ns,
        estimated_fps,
        frame_count: entries.len(),
        device_ids,
        first_intrinsics,
        source_size_bytes: meta.len(),
        source_modified_unix_ns: meta
            .modified()
            .ok()
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|duration| duration.as_nanos() as i128)
            .unwrap_or_default(),
    })
}

fn camera_intrinsics_summary(
    intrinsics: &crate::proto::CameraIntrinsics,
) -> CameraIntrinsicsSummary {
    CameraIntrinsicsSummary {
        fx: intrinsics.fx,
        fy: intrinsics.fy,
        cx: intrinsics.cx,
        cy: intrinsics.cy,
        image_width: intrinsics.image_width,
        image_height: intrinsics.image_height,
        depth_width: intrinsics.depth_width,
        depth_height: intrinsics.depth_height,
    }
}

fn index_is_current(index: &RecordingIndex) -> bool {
    let Ok(meta) = std::fs::metadata(&index.source_path) else {
        return false;
    };
    let modified = meta
        .modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i128)
        .unwrap_or_default();
    meta.len() == index.summary.source_size_bytes
        && modified == index.summary.source_modified_unix_ns
}

pub fn recording_file(root: &Path, recording_id: &str, suffix: &str) -> PathBuf {
    let folder = root.join(recording_id);
    for candidate in recording_file_candidates(root, recording_id, suffix) {
        if candidate.exists() {
            return candidate;
        }
    }
    folder.join(format!("{}.{}", recording_stem(recording_id), suffix))
}

pub fn recording_file_candidates(root: &Path, recording_id: &str, suffix: &str) -> Vec<PathBuf> {
    let folder = root.join(recording_id);
    let mut stems = vec![recording_id.to_owned()];
    let stem = recording_stem(recording_id);
    if stem != recording_id {
        stems.push(stem);
    }
    let mut suffixes = vec![suffix.to_owned()];
    match suffix {
        "segmentation.pb" => suffixes.push("seg.pb".to_owned()),
        "motioncap.pb" => suffixes.push("motion.pb".to_owned()),
        "motioncap.mp4" => suffixes.push("motion.mp4".to_owned()),
        "recon.pb" => suffixes.push("reconstruct.pb".to_owned()),
        _ => {}
    }
    let mut paths = Vec::new();
    for suffix in suffixes {
        for stem in &stems {
            paths.push(folder.join(format!("{stem}.{suffix}")));
        }
    }
    paths
}

pub fn recording_stem(recording_id: &str) -> String {
    let bytes = recording_id.as_bytes();
    if bytes.len() >= 15
        && bytes[0..8].iter().all(u8::is_ascii_digit)
        && bytes[8] == b'_'
        && bytes[9..15].iter().all(u8::is_ascii_digit)
        && bytes.len() > 15
        && bytes[15] == b'_'
    {
        recording_id[..15].to_owned()
    } else {
        recording_id
            .trim_end_matches(".vis.pb")
            .trim_end_matches(".segmentation.pb")
            .trim_end_matches(".motioncap.pb")
            .trim_end_matches(".pongtown.pb")
            .to_owned()
    }
}

#[cfg(test)]
mod tests {
    use bytes::Bytes;
    use tempfile::tempdir;

    use crate::{
        proto::{PerceiverDataFrame, PerceiverFrameIdentifier},
        protoio::encode_delimited_messages,
    };

    use super::*;

    fn frame(ts: i64, number: u32) -> PerceiverDataFrame {
        PerceiverDataFrame {
            frame_identifier: Some(PerceiverFrameIdentifier {
                timestamp_ns: ts,
                frame_number: number,
                device_id: "phone".to_owned(),
            }),
            ..Default::default()
        }
    }

    #[test]
    fn resolves_selectors_and_tolerance() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("r.vis.pb");
        std::fs::write(
            &path,
            encode_delimited_messages([
                frame(100, 10),
                frame(200, 11),
                frame(200, 12),
                frame(350, 13),
            ]),
        )
        .unwrap();

        let index = build_recording_index(RecordingId::parse("r").unwrap(), path, 1024).unwrap();
        assert_eq!(
            index
                .resolve(FrameSelector::Index(2))
                .unwrap()
                .0
                .frame_number,
            12
        );
        assert_eq!(
            index
                .resolve(FrameSelector::FrameNumber(13))
                .unwrap()
                .0
                .timestamp_ns,
            350
        );
        assert_eq!(
            index
                .resolve(FrameSelector::Timestamp {
                    timestamp_ns: 250,
                    mode: TimestampMatch::Floor,
                    tolerance_ns: None
                })
                .unwrap()
                .0
                .timestamp_ns,
            200
        );
        assert_eq!(
            index
                .resolve(FrameSelector::Timestamp {
                    timestamp_ns: 250,
                    mode: TimestampMatch::Ceil,
                    tolerance_ns: None
                })
                .unwrap()
                .0
                .timestamp_ns,
            350
        );
        assert!(index
            .resolve(FrameSelector::Timestamp {
                timestamp_ns: 260,
                mode: TimestampMatch::Nearest,
                tolerance_ns: Some(20)
            })
            .is_err());
        assert_eq!(
            Bytes::from(
                protoio::read_record_at(
                    &index.source_path,
                    index.entries[0].offset,
                    index.entries[0].byte_length
                )
                .unwrap()
            )
            .len(),
            frame(100, 10).encoded_len()
        );
    }

    #[test]
    fn recording_stem_extracts_timestamp_prefix() {
        assert_eq!(
            recording_stem("20260425_140029_tabletennis_home"),
            "20260425_140029"
        );
        assert_eq!(recording_stem("cam_a"), "cam_a");
    }
}
