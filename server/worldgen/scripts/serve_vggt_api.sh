#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .vggt-api.env ]; then
  set -a
  . ./.vggt-api.env
  set +a
fi
exec .vggt-service-venv/bin/uvicorn services.vggt_api:app --host "${VGGT_HOST:-0.0.0.0}" --port "${VGGT_PORT:-18080}" --timeout-keep-alive 120
