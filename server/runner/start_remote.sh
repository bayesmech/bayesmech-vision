#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNNER_ENV_FILE="${RUNNER_ENV_FILE:-$HOME/.bayesmech/runner.env}"

if [[ ! -f "$RUNNER_ENV_FILE" ]]; then
  echo "Runner environment file not found: $RUNNER_ENV_FILE" >&2
  echo "Run $SCRIPT_DIR/setup_remote.sh first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$RUNNER_ENV_FILE"
set +a

export PATH="$SERVER_DIR/.venv/bin:${CUDA_HOME:-/usr/local/cuda}/bin:$PATH"
if [[ -n "${CUDA_HOME:-}" ]]; then
  export LD_LIBRARY_PATH="$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# The setup step has already placed the gated checkpoint on disk. Do not retain
# the Hugging Face credential in the long-running, network-facing process.
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN

cd "$SERVER_DIR"
exec "$SERVER_DIR/.venv/bin/python" -m runner
