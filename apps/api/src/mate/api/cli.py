"""CLI entry point - `mate-api` script (declared in pyproject.toml).

Currently a thin wrapper around uvicorn so the API can be launched without
remembering the full module path. Heavier subcommands (db migrate, module
sync, etc.) land alongside the relevant phases.
"""

from __future__ import annotations

import argparse

import uvicorn

from mate.api.shutdown import set_shutdown_probe


def main() -> None:
    parser = argparse.ArgumentParser(prog="mate-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    # Reload mode runs the app inside a reloader-managed subprocess, so a Server
    # built here isn't the one serving requests - keep the string form and leave
    # the shutdown probe unregistered (SSE falls back to the grace timeout).
    if args.reload:
        uvicorn.run(
            "mate.api.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            timeout_graceful_shutdown=10,
        )
        return

    # Prod: own the Server so SSE streams can poll `should_exit` and self-close
    # during the connection-drain phase, before the grace timeout force-cancels
    # them (which otherwise dumps a CancelledError ASGI traceback on shutdown).
    config = uvicorn.Config(
        "mate.api.main:app",
        host=args.host,
        port=args.port,
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    set_shutdown_probe(lambda: server.should_exit)
    server.run()


if __name__ == "__main__":
    main()
