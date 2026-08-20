# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication Style
- Responses in English only
- Technical, terse, engineering-first tone
- No introductory summaries or recaps
- No filler phrases ("Heute entsteht genau dieser Drift:", "Folge:", etc.)
- State problems and solutions directly: problem → root cause → fix
- Use inline code for all file paths, commands, identifiers
- Max 5 bullet points per topic; no nested elaboration

## What this is

Mate is a locally-hosted, modular **process mining** platform. Two app services (`api` + `web`) plus a bundled Keycloak (OIDC) for login. All application data is embedded and on-disk (SQLite metadata + DuckDB/Parquet event logs) – no broker, no cloud. Every user gets a fully isolated workspace; nothing crosses accounts.

Deeper rationale lives in [`INSTRUCTIONS.md`](./INSTRUCTIONS.md) (design) and [`modules/README.md`](./modules/README.md) (the module authoring contract). [`DEPLOY.md`](./DEPLOY.md) covers the VM deploy. Read those before large changes – this file is the orientation, not the full spec.

## Commands

Two toolchains: **uv** (Python workspace) and **pnpm** (JS workspace). The `Makefile` is the source of truth – `make help` lists everything.

```bash
make install        # uv sync --extra dev  +  pnpm install
make dev            # run API (uvicorn --reload) + web (next dev) together, no Docker
make dev-api        # API only – runs alembic upgrade head first, then uvicorn
make dev-web        # web only
make test           # uv run --extra dev pytest apps/api/tests -v
make typecheck      # cd apps/web && pnpm typecheck (tsc --noEmit)
make fmt            # ruff check --fix . && ruff format .
make codegen        # regenerate apps/web/lib/api-types.ts from the running API's /openapi.json
make up / up-dev    # docker compose (prod-style / dev overlay with hot reload)
```

- **Run a single Python test:** `uv run --extra dev pytest apps/api/tests/test_foo.py::test_bar -v`
- **Module tests** live in `modules/<folder>/tests/`: `uv run pytest modules/<folder>/tests`
- **Type-check Python:** `uv run pyright` (strict mode, `apps/api/src` + `packages/module-sdk-py/src`).
- **DB migration:** add a file under `apps/api/alembic/versions/`; `make dev-api` and the api container both run `alembic upgrade head` on boot.
- After changing API route signatures or Pydantic schemas, run `make codegen` (API must be running on `:8000`) so the web app's generated types stay in sync.

## Architecture

### Monorepo layout
- `apps/api/` – FastAPI backend (Python 3.12, `src/mate/api/`).
- `apps/web/` – Next.js 15 (App Router, React 19, Tailwind, shadcn-style UI in `components/ui/`).
- `packages/module-sdk-py/` – Python SDK module authors subclass (`mate.sdk`).
- `packages/module-sdk-ts/` – TS SDK for module frontends (`@mate/module-sdk-ts`).
- `packages/shared-types/` – generated TS types from OpenAPI.
- `modules/` – bundled module packages, discovered at startup (see below).
- `data/` – bind-mounted: `metadata.db` (SQLite), per-user Parquet under `users/{user_id}/`, uploaded modules, cached uv runtimes. `make clean` wipes it.

The Python workspace (`pyproject.toml` `[tool.uv.workspace]`) has members `apps/api` + `packages/module-sdk-py`. The pnpm workspace (`pnpm-workspace.yaml`) has `apps/web`, `packages/module-sdk-ts`, `packages/shared-types`.

### The module system (the heart of the platform)
Modules are the extension mechanism – process-discovery, performance, complexity, drift detection, etc. all ship as modules under `modules/`, using the exact same SDK as third-party/user modules (no privileged first-party hooks).

- **Loader** (`apps/api/src/mate/api/modules/loader.py`) discovers `modules/*/manifest.yaml` on startup, validates the dep graph, materialises each module's own `.venv` (`uv venv` + `uv pip install`, deps declared in the manifest, hashed to skip unchanged) and esbuilt frontend bundle (`.dist/`), then topologically mounts them: routes at `/api/v1/modules/{id}/*`, `@on_event` handlers on the bus, `@job` handlers on the queue. **in_process venvs are built on the platform's exact interpreter (ABI-locked, currently 3.12) – a manifest's `requires-python` is a validation gate for in_process but an interpreter selector for subprocess** (`installer.py`). The repo's `.python-version` pins the platform to 3.12 so dev and Docker agree.
- A module is a `Module` subclass (`module.py`) instantiated **once per process**. Handlers receive a `ModuleContext` (`packages/module-sdk-py/src/mate/sdk/context.py`) exposing the event log (DuckDB/pandas/polars/pm4py), per-`(log_id, module_id)` cache, config, progress reporter, bus, and capability registry. All as typed Protocols – depend on the Protocol, not the impl.
- Modules talk to each other two ways: the **event bus** (fire-and-forget, must declare `provides:`/`consumes:` in the manifest) and the **capability registry** (typed request/response RPC).
- `isolation: subprocess` in a manifest runs the module in a long-lived worker on its own venv Python, proxied over Unix-socket JSON-RPC (`subprocess_host.py` / `subprocess_worker.py`) – for a **different Python version** than the platform or a hard native-lib conflict. Supports `@route`/`@job`/`@on_event`; DataFrames (`event_log.pandas()/polars()/pm4py()`) cross via a Parquet handoff on the shared filesystem.
- **Per-user ownership** is reference-counted via the `module_installs` table – modules are shared in-process code but each user owns their install (gates list/upload/delete). Don't assume a module is globally enabled.
- **Frontend panels** are bundled separately by `apps/web/scripts/bundle-modules.mjs` (runs on `predev`/`build` and watches in dev), NOT by the Next build. Panels may only import `@/` paths listed in `apps/web/lib/runtime-externals.json` – other imports break the esbuild bundle.

