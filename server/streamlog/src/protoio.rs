use std::{
    fs::File,
    io::{BufReader, Read, Seek, SeekFrom, Write},
    path::Path,
};

use bytes::{BufMut, BytesMut};
use prost::Message;
use serde::Serialize;
use serde_json::json;

use crate::{error::StreamlogError, Result};

#[derive(Clone, Debug, Serialize)]
pub struct RawRecord {
    pub offset: u64,
    pub length: u32,
    #[serde(skip)]
    pub data: Vec<u8>,
}

pub fn encode_delimited_raw(records: impl IntoIterator<Item = Vec<u8>>) -> Vec<u8> {
    let mut out = BytesMut::new();
    for record in records {
        out.put_u32(record.len() as u32);
        out.extend_from_slice(&record);
    }
    out.to_vec()
}

pub fn encode_delimited_messages<M: Message>(messages: impl IntoIterator<Item = M>) -> Vec<u8> {
    let mut out = BytesMut::new();
    for message in messages {
        let mut buf = Vec::new();
        message
            .encode(&mut buf)
            .expect("encoding into Vec cannot fail");
        out.put_u32(buf.len() as u32);
        out.extend_from_slice(&buf);
    }
    out.to_vec()
}

pub fn decode_delimited_buffer<M: Message + Default>(
    data: &[u8],
    max_record_size: u32,
) -> Result<Vec<M>> {
    let mut offset = 0usize;
    let mut messages = Vec::new();
    while offset + 4 <= data.len() {
        let length = u32::from_be_bytes(data[offset..offset + 4].try_into().unwrap());
        let header_offset = offset;
        offset += 4;
        if length == 0 || length > max_record_size {
            return Err(StreamlogError::CorruptRecording {
                message: format!("suspicious length prefix {length}"),
                details: json!({ "offset": header_offset, "length": length }),
            });
        }
        let end = offset + length as usize;
        if end > data.len() {
            return Err(StreamlogError::CorruptRecording {
                message: "truncated length-delimited record".to_owned(),
                details: json!({ "offset": header_offset, "length": length }),
            });
        }
        messages.push(M::decode(&data[offset..end])?);
        offset = end;
    }
    if offset != data.len() {
        return Err(StreamlogError::CorruptRecording {
            message: "trailing partial length prefix".to_owned(),
            details: json!({ "offset": offset, "file_size": data.len() }),
        });
    }
    Ok(messages)
}

pub fn read_delimited_file<M: Message + Default>(
    path: &Path,
    max_record_size: u32,
) -> Result<Vec<M>> {
    let mut records = Vec::new();
    for raw in read_raw_records(path, max_record_size)? {
        records.push(M::decode(raw.data.as_slice())?);
    }
    Ok(records)
}

pub fn read_raw_records(path: &Path, max_record_size: u32) -> Result<Vec<RawRecord>> {
    let mut file = BufReader::new(File::open(path)?);
    let mut records = Vec::new();
    loop {
        let offset = file.stream_position()?;
        let mut header = [0u8; 4];
        match file.read_exact(&mut header) {
            Ok(()) => {}
            Err(err) if err.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(err) => return Err(err.into()),
        }
        let length = u32::from_be_bytes(header);
        if length == 0 || length > max_record_size {
            return Err(StreamlogError::CorruptRecording {
                message: format!("suspicious length prefix {length}"),
                details: json!({ "offset": offset, "length": length }),
            });
        }
        let mut data = vec![0; length as usize];
        file.read_exact(&mut data).map_err(|err| {
            if err.kind() == std::io::ErrorKind::UnexpectedEof {
                StreamlogError::CorruptRecording {
                    message: "truncated length-delimited record".to_owned(),
                    details: json!({ "offset": offset, "length": length }),
                }
            } else {
                StreamlogError::Io(err)
            }
        })?;
        records.push(RawRecord {
            offset,
            length,
            data,
        });
    }
    Ok(records)
}

pub fn read_record_at(path: &Path, offset: u64, length: u32) -> Result<Vec<u8>> {
    let mut file = File::open(path)?;
    file.seek(SeekFrom::Start(offset + 4))?;
    let mut data = vec![0; length as usize];
    file.read_exact(&mut data)?;
    Ok(data)
}

pub fn append_delimited_raw(path: &Path, raw: &[u8]) -> Result<(u64, u32)> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .read(true)
        .open(path)?;
    let offset = file.seek(SeekFrom::End(0))?;
    file.write_all(&(raw.len() as u32).to_be_bytes())?;
    file.write_all(raw)?;
    file.flush()?;
    Ok((offset, raw.len() as u32))
}

#[cfg(test)]
mod tests {
    use bytes::Bytes;
    use tempfile::tempdir;

    use crate::proto::{PerceiverDataFrame, PerceiverFrameIdentifier};

    use super::*;

    #[test]
    fn round_trips_length_delimited_messages() {
        let frame = PerceiverDataFrame {
            frame_identifier: Some(PerceiverFrameIdentifier {
                timestamp_ns: 10,
                frame_number: 7,
                device_id: "d".to_owned(),
            }),
            ..Default::default()
        };
        let encoded = encode_delimited_messages([frame.clone()]);
        let decoded: Vec<PerceiverDataFrame> =
            decode_delimited_buffer(&encoded, 1024).expect("decode");
        assert_eq!(
            decoded[0].frame_identifier.as_ref().unwrap().frame_number,
            7
        );
        assert_eq!(Bytes::from(encoded).len(), frame.encoded_len() + 4);
    }

    #[test]
    fn rejects_suspicious_record_length() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("bad.vis.pb");
        std::fs::write(&path, u32::MAX.to_be_bytes()).unwrap();
        let err = read_raw_records(&path, 1024).unwrap_err();
        assert!(matches!(err, StreamlogError::CorruptRecording { .. }));
    }
}
