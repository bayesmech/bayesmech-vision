use std::path::Path;

use prost::Message;

use crate::{
    artifacts::{decode_record_key, ArtifactProto},
    error::StreamlogError,
    proto::SegmentationResponse,
    protoio,
    store::{FrameSelector, RecordingIndex, TimestampMatch},
    Result,
};

pub fn resolve_segmentation(
    path: &Path,
    max_record_size: u32,
    selector: FrameSelector,
) -> Result<Option<Vec<u8>>> {
    if !path.exists() {
        return Ok(None);
    }
    match selector {
        FrameSelector::FrameNumber(frame_number) => {
            let mut best: Option<Vec<u8>> = None;
            let mut best_frame = None;
            for raw in protoio::read_raw_records(path, max_record_size)? {
                let key = decode_record_key(ArtifactProto::Segmentation, &raw.data)?;
                let Some(candidate_frame) = key.frame_number else {
                    continue;
                };
                if candidate_frame <= frame_number
                    && best_frame.is_none_or(|current| candidate_frame > current)
                {
                    best_frame = Some(candidate_frame);
                    best = Some(raw.data);
                }
            }
            Ok(best)
        }
        FrameSelector::Timestamp {
            timestamp_ns,
            mode,
            tolerance_ns,
        } => resolve_by_timestamp(path, max_record_size, timestamp_ns, mode, tolerance_ns),
        FrameSelector::Index(_) | FrameSelector::RelativeTimestamp { .. } => {
            Err(StreamlogError::invalid_selector(
                "annotation lookup requires frame_number or absolute timestamp selector",
            ))
        }
    }
}

pub fn resolve_segmentation_for_frame(
    path: &Path,
    max_record_size: u32,
    frame: &crate::store::FrameIndexEntry,
) -> Result<Option<Vec<u8>>> {
    resolve_segmentation(
        path,
        max_record_size,
        FrameSelector::FrameNumber(frame.frame_number),
    )
}

pub fn all_segmentations(path: &Path, max_record_size: u32) -> Result<Vec<Vec<u8>>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    Ok(protoio::read_raw_records(path, max_record_size)?
        .into_iter()
        .map(|raw| raw.data)
        .collect())
}

pub fn segmentation_for_recording_frame_number(
    path: &Path,
    max_record_size: u32,
    frame_number: u32,
) -> Result<Option<SegmentationResponse>> {
    let Some(raw) = resolve_segmentation(
        path,
        max_record_size,
        FrameSelector::FrameNumber(frame_number),
    )?
    else {
        return Ok(None);
    };
    Ok(Some(SegmentationResponse::decode(raw.as_slice())?))
}

fn resolve_by_timestamp(
    path: &Path,
    max_record_size: u32,
    target_ns: i64,
    mode: TimestampMatch,
    tolerance_ns: Option<i64>,
) -> Result<Option<Vec<u8>>> {
    let records = protoio::read_raw_records(path, max_record_size)?
        .into_iter()
        .filter_map(|raw| {
            let key = decode_record_key(ArtifactProto::Segmentation, &raw.data).ok()?;
            Some((key.timestamp_ns?, raw.data))
        })
        .collect::<Vec<_>>();
    if records.is_empty() {
        return Ok(None);
    }
    let candidate = match mode {
        TimestampMatch::Exact => records.iter().find(|(ts, _)| *ts == target_ns),
        TimestampMatch::Floor => records.iter().rev().find(|(ts, _)| *ts <= target_ns),
        TimestampMatch::Ceil => records.iter().find(|(ts, _)| *ts >= target_ns),
        TimestampMatch::Nearest => records.iter().min_by_key(|(ts, _)| (*ts - target_ns).abs()),
    };
    let Some((matched_ts, data)) = candidate else {
        return Ok(None);
    };
    if tolerance_ns.is_some_and(|tol| (*matched_ts - target_ns).abs() > tol) {
        return Ok(None);
    }
    Ok(Some(data.clone()))
}

pub fn frame_numbers_for_range(index: &RecordingIndex, start: usize, end: usize) -> Vec<u32> {
    index
        .entries
        .iter()
        .skip(start)
        .take(end.saturating_sub(start))
        .map(|entry| entry.frame_number)
        .collect()
}
