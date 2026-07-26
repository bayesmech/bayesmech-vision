#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_SOURCE=""
RUNNER_HOST_OVERRIDE=""
RUNNER_PORT_OVERRIDE=""
START_RUNNER=1
INSTALL_SYSTEM_PACKAGES=1

usage() {
  cat <<'EOF'
Usage: server/runner/setup_remote.sh [options]

Set up the BayesMech GPU runner, VGGT-Omega checkpoint, Gaussian Splatting
extensions, and Streamable HTTP MCP server on this machine.

Options:
  --env PATH                 Read secrets and settings from PATH.
  --host HOST                Runner bind address (default: env value or 127.0.0.1).
  --port PORT                Runner port (default: env value or 8787).
  --no-start                 Install and verify without starting the runner.
  --skip-system-packages     Do not install build tools or the CUDA compiler.
  -h, --help                 Show this help.

When --env is omitted, the script asks for the .env path. The private installed
copy is stored at ~/.bayesmech/runner.env with mode 600.
EOF
}

while (($#)); do
  case "$1" in
    --env)
      ENV_SOURCE="${2:?--env requires a path}"
      shift 2
      ;;
    --host)
      RUNNER_HOST_OVERRIDE="${2:?--host requires a value}"
      shift 2
      ;;
    --port)
      RUNNER_PORT_OVERRIDE="${2:?--port requires a value}"
      shift 2
      ;;
    --no-start)
      START_RUNNER=0
      shift
      ;;
    --skip-system-packages)
      INSTALL_SYSTEM_PACKAGES=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$ENV_SOURCE" ]]; then
  read -r -p "Path to the runner .env file: " ENV_SOURCE
fi
ENV_SOURCE="$(realpath -e "$ENV_SOURCE")"
if [[ ! -f "$ENV_SOURCE" ]]; then
  echo "Environment file not found: $ENV_SOURCE" >&2
  exit 1
fi

RUNNER_STATE_DIR="$HOME/.bayesmech"
RUNNER_ENV_FILE="$RUNNER_STATE_DIR/runner.env"
mkdir -p "$RUNNER_STATE_DIR"
chmod 700 "$RUNNER_STATE_DIR"
if [[ "$ENV_SOURCE" != "$(realpath -m "$RUNNER_ENV_FILE")" ]]; then
  install -m 600 "$ENV_SOURCE" "$RUNNER_ENV_FILE"
else
  chmod 600 "$RUNNER_ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$RUNNER_ENV_FILE"
set +a

ensure_env() {
  local key="$1"
  local value="$2"
  local temporary
  temporary="$(mktemp "$RUNNER_STATE_DIR/runner.env.XXXXXX")"
  awk -v key="$key" '
    $0 !~ "^[[:space:]]*(export[[:space:]]+)?" key "[[:space:]]*="
  ' "$RUNNER_ENV_FILE" > "$temporary"
  printf '%s=%q\n' "$key" "$value" >> "$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$RUNNER_ENV_FILE"
  export "$key=$value"
}

if [[ -n "$RUNNER_HOST_OVERRIDE" ]]; then
  ensure_env RUNNER_HOST "$RUNNER_HOST_OVERRIDE"
elif [[ -z "${RUNNER_HOST:-}" ]]; then
  ensure_env RUNNER_HOST "127.0.0.1"
fi
if [[ -n "$RUNNER_PORT_OVERRIDE" ]]; then
  ensure_env RUNNER_PORT "$RUNNER_PORT_OVERRIDE"
elif [[ -z "${RUNNER_PORT:-}" ]]; then
  ensure_env RUNNER_PORT "8787"
fi
if [[ -z "${RUNNER_TOKEN:-}" ]]; then
  case "$RUNNER_HOST" in
    localhost|127.0.0.1|::1)
      echo "Loopback runner selected; bearer authentication remains optional."
      ;;
    *)
      ensure_env RUNNER_TOKEN "$(openssl rand -hex 32)"
      echo "Generated RUNNER_TOKEN in the private runner environment file."
      ;;
  esac
fi
if [[ -z "${RUNNER_DATA_DIR:-}" ]]; then
  ensure_env RUNNER_DATA_DIR "$RUNNER_STATE_DIR/runner"
fi

