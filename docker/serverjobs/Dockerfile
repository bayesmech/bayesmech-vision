# pytorch/pytorch base already has: CUDA 12.6, cuDNN, Python 3.11, torch 2.7.0
# This eliminates the apt Python install (and tzdata hang) + the torch download from uv
FROM pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel

# git is required for SAM-2 GitHub source install
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps — torch/torchvision/torchaudio already in base image so uv skips them
# Venv goes to /app/.venv so the later COPY server/ doesn't overwrite it
COPY server/pyproject.toml server/uv.lock server/.python-version server/
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --project server --frozen --no-dev

# Copy source (venv at /app/.venv is unaffected)
COPY proto/ proto/
COPY server/ server/

# recordings bind-mounted at runtime
RUN mkdir -p recordings

ENV PYTHONPATH=/app/server:/app

# No default CMD — batch jobs are invoked directly, e.g.:
#   docker run --rm --gpus all -v ./recordings:/app/recordings bayesmech-serverjobs \
#     /app/.venv/bin/python server/reconstruct/main.py recordings/<name>/<name>.vis.pb
