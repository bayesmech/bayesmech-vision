# BayesMech Runner

The runner executes BayesMech server pipelines on another machine and returns
their status and artifacts over authenticated Streamable HTTP MCP and REST
APIs. The exact same endpoints work over `127.0.0.1` when the UX and runner
share a machine.

The API only executes jobs from the registry in `registry.py`; it does not
accept arbitrary commands or shell fragments.

## Set up a GPU runner

Clone the same branch used by the UX, then run:

```bash
git clone --recurse-submodules <this-repo>
cd bayesmech-vision
server/runner/setup_remote.sh
```

The script asks for the path to a `.env` file. It installs a private copy at
`~/.bayesmech/runner.env`, generates `RUNNER_TOKEN` for a public bind when one
is absent, installs Python 3.12 and the locked server environment, installs the
vendored VGGT-Omega package, installs the CUDA 12.6 compiler when needed,
downloads the gated `facebook/VGGT-Omega` checkpoint using `HF_TOKEN`, compiles
gsplat for the machine's GPU, verifies both systems, and starts the runner.

For non-interactive provisioning:

```bash
server/runner/setup_remote.sh --env /secure/path/runner.env
```

The setup is idempotent. The process ID and log are kept in
`~/.bayesmech/runner.pid` and `~/.bayesmech/runner.log`. Re-running setup checks
the installed components and replaces the existing runner process. To install
without starting, pass `--no-start`.

## Start on the same machine as the UX

From `server/`:

```bash
uv sync
uv run python -m runner
```

The UX defaults to `http://127.0.0.1:8787/api/v1/worldgen`, so no endpoint
configuration is needed. Authentication is optional only for loopback access.

The runner should use the same repository revision as the base computer.
GPU-backed World Modeling also needs the VGGT-Omega dependencies and checkpoint
described in `../worldgen/README.md`; `/api/v1/worldgen/health` reports whether
CUDA and the checkpoint are available. If `VGGT_CKPT` is absent, the existing
World Modeling service downloads `VGGT_MODEL_ID` from Hugging Face on the first
inference.

VGGT-Omega's official checkpoint is gated. Before the first World Modeling
request, either set `HF_TOKEN` to a Hugging Face token with access to
`facebook/VGGT-Omega`, or download `vggt_omega_1b_512.pt` and set `VGGT_CKPT`
to its absolute path.

## Start on a network or public runner

The setup script defaults to loopback. To bind a network interface:

```bash
server/runner/setup_remote.sh --env /secure/path/runner.env --host 0.0.0.0
```

Then allow TCP port 8787 through the runner host firewall or cloud security
group. A token is generated for the public bind when the supplied environment
does not contain one. Loopback and same-machine runners keep authentication
optional. The equivalent manual start is:

```bash
export RUNNER_TOKEN="$(openssl rand -hex 32)"
export RUNNER_HOST=0.0.0.0
export RUNNER_PORT=8787
uv run python -m runner
```

Configure the base computer:

```bash
RUNNER_ENDPOINT=http://RUNNER_PUBLIC_IP:8787
RUNNER_TOKEN=the-same-token
```

For traffic that crosses the public Internet, terminate TLS in a reverse proxy
or set both `RUNNER_TLS_CERT` and `RUNNER_TLS_KEY`. The certificate must be
valid for the hostname or IP used in `RUNNER_ENDPOINT`. Do not expose an
unauthenticated runner; startup rejects public binds without `RUNNER_TOKEN`.

If the runner is behind NAT, its private IP is not publicly reachable merely
because the service binds to `0.0.0.0`. Configure a router mapping, a public
cloud security group, or a private overlay such as Tailscale.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RUNNER_ENDPOINT` | `http://127.0.0.1:8787` | Base URL used by the UX |
| `RUNNER_TOKEN` | empty | Bearer token; required for non-loopback access |
| `RUNNER_HOST` | `127.0.0.1` | Runner bind address |
| `RUNNER_PORT` | `8787` | Runner TCP port |
| `RUNNER_DATA_DIR` | `~/.bayesmech/runner` | Persistent job workspaces |
| `RUNNER_MAX_WORKERS` | `1` | Concurrent CLI jobs |
| `RUNNER_MAX_UPLOAD_BYTES` | 50 GiB | Per-input and total request upload limit |
| `RUNNER_MAX_RUNTIME_SECONDS` | 86400 | CLI job timeout |
| `RUNNER_TLS_CERT` | empty | Optional TLS certificate |
| `RUNNER_TLS_KEY` | empty | Optional TLS private key |

## MCP tools

The MCP endpoint is:

```text
http://RUNNER_HOST:8787/mcp/
```

It uses the Streamable HTTP transport and the same bearer token as REST. Any
standard MCP client can discover these tools with `tools/list`:

- `runner_health`
- `runner_capabilities`
- `runner_list_jobs`
- `runner_get_job`
- `runner_submit_job`
- `runner_cancel_job`
- `runner_artifact`
- `worldgen_health`
- `worldgen_reconstruct_frames`
- `worldgen_splat_status`
- `worldgen_splat_artifact`

`worldgen_reconstruct_frames` accepts base64 image frames and can launch
Gaussian Splatting with `start_splat=true`. This makes the complete reconstruction
workflow available to an MCP client. MCP tool results also report the streaming
REST routes, which should be used for large videos, point clouds, and PLY
downloads to avoid base64 expansion.

The Electron bridge provides `listRunnerMcpTools()` and
`callRunnerMcpTool(name, args, timeoutMs)`, so the UX can discover and invoke new
runner tools without adding a bespoke IPC operation for every endpoint.

## Streaming REST API

World Modeling is available at `/api/v1/worldgen`. Generic jobs use:

- `GET /api/v1/capabilities`
- `POST /api/v1/jobs`
- `POST /api/v1/jobs/{job_type}/recording`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/jobs/{job_id}/artifacts/{artifact_id}`

All endpoints except `/health` require `Authorization: Bearer $RUNNER_TOKEN`
when a token is configured.

The Electron bridge exposes both durable primitives (`submitRunnerJob`,
`readRunnerJob`, `cancelRunnerJob`, and `downloadRunnerArtifact`) and a
convenience `runRunnerJob` operation. By default it uploads the selected
`.vis.pb` plus same-stem protobuf sidecars, waits for completion, and downloads
changed or newly generated artifacts beside the base computer's recording.
Callers can pass `inputPaths` to provide an exact input set.