if ((INSTALL_SYSTEM_PACKAGES)); then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic system-package setup currently requires Ubuntu/Debian." >&2
    echo "Install git, curl, a C++ compiler, and an nvcc matching PyTorch CUDA 12.6, then rerun with --skip-system-packages." >&2
    exit 1
  fi
  SUDO=()
  if [[ "$(id -u)" -ne 0 ]]; then
    SUDO=(sudo)
  fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git build-essential g++-12 ninja-build openssl

  if ! command -v nvcc >/dev/null 2>&1; then
    if ! apt-cache show cuda-compiler-12-6 >/dev/null 2>&1; then
      # Install NVIDIA's repository keyring when the machine image does not
      # already provide CUDA packages.
      # shellcheck disable=SC1091
      source /etc/os-release
      case "${ID:-}:${VERSION_ID:-}" in
        ubuntu:22.04) CUDA_REPO_DIST="ubuntu2204" ;;
        ubuntu:24.04) CUDA_REPO_DIST="ubuntu2404" ;;
        *)
          echo "CUDA 12.6 repository setup is unsupported on ${PRETTY_NAME:-this OS}." >&2
          echo "Install nvcc 12.6 manually, then rerun with --skip-system-packages." >&2
          exit 1
          ;;
      esac
      case "$(uname -m)" in
        x86_64) CUDA_REPO_ARCH="x86_64" ;;
        aarch64) CUDA_REPO_ARCH="sbsa" ;;
        *)
          echo "Unsupported CUDA architecture: $(uname -m)" >&2
          exit 1
          ;;
      esac
      CUDA_KEYRING="$(mktemp --suffix=.deb)"
      curl -fsSL \
        "https://developer.download.nvidia.com/compute/cuda/repos/$CUDA_REPO_DIST/$CUDA_REPO_ARCH/cuda-keyring_1.1-1_all.deb" \
        -o "$CUDA_KEYRING"
      "${SUDO[@]}" dpkg -i "$CUDA_KEYRING"
      rm -f "$CUDA_KEYRING"
      "${SUDO[@]}" apt-get update
    fi
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y cuda-compiler-12-6
  fi
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
  UV_BIN="$HOME/.local/bin/uv"
else
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  UV_BIN="$HOME/.local/bin/uv"
fi

"$UV_BIN" python install 3.12
cd "$SERVER_DIR"
"$UV_BIN" sync --locked
"$UV_BIN" pip install --python "$SERVER_DIR/.venv/bin/python" \
  -e "$SERVER_DIR/worldgen/vendor/vggt_omega"

CUDA_HOME_VALUE="${CUDA_HOME:-}"
if [[ -z "$CUDA_HOME_VALUE" ]]; then
  if [[ -d /usr/local/cuda-12.6 ]]; then
    CUDA_HOME_VALUE="/usr/local/cuda-12.6"
  elif command -v nvcc >/dev/null 2>&1; then
    CUDA_HOME_VALUE="$(dirname -- "$(dirname -- "$(readlink -f "$(command -v nvcc)")")")"
  else
    echo "nvcc was not found; Gaussian Splatting cannot compile its CUDA extension." >&2
    exit 1
  fi
fi
ensure_env CUDA_HOME "$CUDA_HOME_VALUE"

GPU_ARCH="$("$SERVER_DIR/.venv/bin/python" - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit("A CUDA GPU is required for the remote World Modeling runner.")
major, minor = torch.cuda.get_device_capability()
print(f"{major}.{minor}")
PY
)"
ensure_env TORCH_CUDA_ARCH_LIST "${TORCH_CUDA_ARCH_LIST:-$GPU_ARCH}"
ensure_env MAX_JOBS "${MAX_JOBS:-6}"
ensure_env VGGT_MODEL_ID "${VGGT_MODEL_ID:-facebook/VGGT-Omega}"
ensure_env VGGT_MODEL_FILENAME "${VGGT_MODEL_FILENAME:-vggt_omega_1b_512.pt}"

CHECKPOINT_DIR="$HOME/.cache/bayesmech/models/VGGT-Omega"
CHECKPOINT_PATH="${VGGT_CKPT:-$CHECKPOINT_DIR/${VGGT_MODEL_FILENAME}}"
ensure_env VGGT_CKPT "$CHECKPOINT_PATH"
mkdir -p "$(dirname -- "$CHECKPOINT_PATH")"
if [[ ! -s "$CHECKPOINT_PATH" ]]; then
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "HF_TOKEN is missing from the supplied .env file." >&2
    echo "It is required to download the gated facebook/VGGT-Omega checkpoint." >&2
    exit 1
  fi
  echo "Downloading the gated VGGT-Omega checkpoint..."
  DOWNLOAD_DIR="$(mktemp -d)"
  "$SERVER_DIR/.venv/bin/hf" download \
    "$VGGT_MODEL_ID" "$VGGT_MODEL_FILENAME" \
    --local-dir "$DOWNLOAD_DIR"
  install -m 600 "$DOWNLOAD_DIR/$VGGT_MODEL_FILENAME" "$CHECKPOINT_PATH"
  rm -rf "$DOWNLOAD_DIR"
