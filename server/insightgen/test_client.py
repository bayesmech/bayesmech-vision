import sys
import time
import subprocess
from pathlib import Path
import urllib.request
import urllib.error

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))
sys.path.insert(0, str(_project_root))

from proto import insightgen_pb2

proc = subprocess.Popen([sys.executable, str(_server_root / "insightgen" / "main.py")])
time.sleep(2)

try:
    req_proto = insightgen_pb2.ListRecordingsRequest(username="test_user", auth_token="mytoken")
    req = urllib.request.Request(
        "http://localhost:8081/api/insightgen/recordings", 
        method="POST", 
        data=req_proto.SerializeToString()
    )
    with urllib.request.urlopen(req) as response:
        content = response.read()
        proto_resp = insightgen_pb2.ListRecordingsResponse()
        proto_resp.ParseFromString(content)
        print("Success! Number of recordings:", len(proto_resp.recordings))
        for r in list(proto_resp.recordings)[:5]:
            print(f"File: {r.file_name}")
            print(f"  Frame Size: {len(r.image_frame)} bytes")
            print(f"  Segmentation: {r.is_segmentation_available}")
finally:
    proc.terminate()
    proc.wait()