When working on a module, never edit `apps/api` or `apps/web` to ship it, and never touch `apps/api/pyproject.toml` / `apps/web/package.json` from a module – its deps belong in its manifest.

### Event bus (`apps/api/src/mate/api/events/bus.py`)
In-process pub/sub. Each subscriber has a bounded queue (oldest dropped when full – a `job.progress` tick is expendable). Drives the frontend `GET /api/v1/events` (topic-filtered SSE) and per-job `GET /api/v1/jobs/{id}/stream` (also SSE). These are **Server-Sent Events, not WebSocket** – the prod proxy chain (uni edge proxy → Caddy → api) carries HTTP streaming transparently but drops WS upgrades, so a WS handshake reaches the API as a plain `GET` and 404s; the client (`apps/web/lib/ws.ts`, name kept for the module-SDK alias) reads SSE over `fetch` with a `Bearer` header. **Tenant-isolation invariant:** every user-scoped bus event MUST carry `user_id` – the stream fan-out filters by it server-side, and omitting it leaks the event to all users.

### Job runtime (`apps/api/src/mate/api/jobs/runtime.py`)
asyncio-based queue (no external broker). Long operations (`@job` handlers, log import) run here and stream `job.queued|started|progress|completed|failed|cancelled` over the bus. `main.py`'s `_job_event_recorder_loop` mirrors terminal job events into the analytics stream, so the runtime itself needs no tracking code.

### Job progress & precompute ordering
- **Progress is optional, never enforced.** `@job(progress=False)` is the default; `ctx.progress.update(...)` takes a fraction `[0,1]`, an absolute `current`+`total`, or a running `int` counter. A silent job degrades to an indeterminate "Working…" bar; a running job that stops ticking for `STALL_THRESHOLD_MS` (3 min, `apps/web/lib/stores/jobs.ts`) shows a client-side "no progress" hint. Long jobs *should* emit something live — it's a guideline + author-checklist item, not a runtime gate.
- **Precompute jobs run in parallel by default.** A module precomputes by stacking `@on_event("log.imported")` (or `ocel.imported`) + `@job`; all such jobs for one import run concurrently up to worker concurrency.
- **Ordering is declared via `provides`/`consumes`, not a new field.** To run *after* another module, subscribe to its reserved `<module_id>.completed` event — the platform auto-emits it (with `import_job_id`) when that module's precompute job succeeds (`ModuleProcessingCoordinator.on_terminal_job`). A failed/cancelled upstream emits nothing, so dependents are **cascade-skipped**, never stranded.
- **The readiness gate waits for the whole chain.** `processing → ready` is held until every module in the transitive precompute closure (`ModuleLoader.precompute_closure`, frozen at import in `EventLog.expected_modules`) has a terminal job or is skipped. Only **job-backed** `@on_event` handlers enter the closure — a `@on_event` with no `@job` creates no `Job` row and must never gate a log. The import job's `payload_json.precompute_plan` (+ a `job.plan` bus event) feeds the jobs UI's waiting/skipped checklist.

### Auth & tenant isolation
Login is mandatory. Web side: Auth.js v5 (`apps/web/auth.ts`), JWT-only sessions (no DB adapter), Keycloak OIDC, token rotation in the `jwt` callback. API side: PyJWT + JWKS validation (`apps/api/src/mate/api/auth/`). Every resource is keyed by the Keycloak `sub` claim; `CurrentUserDep` resolves it and upserts the `users` row on first sighting. On-disk data lives under `data/users/{user_id}/`. The realm role `admin` gates `/admin/export`. The browser calls the FastAPI backend **directly** via `NEXT_PUBLIC_API_URL` (CORS-configured); server components use `apiServer()` with the cookie-backed token.

### Event log ingest
Upload XES / XES.gz / CSV → parsed (`apps/api/src/mate/api/ingest/`) → stored as Parquet, queried via a pooled DuckDB connection (`duckdb/pool.py`). Modules read logs through `ctx.event_log`; prefer DuckDB for aggregations.

### Web app
Next.js App Router under `apps/web/app/`: `(auth)/` for login, `(platform)/` for the authed app (`processes`, `models`, `dashboards`, `settings`, `admin`, `profile`). Data fetching via TanStack Query (`lib/queries.ts`, `lib/*-queries.ts`); client state via Zustand (`lib/stores/`). The typed fetch wrapper is `lib/api.ts` (browser) / `lib/api-server.ts` (server). `@/*` → `apps/web/`, `@modules/*` → `modules/`.

## Conventions
- Python: ruff (line length 100, formatter-enforced) + pyright strict. Alembic version files are exempt from lint.
- Analytics/tracking: capture **every** DOM click, not just interactive elements. Avoid ad-blocker trigger words (`/analytics`, `/events`, `/track`) in API paths – use `/usage`, `/sync`, `/insights` instead (existing routes already follow this).
- Onboarding completion and other account-scoped flags are **per-user server state** (UserSetting via the API), not localStorage.
- Mutations that edit an existing resource are **optimistic**: `onMutate` cancels in-flight queries, snapshots the cache, and writes the expected next state; `onError` rolls back from the snapshot; `onSettled` invalidates to reconcile with the server (canonical examples in `lib/queries.ts`). Plain `onSuccess`-invalidate is fine only for create/duplicate, where there's no prior row to update.
