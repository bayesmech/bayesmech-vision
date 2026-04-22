#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IOS_DIR="$ROOT_DIR/ios"
OUT_DIR="$IOS_DIR/BayesMechVision/Proto/Generated"

if ! command -v protoc >/dev/null 2>&1; then
  echo "error: protoc is required to generate Swift protobufs" >&2
  exit 1
fi

if ! command -v protoc-gen-swift >/dev/null 2>&1; then
  echo "error: protoc-gen-swift is required to generate Swift protobufs" >&2
  exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

PROTO_FILES=(
  "$ROOT_DIR/proto/primitives.proto"
  "$ROOT_DIR/proto/spatial.proto"
  "$ROOT_DIR/proto/perceiver.proto"
  "$ROOT_DIR/proto/insightgen.proto"
)

protoc \
  --proto_path="$ROOT_DIR/proto" \
  --swift_opt=Visibility=Public \
  --swift_out="$OUT_DIR" \
  "${PROTO_FILES[@]}"

echo "Generated Swift protobufs in $OUT_DIR"
