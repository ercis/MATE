# mate-api

FastAPI backend for the Mate platform.

## Local development

From the repo root:

```bash
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn mate.api.main:app --reload --app-dir apps/api/src
```

The API listens on `http://localhost:8000`. OpenAPI is at `/openapi.json`, interactive docs at `/docs`.

Persistent state lives in `data/` (bind-mounted under Docker): SQLite metadata at `data/metadata.db`, per-log Parquet under `data/event_logs/{logId}/`.
