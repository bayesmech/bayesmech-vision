#!/usr/bin/env python3
"""
Annotate a .vis.pb recording via the running segmentation server.

Connects to the segmentation server over HTTP + WebSocket, sends frames
in batches matching the server's buffer size, waits for propagation results
after each batch, and writes SegmentationResponse results to a .seg.pb file.

The segmentation server must already be running (default: http://127.0.0.1:8081).

Usage:
    uv run python segmentation/annotate_recording.py recordings/<name>.vis.pb
    uv run python segmentation/annotate_recording.py recordings/<name>.vis.pb --host http://host:port
"""

import asyncio
import sys
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_server_root))

import yaml
import aiohttp
from tqdm import tqdm

from proto import segmentation_pb2, perceiver_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)

# Read BATCH_SIZE from segmentation config so it stays in sync with the server's
# frame_buffer_size. If the config is missing, fall back to 20.
_cfg_path = Path(__file__).resolve().parent / "segmentation_config.yaml"
try:
    with open(_cfg_path) as _f:
        _seg_cfg = yaml.safe_load(_f)
    BATCH_SIZE: int = _seg_cfg["streaming"]["frame_buffer_size"]
except Exception:
    BATCH_SIZE = 20

DEFAULT_HOST = "http://127.0.0.1:8081"
BATCH_TIMEOUT = 180  # seconds per batch (propagation can be slow)


def seg_path(recording_path: Path) -> Path:
    """YYYYMMDD_HHMMSS.vis.pb -> YYYYMMDD_HHMMSS.seg.pb"""
    name = recording_path.name
    if name.endswith(".vis.pb"):
        return recording_path.parent / (name.removesuffix(".vis.pb") + ".seg.pb")
    return recording_path.with_suffix(".seg.pb")


