# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# git – required by the module install + AI-refactor flows when the source
# is a git URL (shell-out via `asyncio.create_subprocess_exec("git", ...)`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv binary
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Temurin JRE – runs JVM modules (`runtime: {kind: jvm}`, see modules/PROTOCOL.md).
# A full JRE rather than a jlink-minimised runtime: uploaded module jars are
# arbitrary code and may need any JDK module (java.sql, jdk.unsupported, ...).
# jammy binaries run fine on bookworm (glibc 2.35 < 2.36, forward-compatible).
COPY --from=eclipse-temurin:21-jre-jammy /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:${PATH}"

WORKDIR /app

# Workspace metadata first – better layer caching for dependency resolution.
# `.python-version` pins uv to 3.12 so dev (host-mode) and the image resolve
# the same interpreter – in_process module venvs are ABI-locked to it.
COPY .python-version ./
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/module-sdk-py/pyproject.toml packages/module-sdk-py/pyproject.toml
COPY packages/module-sdk-py/README.md packages/module-sdk-py/README.md

# Source – needed to resolve workspace member packages.
COPY apps/api/src apps/api/src
COPY apps/api/alembic apps/api/alembic
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/README.md apps/api/README.md
COPY packages/module-sdk-py/src packages/module-sdk-py/src

# --frozen, no fallback: the image must install exactly what uv.lock pins. The
# old `|| uv sync --no-dev` escape hatch silently re-resolved whenever --frozen
# failed (it always did – uv.lock wasn't COPYed), so the image drifted to
# whatever was newest on PyPI while dev stayed on the lock. A stale lock should
# fail the build loudly; fix it with `uv lock`, don't resolve around it.
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    DATA_DIR=/app/data \
    DATABASE_URL=sqlite+aiosqlite:////app/data/metadata.db

EXPOSE 8000

# `exec` so uvicorn replaces the shell as the container's main process: without
# it a non-interactive `sh` sits in front as the signal target, ignores the
# SIGTERM `docker stop` / `compose down` sends (PID 1 has no default handler),
# and the lifespan shutdown never runs - the container is SIGKILLed at the grace
# deadline instead. With `exec`, uvicorn gets the signal and drains gracefully.
# `init: true` in compose adds a PID-1 reaper for any escaped grandchild.
CMD ["sh", "-c", "alembic -c apps/api/alembic.ini upgrade head && exec mate-api --host 0.0.0.0 --port 8000"]
