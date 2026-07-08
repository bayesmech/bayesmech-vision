#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv/bin ]; then
  export PATH="$PWD/.venv/bin:$PATH"
elif [ -d .vggt-service-venv/bin ]; then
  export PATH="$PWD/.vggt-service-venv/bin:$PATH"
fi
if [ -f .vggt-api.env ]; then
  set -a
  . ./.vggt-api.env
  set +a
fi
if [ -x .venv/bin/uvicorn ]; then
  exec .venv/bin/uvicorn services.vggt_api:app --host "${VGGT_HOST:-0.0.0.0}" --port "${VGGT_PORT:-18080}" --timeout-keep-alive 120
fi
if [ -x .vggt-service-venv/bin/uvicorn ]; then
  exec .vggt-service-venv/bin/uvicorn services.vggt_api:app --host "${VGGT_HOST:-0.0.0.0}" --port "${VGGT_PORT:-18080}" --timeout-keep-alive 120
fi
exec uv run uvicorn services.vggt_api:app --host "${VGGT_HOST:-0.0.0.0}" --port "${VGGT_PORT:-18080}" --timeout-keep-alive 120