fi

set -a
# shellcheck disable=SC1090
source "$RUNNER_ENV_FILE"
set +a
export PATH="$SERVER_DIR/.venv/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

echo "Precompiling and checking the gsplat CUDA extension..."
"$SERVER_DIR/.venv/bin/python" - <<'PY'
import torch
from gsplat.cuda._backend import _C

assert torch.cuda.is_available()
_C
print(f"gsplat ready on {torch.cuda.get_device_name(0)}")
PY

echo "Checking the VGGT-Omega checkpoint..."
"$SERVER_DIR/.venv/bin/python" - <<'PY'
import os
from pathlib import Path

checkpoint = Path(os.environ["VGGT_CKPT"])
if checkpoint.stat().st_size < 1_000_000:
    raise SystemExit(f"Checkpoint is unexpectedly small: {checkpoint}")
print(f"VGGT-Omega checkpoint ready ({checkpoint.stat().st_size} bytes)")
PY

if ((START_RUNNER)); then
  RUNNER_PID_FILE="$RUNNER_STATE_DIR/runner.pid"
  RUNNER_LOG_FILE="$RUNNER_STATE_DIR/runner.log"
  if [[ -f "$RUNNER_PID_FILE" ]]; then
    OLD_PID="$(<"$RUNNER_PID_FILE")"
    if [[ "$OLD_PID" =~ ^[0-9]+$ ]] && kill -0 "$OLD_PID" 2>/dev/null; then
      OLD_COMMAND="$(ps -p "$OLD_PID" -o args= || true)"
      if [[ "$OLD_COMMAND" == *"-m runner"* ]]; then
        kill "$OLD_PID"
        for _ in {1..30}; do
          kill -0 "$OLD_PID" 2>/dev/null || break
          sleep 1
        done
        if kill -0 "$OLD_PID" 2>/dev/null; then
          echo "Existing runner process $OLD_PID did not stop; refusing to start a second process." >&2
          exit 1
        fi
      fi
    fi
  fi

  RUNNER_ENV_FILE="$RUNNER_ENV_FILE" nohup "$SCRIPT_DIR/start_remote.sh" \
    >> "$RUNNER_LOG_FILE" 2>&1 &
  RUNNER_PID=$!
  printf '%s\n' "$RUNNER_PID" > "$RUNNER_PID_FILE"
  chmod 600 "$RUNNER_PID_FILE"

  HEALTH_HOST="$RUNNER_HOST"
  if [[ "$HEALTH_HOST" == "0.0.0.0" || "$HEALTH_HOST" == "::" ]]; then
    HEALTH_HOST="127.0.0.1"
  fi
  RUNNER_SCHEME="http"
  if [[ -n "${RUNNER_TLS_CERT:-}" ]]; then
    RUNNER_SCHEME="https"
  fi
  for _ in {1..60}; do
    if curl -kfsS "$RUNNER_SCHEME://$HEALTH_HOST:$RUNNER_PORT/health" >/dev/null; then
      echo "Runner ready: $RUNNER_SCHEME://$HEALTH_HOST:$RUNNER_PORT"
      echo "MCP endpoint: $RUNNER_SCHEME://$HEALTH_HOST:$RUNNER_PORT/mcp/"
      exit 0
    fi
    if ! kill -0 "$RUNNER_PID" 2>/dev/null; then
      echo "Runner exited during startup. Last log lines:" >&2
      tail -n 80 "$RUNNER_LOG_FILE" >&2
      exit 1
    fi
    sleep 1
  done
  echo "Runner did not become healthy. Last log lines:" >&2
  tail -n 80 "$RUNNER_LOG_FILE" >&2
  exit 1
fi

echo "Runner installation verified. Start it with:"
echo "  RUNNER_ENV_FILE=$RUNNER_ENV_FILE $SCRIPT_DIR/start_remote.sh"
