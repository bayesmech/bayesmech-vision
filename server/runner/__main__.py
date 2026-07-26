from __future__ import annotations

import ipaddress
import os

import uvicorn

from .app import RunnerSettings, app


def _public_bind(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def main() -> None:
    settings = RunnerSettings.from_env()
    host = os.environ.get("RUNNER_HOST", "127.0.0.1")
    port = int(os.environ.get("RUNNER_PORT", "8787"))
    certificate = os.environ.get("RUNNER_TLS_CERT", "").strip() or None
    private_key = os.environ.get("RUNNER_TLS_KEY", "").strip() or None

    if _public_bind(host) and not settings.token:
        raise SystemExit("RUNNER_TOKEN must be set when RUNNER_HOST is publicly reachable")
    if bool(certificate) != bool(private_key):
        raise SystemExit("RUNNER_TLS_CERT and RUNNER_TLS_KEY must be configured together")

    uvicorn.run(
        app,
        host=host,
        port=port,
        timeout_keep_alive=120,
        ssl_certfile=certificate,
        ssl_keyfile=private_key,
    )


if __name__ == "__main__":
    main()
