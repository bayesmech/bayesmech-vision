FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps first (cached layer — only rebuilds if pyproject.toml or uv.lock changes)
COPY server/streamlog/pyproject.toml server/streamlog/uv.lock server/streamlog/
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --project server/streamlog --frozen --no-dev

# Copy application source (venv is at /app/.venv, unaffected by these copies)
COPY proto/ proto/
# Dashboard dist pre-built before docker build (run `npm run build` in analysis/dashboard/)
COPY analysis/dashboard/dist/ dashboard/dist/
COPY server/streamlog/ server/streamlog/

# recordings bind-mounted at runtime
RUN mkdir -p recordings

# PYTHONPATH lets uvicorn find `streamlog` package under server/
ENV PYTHONPATH=/app/server:/app

EXPOSE 8080

CMD ["/app/.venv/bin/uvicorn", "streamlog.main:app", "--host", "0.0.0.0", "--port", "8080"]
