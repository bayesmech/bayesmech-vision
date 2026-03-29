import sys
import struct
from pathlib import Path

from fastapi import FastAPI, Response, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))
sys.path.insert(0, str(_project_root))

from proto import insightgen_pb2
from proto import perceiver_pb2

app = FastAPI(title="BayesMech InsightGen Server")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

RECORDINGS_DIR = _project_root / "recordings"

def extract_thumbnail(pb_path: Path) -> bytes | None:
    target_ns = 20 * 1_000_000_000
    start_ns = None
    last_frame_data = None
    
    try:
        with open(pb_path, "rb") as f:
            while True:
                header = f.read(4)
                if len(header) < 4:
                    break
                (length,) = struct.unpack(">I", header)
                if length == 0 or length > 10 * 1024 * 1024:
                    f.seek(f.tell() - 3)
                    continue
                data = f.read(length)
                if len(data) < length:
                    break
                
                frame = perceiver_pb2.PerceiverDataFrame()
                try:
                    frame.ParseFromString(data)
                except Exception:
                    continue
                
                if not frame.HasField("frame_identifier") or not frame.HasField("rgb_frame"):
                    continue
                
                ts = frame.frame_identifier.timestamp_ns
                if start_ns is None:
                    start_ns = ts
                    
                last_frame_data = frame.rgb_frame.data
                
                if ts - start_ns >= target_ns:
                    return last_frame_data
    except Exception as e:
        print(f"Error reading {pb_path}: {e}")
        
    return last_frame_data

@app.post("/api/insightgen/recordings")
async def list_recordings(request: Request):
    body = await request.body()
    req = insightgen_pb2.ListRecordingsRequest()
    if body:
        try:
            req.ParseFromString(body)
        except Exception:
            pass
            
    print(f"Fetching recordings for user={req.username}")

    recordings_map = {}
    
    # Pre-populate map with vis.pb to get thumbnails
    for pb_path in RECORDINGS_DIR.glob("*.vis.pb"):
        base_name = pb_path.name.split(".")[0]
        thumb = extract_thumbnail(pb_path)
        
        dl = insightgen_pb2.DataList(
            file_name=base_name,
            is_segmentation_available=False,
            is_genspark_available=False,
            is_motioncap_available=False
        )
        if thumb:
            dl.image_frame = thumb
        recordings_map[base_name] = dl

    # Check for metadata files
    for file_path in RECORDINGS_DIR.glob("*"):
        if not file_path.is_file() or file_path.name.endswith(".vis.pb"):
            continue
        
        name = file_path.name
        parts = name.split(".")
        if len(parts) >= 2:
            base_name = parts[0]
            if base_name not in recordings_map:
                continue
                
            if ".seg.pb" in name:
                recordings_map[base_name].is_segmentation_available = True
            elif ".vis.genspark." in name and name.endswith(".txt"):
                recordings_map[base_name].is_genspark_available = True
            elif ".motion.pb" in name or ".motion.mp4" in name:
                recordings_map[base_name].is_motioncap_available = True
                
    response = insightgen_pb2.ListRecordingsResponse()
    # Sort backwards by date
    sorted_recordings = sorted(recordings_map.values(), key=lambda r: r.file_name, reverse=True)
    response.recordings.extend(sorted_recordings)
    
    return Response(content=response.SerializeToString(), media_type="application/x-protobuf")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