async def main():
    # ── Parse args ────────────────────────────────────────────────────────
    args = sys.argv[1:]
    host = DEFAULT_HOST
    rec_file = None

    for arg in args:
        if arg.startswith("--host"):
            continue
        if args and args[args.index(arg) - 1] == "--host":
            continue
        rec_file = arg

    if "--host" in args:
        host = args[args.index("--host") + 1]

    if not rec_file:
        print(f"Usage: {sys.argv[0]} <recording.vis.pb> [--host http://host:port]")
        sys.exit(1)

    rec_path = Path(rec_file).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = seg_path(rec_path)
    ws_url = host.replace("http://", "ws://").replace("https://", "wss://")

    # ── Load frames ───────────────────────────────────────────────────────
    print(f"Loading {rec_path.name}...")
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames in recording")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames")

    # ── Connect to segmentation server ────────────────────────────────────
    http_session = aiohttp.ClientSession()
    try:
        async with http_session.get(f"{host}/segment/status", timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status != 200:
                print(f"Segmentation server returned {resp.status}")
                sys.exit(1)
            status = await resp.json()
            print(f"Server: {status.get('model_type', '?')} on {status.get('device', '?')}")
    except aiohttp.ClientConnectorError:
        print(f"Cannot connect to segmentation server at {host}")
        await http_session.close()
        sys.exit(1)

    # Start a session
    async with http_session.post(f"{host}/segment/session/start") as resp:
        data = await resp.json()
        session_id = data["session_id"]
    print(f"Session: {session_id}")

    # Open WebSocket
    ws = await http_session.ws_connect(
        f"{ws_url}/segment/stream?session_id={session_id}",
        heartbeat=30,
    )

    # ── Batched send & collect ────────────────────────────────────────────
    # The server has a fixed-size frame buffer (default 20). We send frames
    # in batches of that size, then wait for the full propagation results
    # (one result per buffer frame) before sending the next batch.

    # BATCH_SIZE is read from segmentation_config.yaml at module load time
    # (see top-level BATCH_SIZE). It must match the server's frame_buffer_size.
    results: list[segmentation_pb2.SegmentationResponse] = []
    batch_results: list[segmentation_pb2.SegmentationResponse] = []
    new_result = asyncio.Event()
    ws_closed = asyncio.Event()

    async def listen():
        """Read SegmentationResponse messages from the WebSocket."""
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    resp = segmentation_pb2.SegmentationResponse()
                    resp.ParseFromString(msg.data)
                    batch_results.append(resp)
                    new_result.set()
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            ws_closed.set()

    listen_task = asyncio.create_task(listen())

    total_batches = (len(frames) + BATCH_SIZE - 1) // BATCH_SIZE
    progress = tqdm(total=len(frames), desc="Annotating", unit="frame")

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(frames))
        batch_frames = frames[start:end]

        # Clear batch state
        batch_results.clear()
        new_result.clear()

        # Send this batch
        for frame in batch_frames:
            if ws.closed:
                break
            req = segmentation_pb2.SegmentationRequest()
            req.frame_identifier.CopyFrom(frame.frame_identifier)
            req.image_frame.CopyFrom(frame.rgb_frame)
            try:
                await ws.send_bytes(req.SerializeToString())
            except (ConnectionResetError, aiohttp.ClientConnectionResetError):
                print("\nConnection lost while sending frames")
                break

        if ws.closed:
            break

        # Wait for propagation results. The server processes the buffer and
        # returns ~BATCH_SIZE results. Auto-segment init + propagation may take 60s+.
        # Strategy: keep waiting until we have >= BATCH_SIZE results. Use a 5s
        # gap timeout only when we have a substantial number of results. This
        # avoids exiting prematurely after the auto-segment init result (1 result)
        # while propagation is still running.
        deadline = asyncio.get_event_loop().time() + BATCH_TIMEOUT
        while len(batch_results) < BATCH_SIZE and asyncio.get_event_loop().time() < deadline:
            new_result.clear()
            # If we have >= half the expected results, use gap detection.
            # Otherwise keep waiting with a long timeout for more results to arrive.
            if len(batch_results) >= BATCH_SIZE // 2:
                gap = 5.0
            else:
                gap = 60.0
            try:
                await asyncio.wait_for(new_result.wait(), timeout=gap)
            except asyncio.TimeoutError:
                if len(batch_results) >= BATCH_SIZE // 2:
                    break  # Enough results, gap detected
                # Few results — keep waiting for propagation

        results.extend(batch_results)
        progress.update(len(batch_frames))

        if batch_results:
            total_masks = sum(len(r.masks) for r in batch_results)
            tqdm.write(f"  Batch {batch_idx+1}/{total_batches}: {len(batch_results)} results, {total_masks} masks")
        else:
            tqdm.write(f"  Batch {batch_idx+1}/{total_batches}: no results")

    progress.close()

    listen_task.cancel()
    try:
        await listen_task
    except asyncio.CancelledError:
        pass

    # ── Save results ──────────────────────────────────────────────────────
    if results:
        if out_path.exists():
            out_path.unlink()
        _seg_io.write_file(out_path, results)
        total_masks = sum(len(r.masks) for r in results)

        # Coverage stats
        frame_numbers_with_results = {r.frame_identifier.frame_number for r in results}
        coverage_pct = len(frame_numbers_with_results) / len(frames) * 100
        useful = sum(1 for r in results if any(m.confidence > 0 and m.pixel_count > 0 for m in r.masks))
        print(f"\nWrote {len(results)} results ({total_masks} masks) to {out_path.name}")
        print(f"Coverage: {len(frame_numbers_with_results)}/{len(frames)} frames ({coverage_pct:.1f}%)")
        print(f"Useful results (non-zero masks): {useful}/{len(results)}")
    else:
        print("No segmentation results received")

    # ── Cleanup ───────────────────────────────────────────────────────────
    await ws.close()
    try:
        await http_session.delete(f"{host}/segment/session/{session_id}")
    except Exception:
        pass
    await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
