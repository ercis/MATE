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

WORKDIR /app

# Workspace metadata first – better layer caching for dependency resolution.
# `.python-version` pins uv to 3.12 so dev (host-mode) and the image resolve
# the same interpreter – in_process module venvs are ABI-locked to it.
COPY .python-version ./
COPY pyproject.toml ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/module-sdk-py/pyproject.toml packages/module-sdk-py/pyproject.toml
COPY packages/module-sdk-py/README.md packages/module-sdk-py/README.md

# Source – needed to resolve workspace member packages.
COPY apps/api/src apps/api/src
COPY apps/api/alembic apps/api/alembic
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/README.md apps/api/README.md
COPY packages/module-sdk-py/src packages/module-sdk-py/src

RUN uv sync --frozen --no-dev || uv sync --no-dev

ENV PATH="/app/.venv/bin:${PATH}" \
    DATA_DIR=/app/data \
    DATABASE_URL=sqlite+aiosqlite:////app/data/metadata.db

EXPOSE 8000

CMD ["sh", "-c", "alembic -c apps/api/alembic.ini upgrade head && uvicorn mate.api.main:app --host 0.0.0.0 --port 8000"]
