"""Length-delimited protobuf I/O used by Python analyzer scripts.

Wire format: [uint32 big-endian length][serialized protobuf bytes]...
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TypeVar

from google.protobuf.message import Message

M = TypeVar("M", bound=Message)


class ProtoIO:
    FRAME_SIZE_LIMIT = 512 * 1024 * 1024

    def __init__(self, msg_type: type[M]) -> None:
        self._msg_type = msg_type

    def encode(self, messages: list[Message]) -> bytes:
        parts: list[bytes] = []
        for msg in messages:
            raw = msg.SerializeToString()
            parts.append(struct.pack(">I", len(raw)))
            parts.append(raw)
        return b"".join(parts)

    def decode(self, data: bytes) -> list[M]:
        messages: list[M] = []
        offset = 0
        while offset + 4 <= len(data):
            (length,) = struct.unpack(">I", data[offset : offset + 4])
            offset += 4
            if length == 0 or length > self.FRAME_SIZE_LIMIT:
                raise ValueError(f"suspicious length prefix {length}")
            end = offset + length
            if end > len(data):
                raise ValueError("truncated length-delimited protobuf record")
            msg = self._msg_type()
            msg.ParseFromString(data[offset:end])
            messages.append(msg)
            offset = end
        if offset != len(data):
            raise ValueError("trailing partial length prefix")
        return messages

    def read_file(self, path: Path) -> list[M]:
        messages: list[M] = []
        with open(path, "rb") as f:
            while True:
                header = f.read(4)
                if not header:
                    break
                if len(header) < 4:
                    raise ValueError("truncated length prefix")
                (length,) = struct.unpack(">I", header)
                if length == 0 or length > self.FRAME_SIZE_LIMIT:
                    raise ValueError(f"suspicious length prefix {length}")
                data = f.read(length)
                if len(data) < length:
                    raise ValueError("truncated length-delimited protobuf record")
                msg = self._msg_type()
                msg.ParseFromString(data)
                messages.append(msg)
        return messages

    def write_file(self, path: Path, messages: list[Message]) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as f:
            f.write(self.encode(messages))
        return len(messages)
