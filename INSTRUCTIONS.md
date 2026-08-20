# Mate – Process Analysis Platform

A **locally-hosted, lightweight, modular** process analysis platform. State-of-the-art process mining stack, fully containerised (`docker compose up`), zero cloud dependencies, with a first-class module system that lets new analytics drop into the platform without touching core code.

---

## 1. Design Principles

1. **Local-first.** Everything runs via `docker compose`. No Redis, no Postgres container, no broker. Two services: `api` and `web`. Persistent state in a single bind-mounted `data/` directory.
2. **Lightweight.** Embedded data stores (SQLite + DuckDB + Parquet). No external services required.
3. **Modular.** Modules are first-class. Filesystem-discovered with declared capabilities, requirements and dependencies. New modules drop in; the platform picks them up.
4. **Type-safe end-to-end.** Pydantic v2 (Python) ↔ Zod / generated TypeScript types from the OpenAPI schema (web).
5. **Async by default.** FastAPI async, asyncio-based job queue, WebSocket progress streams. CPU-bound work goes to `to_thread` / `ProcessPoolExecutor`.
6. **Forward-compatible.** Storage format is a strict superset of the present needs and an easy upgrade path to **OCEL 2.0**.

---

## 2. Tech Stack

### 2.1 Backend (`apps/api`)

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12+ | pm4py ecosystem, mature async |
| Web framework | FastAPI | async, OpenAPI built-in, Pydantic-native |
| Package manager | `uv` | fast, deterministic, lockfile-based |
| Linter / formatter | `ruff` | one tool, zero config |
| Type checker | `pyright` (strict) | fast, accurate |
| Metadata DB | **SQLite** (WAL mode, `aiosqlite`) | zero-ops, transactional, fits our scale |
| Event-log storage | **Parquet** files | columnar, compressed, OLAP-friendly |
| Analytics engine | **DuckDB** | embedded SQL over Parquet – sub-second queries on millions of events |
| Process mining | `pm4py` | de facto standard |
| Validation | Pydantic v2 | fast, JSON-schema generation |
| Migrations | Alembic | works fine with SQLite |
| Job queue | asyncio `Queue` + SQLite-persisted jobs | no broker, no extra container |
| Realtime | native WebSockets (FastAPI) | progress + event bus fan-out |

### 2.2 Frontend (`apps/web`)

| Concern | Choice |
|---|---|
| Framework | Next.js 15 (App Router, RSC where useful) |
| Language | TypeScript 5.x strict |
| Package manager | pnpm (workspace) |
| Server state | TanStack Query v5 |
| Client state | Zustand (with `persist` for UI prefs) |
| UI | shadcn/ui + Tailwind v4 |
| Charts | Recharts + ECharts (heavier viz) |
| Graph canvas | xyflow (React Flow) |
| Realtime | native WebSocket client |
| Validation | Zod (schemas mirrored from Pydantic) |
| Type sync | `openapi-typescript` codegen from `/openapi.json` |

---

## 3. Event Log Storage Strategy

### 3.1 Accepted import formats

| Format | Status | Notes |
|---|---|---|
| **XES** (`.xes`, `.xes.gz`) | primary | IEEE-1849 standard for process mining |
| **CSV** | primary | flat-file with column-mapping wizard at import |
| **XML** (generic) | secondary | mapping-based, advanced users |
| **OCEL 2.0** (`.jsonocel`, `.xmlocel`, `.sqlite`) | reserved (forward-compatible) | extend the on-disk schema; same APIs |

### 3.2 Internal storage – **Parquet + DuckDB**

XES on disk is too slow to query repeatedly (hundreds of MB of XML to parse for a single aggregation). Therefore on import we **normalise once** and store a canonical, columnar form.

**On-disk layout per imported log:**

```
data/event_logs/{log_id}/
├── meta.json          # source format, ingest stats, detected schema, OCEL flag, mapping
├── events.parquet     # flat event table, sorted by (case_id, timestamp)
├── cases.parquet      # cached case-level aggregates
├── original.{ext}     # original upload (for audit / re-export)
└── ocel/              # object-centric tables, written on OCEL import
    ├── events.parquet
    ├── objects.parquet
    ├── relations.parquet  # flattened event↔object map
    └── o2o.parquet        # object↔object relations
```

**Why Parquet?**
- Columnar → group-by activity / resource / case is essentially free.
- Compressed (~5–20× smaller than XES on disk).
- DuckDB reads it directly – no ETL on every query.
- pandas / polars / pm4py all read Parquet natively.
- Forward-compatible with **OCEL** by simply adding `objects.parquet` + `relations.parquet`.

**Why not store events in SQLite?** SQLite is row-oriented – for million-event logs, columnar wins by 10–100× on aggregations.

**Why not Postgres?** It would mean a second container, a DB user/password, migrations against shared state. DuckDB gives the analytical power without operational burden. If a multi-user, multi-writer deployment ever becomes a need, swap DuckDB → Postgres + TimescaleDB and keep the API surface intact.

### 3.3 Hybrid persistence

| Data | Where | Why |
|---|---|---|
| Event records (large, immutable) | Parquet on disk | columnar, fast scans |
| Module result caches | Parquet / JSON per `(log_id, module_id)` | reuse across runs |
| Process logs metadata, jobs, users, module configs, layouts | SQLite | row-oriented, transactional |
| Live ad-hoc queries | DuckDB | unified SQL across Parquet + SQLite |

DuckDB attaches both transparently:

```sql
ATTACH 'data/metadata.db' AS meta (TYPE sqlite);
SELECT *
FROM read_parquet('data/event_logs/abc/events.parquet') e
JOIN meta.process_logs p USING (log_id);
```

### 3.4 Decision summary (your questions)

- **CSV / XML / XES / OCEL** – accept all four on import; **XES** is primary, **OCEL 2.0** is the reserved upgrade target.
- **Internal canonical storage** – **Parquet** (events) + **JSON manifest** (metadata).
- **SQL or NoSQL?** – **SQL, but split**: SQLite for metadata, DuckDB on Parquet for analytics. Best of both: transactional small-data, columnar big-data, no extra services.

---

## 4. Repository Layout

```
mate/
├── apps/
│   ├── api/                       # FastAPI backend
│   └── web/                       # Next.js frontend
├── modules/                       # Discoverable module packages – flat drop-in directory (empty at v1)
│   └── (no modules ship with v1 – this is the platform's extension point)
├── packages/
│   ├── module-sdk-py/             # Python SDK for module authors
│   ├── module-sdk-ts/             # TS SDK for frontend module authors
│   └── shared-types/              # Generated TS types from OpenAPI
├── data/                          # Bind-mounted in Docker
│   ├── event_logs/
│   ├── module_results/
│   └── metadata.db                # SQLite
├── docker-compose.yml
├── pyproject.toml                 # uv workspace
└── pnpm-workspace.yaml
```

`modules/` is intentionally **outside** `apps/api`. It is the platform's extension point – modules can be authored, version-controlled, and shipped independently of the core. The directory is **flat**: drop a module folder in and it's picked up. **Folder names are arbitrary** – only the `id` and `category` declared in `manifest.yaml` are authoritative. The platform never groups, filters, or routes by directory path.

---

## 5. Module System

### 5.1 Module manifest (`manifest.yaml`)

Every module ships a manifest. Validated on load.

The example below uses `performance` as an illustrative module id – no such module exists in v1 (see §11). It's a worked-out shape so the schema is concrete; treat it as documentation, not a stub.

```yaml
id: performance                     # globally unique snake_case
                                    # (illustrative – `performance` is not implemented in v1)
name: Performance                   # human-readable
version: 1.2.0
category: foundation                # foundation | attribute | external_input | advanced | other
description: Throughput, lead time, waiting / sojourn time, bottleneck detection.
author: Mate Core
license: MIT

requirements:
  event_log:                        # validated against the log's detected schema
    required_columns:
      - case_id
      - activity
      - timestamp
    optional_columns:
      - resource
      - end_timestamp               # enables waiting / sojourn time
    min_events: 100
    min_cases: 5

  modules: []                       # hard deps – must be loaded for this to load

  optional_modules:                 # soft deps – used if present, warning if not
    - id: discovery
      reason: KPIs are computed per discovered activity; without it, raw activity strings are used.

provides:                           # capabilities advertised on the bus / RPC registry
  - kpi.throughput
  - kpi.lead_time
  - kpi.waiting_time
  - bottleneck.list

consumes:                           # bus events / capabilities this module uses
  - log.imported
  - discovery.completed

dependencies:                       # fully self-contained – installed *into the module folder* at startup
  python:
    requires-python: ">=3.12"
    packages:                       # PEP 631-style; resolved by uv into modules/<folder>/.venv/
      - "scikit-learn>=1.5"
      - "tslearn==0.6.3"
    inherit:                        # platform-provided shared libs – no install, just made importable
      - pm4py
      - pandas
      - polars
      - duckdb
    isolation: in_process           # in_process (default, fast) | subprocess (true isolation)
  npm:                              # frontend – bundled into modules/<folder>/.dist/ at startup
    - "d3-sankey@^0.12"

frontend:
  panel: ./panel/index.tsx          # module page entry point
  widgets:                          # reusable widgets (also usable from other modules' pages)
    - id: throughput-chart
      entry: ./widgets/Throughput.tsx
    - id: bottleneck-table
      entry: ./widgets/BottleneckTable.tsx
  page_layout:                      # default layout (user can customise + persist per-user)
    - section: KPIs
      widgets: [throughput-chart, lead-time-card]
    - section: Bottlenecks
      widgets: [bottleneck-table]

permissions:
  - read:event_log
  - write:module_results
```

### 5.2 Module categories

| Category | Purpose | Example |
|---|---|---|
| `foundation` | Always-relevant baseline | Discovery, Performance, Object-Centric Discovery |
| `attribute` | Mining beyond control-flow (org / time / cost / decisions) | – (no built-in; the manifest schema still supports this category) |
| `external_input` | Compares the log against an externally-supplied artefact | – (no built-in; the manifest schema still supports this category) |
| `advanced` | Specialised analytics | – (no built-in; the manifest schema still supports this category) |
| `other` | Catch-all (utility, integrations) | – |

### 5.3 Module loading (lifecycle)

1. **Discovery (startup).**
   - Scan `modules/*/manifest.yaml` (one level deep – the directory is flat, folder names are arbitrary, the manifest's `id` is authoritative).
   - Also scan installed Python packages exposing the `mate.modules` entry point – supports installable third-party modules without copying files.
   - Two modules declaring the same `id` is a startup error.
2. **Validation.** Parse manifests, build dependency graph, abort startup on cycles or missing **hard** dependencies.
3. **Materialise dependencies (per-module, isolated).** The API container runs `uv venv && uv pip install` into `modules/<folder>/.venv/` when the manifest's `dependencies` hash changes – cached at `modules/<folder>/.installed-hash`, so subsequent boots are near-instant. The web container's bundler (`apps/web/scripts/bundle-modules.mjs`) esbuilds each module's `frontend.panel` + `frontend.widgets` into `modules/<folder>/.dist/` at container start (and watches in dev). The two containers share the bind-mounted `modules/` so they see each other's writes. See §5.4.
4. **Topological load order.** Hard deps loaded first.
5. **Mount.** Register routes at `/api/v1/modules/{id}/*` (FastAPI handles sync→threadpool natively), subscribe `@on_event` handlers on the bus, register `@job` handlers with the queue. The SDK wraps `@on_event` and `@job` handlers with the sync/async auto-wrap described in §5.5 – `@route` handlers ride FastAPI's existing threadpool. Either way, no handler can block the event loop regardless of whether the author used `async def` or plain `def`.
6. **Runtime per-log gating.** When a log is opened, the platform re-evaluates `requirements.event_log` against that log's schema → module is **available** or **unavailable** for that log.
7. **Hot reload (dev).** A `watchdog.Observer` on `modules/` re-loads changed modules without restart, gated on `settings.env == "dev"` so production boots always run with a known module set. 500 ms debounce, excluding writes inside `.venv/` / `.dist/` / `node_modules/`. Manifest dep changes trigger an in-place `uv pip install` for that module only. At startup the loader also calls `sweep_stale_workdirs()` to clean up `ff-mod-*` temp dirs older than 24 h that a previous SIGKILL / crash left behind.

### 5.4 Per-module dependencies & isolation

Each module declares its own runtime dependencies in the manifest. The platform installs them **into the module's own folder** at startup. Deleting the module folder removes everything it added – its venv, its bundled JS, its lockfile, its caches. **The platform's own `pyproject.toml` and `package.json` are never touched.**

#### Backend (Python)

- Each module folder has its own `pyproject.toml` (or the platform synthesises one from `dependencies.python` in `manifest.yaml`).
- At startup the platform runs `uv venv modules/<folder>/.venv && uv sync` for that module. The result is a self-contained virtualenv at `modules/<folder>/.venv/`, with its own lockfile at `modules/<folder>/uv.lock`.
- A content-hash of the manifest's `dependencies` block is cached at `modules/<folder>/.venv/.installed-hash`. On subsequent boots, if the hash matches, the install step is skipped – startup is near-instant.
- Modules are loaded into the platform process via a custom `importlib.abc.MetaPathFinder` that resolves their imports against their own `.venv/site-packages` first. Each module sees only:
  1. Its own venv (highest priority).
  2. Standard library.
  3. Anything declared in `dependencies.python.inherit` (resolved against the platform venv).
  4. The platform SDK (`mate.sdk`).

  It does **not** see other modules' deps or the rest of the platform's deps.

- **`inherit` mechanism.** Process mining modules typically need pm4py / pandas / polars / duckdb. Re-installing those per module would waste hundreds of MB. Listing them in `inherit:` makes them importable from the platform venv without re-installing – cheap and conflict-free, since the platform's versions are the source of truth. Anything *not* inherited is fully isolated.

#### Isolation modes

| Mode | When | How |
|---|---|---|
| `in_process` (default) | Pure-Python deps, or deps with no native conflicts with anything inherited / other modules. | The custom `MetaPathFinder` shims imports against `modules/<folder>/.venv/site-packages`. Fast, zero IPC. |
| `subprocess` | Module needs a native lib version that conflicts with the platform's (e.g. `numpy 1.x` while platform ships `numpy 2.x`). | Platform spawns a long-lived worker subprocess from the module's `.venv/bin/python` via [subprocess_host.py](apps/api/src/mate/api/modules/subprocess_host.py). Communication is bidirectional JSON-RPC over a Unix socket: host → worker for handler calls, worker → host for every `ctx.*` access. Same `ModuleContext` interface; transport is hidden from author. Slower (IPC) but provides true isolation. |

The manifest sets `dependencies.python.isolation: subprocess` explicitly. Auto-promotion is not implemented – authors pick the mode.

**Subprocess isolation – current limitations:**

- Only `@route.*` handlers are supported. `@on_event` / `@job`-decorated methods in a subprocess module raise at load time with a clear error pointing the author back to `in_process`.
- DataFrame views (`ctx.event_log.pandas()` / `polars()` / `pm4py()`) raise inside the worker – shipping a DataFrame across the socket would need Parquet round-trip or Arrow IPC. Use `await ctx.event_log.duckdb_fetch(sql)` instead; it returns JSON-serialisable rows.
- `ctx.registry.has()` (sync) raises; use `await ctx.registry.call(...)` instead.

Both limitations are extension points – the wire protocol in [subprocess_worker.py](apps/api/src/mate/api/modules/subprocess_worker.py) is the place to add them when a module actually needs DataFrame transfer.

#### Frontend (JS / TS)

Same story, with one architectural choice worth flagging: module bundles **must share the host's React, shadcn primitives, and TanStack Query instances** so hook context flows across module boundaries and modules pick up the host's theme automatically. We solve that with a shared runtime contract rather than bundling everything per-module.

- The web container runs [apps/web/scripts/bundle-modules.mjs](apps/web/scripts/bundle-modules.mjs) – esbuilds each module's `frontend.panel` (and any declared `frontend.widgets`) into `modules/<folder>/.dist/<entry>.js` as a **CJS** bundle with every shared dep marked as `external`. The dev script runs it in `--watch` mode alongside `next dev`; the production Docker image runs it once at container start.
- **The shared runtime.** [apps/web/lib/module-runtime.ts](apps/web/lib/module-runtime.ts) lazily imports React, ReactDOM, TanStack Query, xyflow, lucide, recharts, sonner, radix, d3-hierarchy, elkjs, every shadcn primitive used by panels today, plus `@/lib/api`, `@/lib/cn`, `@/lib/format`, `@/lib/ws`, `@/lib/module-widgets`, `@/lib/module-layout`, and the visualization-settings store – and assigns them all to `window.__FF_RUNTIME__`. The list is authoritative: it lives in [apps/web/lib/runtime-externals.json](apps/web/lib/runtime-externals.json) and is read by both the bundler (as the `external:` array) and the runtime installer (which asserts the two stay in sync).
- **Lazy install.** `installModuleRuntime()` returns a Promise and dynamic-imports its 30+ deps so non-module pages never pay the cost – important because some of those packages (xyflow, recharts) inject styles at import time and would otherwise force hydration mismatches on every page.
- **Serving.** Built bundles are served by FastAPI under `/api/v1/modules/{id}/assets/<path>` ([apps/api/src/mate/api/routes/modules.py](apps/api/src/mate/api/routes/modules.py)) – strict path-traversal check, `application/javascript` MIME.
- **Loader.** [apps/web/lib/module-panels.tsx](apps/web/lib/module-panels.tsx) fetches the bundle text, awaits `installModuleRuntime()`, then executes via `new Function(module, exports, require, source)` with a `require()` shim that reads from `window.__FF_RUNTIME__`. Picks `Panel`, `default`, or the first React-component-looking export. `getModulePanel(moduleId, { hasFrontend })` returns the wrapper for any module declaring `frontend.panel` and `null` otherwise (so the placeholder card renders without a 404 fetch).
- **Cross-module widgets.** [apps/web/lib/module-widgets.tsx](apps/web/lib/module-widgets.tsx) exposes `useWidget(sourceModuleId, widgetId)` which resolves a widget bundle via the same loader. Re-exported from `@mate/module-sdk-ts`.
- **Deletion.** Removing the folder removes both `.venv/` and `.dist/`. Nothing in `apps/web/package.json` to revert.

#### Conflict detection & lifecycle

- **Install-time:** `uv` resolves each module's deps independently. A module with an unresolvable spec fails to load (with a clear error) but does not block the rest of the platform.
- **Inherit-conflict:** If a module declares `inherit: [pandas]` but its own `packages:` list also pins a different `pandas`, startup fails with a manifest error.
- **Cross-module conflicts at runtime:** impossible by construction – each module sees only its own venv + inherits + stdlib + SDK.
- **Removal:** delete the module folder. The next platform start observes the manifest is gone and skips it. There is no "uninstall" command to forget – the deps live entirely under the deleted folder.
- **Ignore in VCS:** `modules/*/.venv/`, `modules/*/.dist/`, `modules/*/node_modules/` are gitignored. `manifest.yaml` and `uv.lock` are committed.

#### Trade-offs (worth being explicit about)

- Per-module venvs cost disk space – typically tens of MB per module after `inherit` does its job, hundreds of MB if a module forgoes inheriting and brings its own pandas/numpy. For a local-first tool this is acceptable.
- `subprocess` mode adds ~5–50 ms IPC latency per call. For modules that read large Parquet files via DuckDB locally and only return small results, this is negligible. For chatty modules, prefer `in_process` and `inherit`.
- Hot reload of `dependencies` requires a restart of just that module's subprocess (or, in `in_process` mode, rebuilding its `MetaPathFinder`) – the rest of the platform stays up.

### 5.5 What every module receives – `ModuleContext`

The platform injects a `ModuleContext` into every entry point (route handlers, event handlers, jobs):

```python
class ModuleContext:
    log_id: str                            # the log this invocation is scoped to
    module_id: str                         # the calling module's manifest id
    event_log: EventLogAccess              # lazy access – .duckdb_fetch / .pandas() / .polars() / .pm4py()
    bus: EventBus                          # typed pub/sub (Pydantic enforcement opt-in via events.py – §5.7)
    registry: ModuleRegistry               # inspect + call other modules
    cache: ResultCache                     # per-(log_id, module_id) Parquet/JSON cache
    config: ModuleConfig                   # user-set config (validated against module's schema)
    progress: ProgressReporter             # progress.update(0.42, "Computing KPIs")
    logger: structlog.BoundLogger          # also fans out to module.log.<level> on the bus (§7.6.2)
    workdir: Path                          # scratch space, rmtree'd by the loader after every invocation
    run_in_process: RunInProcessProtocol   # await ctx.run_in_process(fn, *args) – ProcessPool offload, §8.3
```

Workdir cleanup is enforced by the loader's `_invoke_handler` wrapping every handler call in try/finally + `shutil.rmtree(ctx.workdir)`, covering all four invocation sites (`@route` with and without `@job`, `@on_event` with and without `@job`). A startup `sweep_stale_workdirs()` mops up `ff-mod-*` temp dirs older than 24 h that a previous SIGKILL left behind.

`EventLogAccess` is a thin wrapper that hands out the right view of the data on demand:

```python
async with ctx.event_log as log:
    # SQL via DuckDB – preferred for aggregations
    rows = await log.duckdb.fetch("SELECT activity, count(*) FROM events GROUP BY 1")
    # Pandas / Polars – for module code that wants dataframes
    df = await log.polars()
    # pm4py-native – full event log object
    pm_log = await log.pm4py()
```

#### Sync / async safety – divided responsibility

Module authors may write handlers as `async def` or plain `def`; the platform guarantees that no handler can block the event loop. The mechanism differs by handler type, and we leverage FastAPI's built-in behaviour where it already covers us:

| Handler kind | Who handles sync→threadpool | Why |
|---|---|---|
| `@route.*` (HTTP) | **FastAPI** (built-in) | `@route` registers the handler as a FastAPI route. `ModuleContext` is injected via `Depends(get_ctx)`. FastAPI's Starlette layer already runs sync handlers via `run_in_threadpool` – we get this for free. |
| `@on_event` (bus) | **SDK auto-wrap** | The event bus is an in-process pub/sub owned by the SDK, not FastAPI. There is no FastAPI threadpool to lean on – the SDK detects sync vs async at subscribe time and wraps sync handlers in `asyncio.to_thread`. |
| `@job` (job queue) | **SDK auto-wrap** | Job handlers run on the platform's asyncio job queue (§8), not as FastAPI requests. Same deal – the SDK auto-wraps. |

The SDK auto-wrap implementation (used by `@on_event` and `@job`):

```python
import inspect, asyncio

def _autowrap(fn):
    if inspect.iscoroutinefunction(fn):
        async def runner(self, ctx, *a, **kw):
            return await fn(self, ctx, *a, **kw)
    else:
        async def runner(self, ctx, *a, **kw):
            return await asyncio.to_thread(fn, self, ctx, *a, **kw)
    return runner
```

Notes:

- `inspect.iscoroutinefunction()` is the canonical check across modern Python; `asyncio.iscoroutinefunction` is just an alias to it. The legacy `@asyncio.coroutine` generator-based form was removed in Python 3.11, so this single check covers every coroutine the SDK will encounter.
- The auto-wrap is invisible to authors – it's a safety net, not an API.
- For `@route` handlers, the SDK does **not** add its own wrap; that would be redundant with FastAPI's threadpool and would only add an extra task switch. `ModuleContext` injection happens via `Depends`, which keeps `@route` handlers behaving like idiomatic FastAPI routes.

### 5.6 Module entry point – `module.py`

`PerformanceModule` below is the actual class shipped under [modules/performance/](modules/performance/); the second snippet (`ExtensionModule`) is a synthetic illustration of the `@job` decorator. Both show the SDK shape any module author writes.

```python
from mate.sdk import Module, ModuleContext, on_event, route

class PerformanceModule(Module):
    id = "performance"

    @on_event("log.imported")
    async def precompute(self, ctx: ModuleContext, payload):
        async with ctx.event_log as log:
            kpis = await self.compute_kpis(log)
        await ctx.cache.set("kpis", kpis)
        await ctx.bus.emit("kpi.computed", {"log_id": ctx.log_id, "kpis": kpis})

    @route.get("/kpis")
    async def get_kpis(self, ctx: ModuleContext):
        return await ctx.cache.get("kpis") or await self.compute_kpis(ctx)
```

No registration boilerplate – the manifest **is** the registration.

#### `@job` – opt-in for long-running operations

For operations expected to run more than a few seconds (discovery on large logs, exhaustive variant analysis, expensive aggregations), the SDK provides an optional `@job` decorator. It turns a route or event handler into a non-blocking job that integrates with the platform's job queue, progress streaming, and the bottom-left dock + drawer (§7.9).

> **Why not just FastAPI's `BackgroundTasks`?** `BackgroundTasks` is in-process fire-and-forget – no persistence (lost on restart), no progress streaming, no cancellation, no queue ordering, no cross-request observability. The drawer, toasts and dock (§7.9) require **persisted, observable, cancellable** jobs. `@job` is the thin author-facing API on top of the platform's own queue (§8), and it's only needed for long-running work – short routes return their value directly, the way FastAPI users expect.

```python
from mate.sdk import Module, ModuleContext, route, job

class ExtensionModule(Module):                    # synthetic illustration only
    id = "extension"

    @route.post("/expensive-aggregate")
    @job(progress=True, title="Extension – heavy aggregation")
    async def expensive_aggregate(self, ctx: ModuleContext):
        async with ctx.event_log as log:
            await ctx.progress.update(0.1, "Loading inputs")
            inputs = await self.load_inputs(ctx)
            await ctx.progress.update(0.4, "Aggregating")
            result = await self.aggregate(log, inputs)
            await ctx.progress.update(1.0, "Done")
        return result
```

When `@job` is present:

- The endpoint immediately returns `{ "job_id": "..." }` instead of blocking the request.
- The SDK inserts a row into the SQLite jobs table (with `module_id`, the supplied `title`, and a default `subtitle` derived from the route/payload), pushes the work onto the platform's asyncio job queue, and assigns the job a UUID v7.
- Progress flows through `ctx.progress.update(fraction_or_count, message)` and is broadcast over `WS /events` (`job.*`) and `WS /jobs/{job_id}/stream` – exactly like the import job.
- The frontend handles the `{ job_id }` response generically: it shows a Sonner toast, the new job appears in the dock and drawer, and any `successUrl` declared on the decorator is opened on completion.
- The auto-wrap from §5.5 still applies – `@job` works on `async def` and `def` handlers identically.

`@job` is **opt-in and additive** – simple, fast operations stay as plain route handlers and return their value directly. Only operations expected to run for more than a few seconds should use it.

Decorator parameters:

| Parameter | Default | Purpose |
|---|---|---|
| `progress` | `False` | Enable `ctx.progress.update(...)` streaming: a fraction `[0,1]`, an absolute `current`+`total`, or a running `int` counter. Optional and never enforced — a silent job degrades to an indeterminate "Working…" bar (plus a client-side stall hint after ~3 min). Long jobs *should* still emit something live. |
| `title` | derived from handler name | Toast + drawer headline. Can also be a callable `(ctx_stub, payload) -> str` resolved at submission time (the loader passes a minimal `{log_id, module_id}` stub since the real `ModuleContext` doesn't exist yet – the job hasn't run). Any exception in the callable falls back to the static default. |
| `subtitle` | derived from `module_id` + route | Drawer subtitle. Also supports the same callable form as `title`. |
| `priority` | `0` | Higher = scheduled sooner; queued jobs are reorderable in the drawer. |
| `cancellable` | `True` | Whether the *Cancel* button is enabled in the drawer. |
| `result_url` | `None` | Optional template (`/processes/{log_id}/modules/{module_id}`) for the toast's *Open* action on success. |

### 5.7 Module-to-module communication

Two patterns, both type-safe.

**(a) Event bus – async pub/sub, fire-and-forget or fan-out.**

```python
# emit
await ctx.bus.emit("kpi.computed", KpiPayload(log_id=..., kpis=...))

# subscribe
@on_event("kpi.computed")
async def react(self, ctx, payload: KpiPayload): ...
```

Events are typed via Pydantic schemas declared in the source module's `events.py` – the loader looks for an `EVENT_SCHEMAS: dict[str, type[BaseModel]]` mapping in that file and registers each `(topic, model)` pair on the bus at load time. Enforcement is **opt-in per topic**: registered topics validate the payload at publish time and raise `EventSchemaError` on mismatch; unregistered topics (including the platform's `job.*` and `log.imported`) pass through untouched so internal events don't have to round-trip through Pydantic.

**(b) Capability registry – typed RPC for synchronous queries.**

```python
if ctx.registry.has("discovery"):
    dfg = await ctx.registry.call("discovery.dfg", log_id=ctx.log_id)
else:
    ctx.logger.warning("discovery module not present – falling back to raw activity strings")
```

Capabilities are declared in `provides:`. The platform validates at startup that callers only call capabilities listed in their `consumes:` or `optional_modules:` – missing-dep bugs surface at boot, not at runtime.

**(c) Precompute ordering – run a precompute job after another module.**

A module precomputes on import by stacking `@on_event("log.imported")` (or `ocel.imported`) + `@job`; all such jobs for one import run **in parallel** up to worker concurrency. To run *after* another module, subscribe to its reserved `<module_id>.completed` event instead:

```python
@on_event("discovery.completed")   # fires only after `discovery` precompute succeeds
@job(progress=True, title="Performance – overlays")
async def precompute(self, ctx, payload): ...
```

The producer does nothing special — the platform **auto-emits `<module_id>.completed`** (carrying `import_job_id`) when a module's precompute job succeeds. The consumer just `@on_event`-subscribes and declares the edge for discoverability/validation: `consumes: [discovery.completed]` + `optional_modules: [discovery]`. The platform freezes the **transitive precompute closure** at import time and holds the log in `processing` until every module in it reaches a terminal job; a failed/cancelled upstream emits nothing, so its dependents are **cascade-skipped** rather than stranding the log. Only **job-backed** subscriptions gate a log — a `@on_event` with no `@job` is a fire-and-forget hook (e.g. a cheap cache refresh) and never holds `processing`.

### 5.8 How dependency presence is surfaced to the user

When the user opens a process page:

- The platform checks each module's `requirements.event_log` against the log's detected schema → **available / unavailable** (e.g. *"This module needs a `resource` column on events"*).
- The platform checks `requirements.modules` (hard) and `optional_modules` (soft) against the loaded module set → status badge on the module card (e.g. *"This module works better with Discovery – currently disabled"*).
- Unavailable modules are rendered **greyed-out** with an inline tooltip explaining what is missing and (where applicable) how to fix it (install the missing module, add the required column, etc.).

---

## 6. API Surface (`/api/v1/`)

| Path | Purpose |
|---|---|
| `POST /event-logs` | Multipart upload; returns `{ log_id, job_id }` |
| `GET /event-logs` | List logs with status + stats |
| `GET /event-logs/{id}` | Detail incl. detected schema |
| `DELETE /event-logs/{id}` | Tombstone + cleanup |
| `GET /jobs/{id}` | Poll job state |
| `WS /jobs/{id}/stream` | Real-time progress (lines processed / total) |
| `GET /modules` | List manifests + per-log availability |
| `GET /modules/{id}/manifest` | Full manifest |
| `GET / PUT /modules/{id}/config` | User config |
| `GET / PUT /modules/{id}/layout?log_id=...` | Per-(user, log, module) layout JSON for the widget grid (§7.7) |
| `GET /modules/{id}/assets/{path}` | Serve `modules/<id>/.dist/<path>` – the bundled panel + widgets (§5.4) |
| `POST /modules/install` | Multipart zip / tar.gz upload – returns `{ job_id }` |
| `POST /modules/install/git` | `{ url, ref? }` – clone + install – returns `{ job_id }` |
| `POST /modules/install/registry` | `{ source: "pypi", id, version? }` – `uv pip install` + re-scan entry points |
| `DELETE /modules/{id}` | Uninstall – unloads, deletes the folder, emits `module.uninstalled` |
| `*  /modules/{id}/...` | Module-defined routes |
| `GET /system/storage` | Filesystem totals + `data/` & `modules/` directory sizes for the Settings → General gauge |
| `GET /system/diagnostics` | Single JSON blob for the *Copy diagnostics* button (§7.6.3) |
| `WS /events` | Subscribe to platform-wide events for live UI updates (`?topic=` repeatable; `module.log.*` carries per-module log lines) |

OpenAPI is generated by FastAPI; `apps/web` consumes the schema via `openapi-typescript` codegen – no hand-written API types.

---

## 7. Frontend Structure

### 7.1 Design system & UI guidelines

- **shadcn/ui everywhere it fits** – `Button`, `Input`, `Card`, `Dialog`, `DropdownMenu`, `Sheet`, `Tabs`, `Tooltip`, `Toast` (Sonner), `Table`, `Form` (with `react-hook-form` + Zod resolver), `Skeleton`, `Switch`, `Slider`, `Progress`, `Badge`, `Breadcrumb`, `Command` (cmd-K palette). Custom components only when shadcn doesn't cover a need.
- **Modern look.** Tailwind v4, generous whitespace, soft elevation (one or two `shadow-sm`/`shadow-md` levels), 12 px / 16 px / 24 px spacing scale, rounded-`xl` cards, subtle motion via `framer-motion` (≤ 200 ms easings only). Focus on calm, dense-when-needed dashboards – not glassmorphism or heavy gradients.
- **Light + dark mode.** `next-themes` with `system` default. All colours live behind CSS variables (`--background`, `--foreground`, `--primary`, `--muted`, `--accent`, `--destructive`, `--surface`, `--surface-elevated`, etc.) defined in `globals.css`. **Zero hardcoded colours in components.** Theme toggle in the sidebar footer (sun / moon / system tri-state via shadcn `DropdownMenu`).
- **Cursor + interaction.** Every clickable element gets `cursor-pointer` on hover. Disabled elements use `cursor-not-allowed` + a tooltip explaining why. Keyboard-first: every action reachable by Tab + Enter; cmd-K palette for global navigation and process search.
- **Iconography.** `lucide-react` only – no emoji, no mixed icon sets.
- **Empty states & errors.** Every list/section has a designed empty state (icon + headline + one-line hint + primary CTA) and a designed error state. Never render a blank panel.
- **Loading.** `Skeleton` placeholders matching the eventual layout (not spinners) for above-the-fold content; spinners only inside buttons during submit.
- **Accessibility.** Honour `prefers-reduced-motion`; AA contrast in both themes; semantic landmarks (`<header>`, `<main>`, `<nav>`); shadcn's Radix primitives provide ARIA out of the box – keep that.
- **Density toggle.** Sidebar setting for *Comfortable* / *Compact* affects table row height and card padding (persisted in `ui.store`).

### 7.2 Routing & URLs

```
apps/web/app/(platform)/
├── layout.tsx                           # sidebar + topbar + theme provider + toaster
├── page.tsx                             # redirects to /processes
├── processes/
│   ├── page.tsx                         # landing: list + import + ERP/CRM connect (greyed)
│   ├── import/page.tsx                  # drop zone + column-mapping wizard (CSV)
│   └── [logId]/                         # logId is a UUID v7
│       ├── page.tsx                     # process detail: sectioned grid of module cards
│       └── modules/
│           └── [moduleId]/page.tsx      # single-module deep view for this log
└── settings/
    ├── page.tsx                         # tabbed: General | Modules | About
    ├── general/page.tsx                 # appearance, density, data dir, telemetry
    └── modules/
        ├── page.tsx                     # installed modules grid
        ├── [moduleId]/page.tsx          # per-module config & details
        └── import/page.tsx              # import a new module
```

**URL identity.** Event logs are addressed by **UUID v7** (`logId`) embedded in the URL – `/processes/{logId}` and `/processes/{logId}/modules/{moduleId}`. UUID v7 is time-ordered (good for indexing & natural sort) and globally unique without coordination. The UUID is also the directory name in `data/event_logs/{logId}/`. The user-visible *display name* of a log is editable and stored in SQLite (`process_logs.name`); URLs never use the display name.

`moduleId` in URLs is the manifest `id` (snake_case, e.g. `performance`).

### 7.3 Processes landing page (`/processes`)

The default landing page after start. Three regions, top-down:

1. **Header.** Page title "Processes", description, and a **primary action group** (top-right):
   - `Button` (primary) – *Import event log* → opens `/processes/import`.
   - Overflow `DropdownMenu` for: *Import from URL*, *Import demo log*.

2. **Process list.** A shadcn `Table` (or `Card` grid in *Comfortable* density). Columns:
   - Display name (editable via inline `Popover` rename).
   - Status – `Badge`: `importing` / `ready` / `failed` (with inline retry).
   - Cases / events / variants (small numeric stats).
   - Date range covered by the log.
   - Imported at (relative time, full timestamp on hover).
   - Source format.
   - Row actions (`DropdownMenu`): *Open*, *Rename*, *Re-run import*, *Export*, *Delete* (with `AlertDialog` confirm).
   - **In-progress rows are greyed and non-clickable**, with a small inline progress bar showing live `current/total` (powered by `WS /jobs/{job_id}/stream`).
   - **Failed rows** show the failure reason in a `HoverCard` and offer *Retry*.

3. **Empty state.** First-run users see a centered illustration + headline ("Import your first event log") + the same primary action above + a *Try with sample data* secondary action that loads a bundled XES demo.

URL search params: `?q=` (filter) and `?status=ready|importing|failed` (filter). The topbar search drives the same `q` param.

### 7.4 Process detail page (`/processes/{logId}`)

Opens when the user clicks a *ready* process. Layout:

- **Header bar.** Display name (inline editable), source format `Badge`, key stats inline (`12,438 cases · 184,620 events · 17 variants`), date range, **`Tabs`**: *Overview* (the module grid, default) · *Events* (browseable table) · *Variants* · *Settings* (just for this log: rename, re-import, delete).
- **Module grid (Overview tab).** Sectioned by `category` in this fixed order: **Foundation → Attribute → External Input → Advanced Process Analytics → Other**. Each section is a labelled subheader with its module cards laid out in a responsive 1 / 2 / 3 / 4-column grid.

Each module card (shadcn `Card`):

| State | Visual | Behaviour |
|---|---|---|
| **available** | Full colour, hover lift, `cursor-pointer` | Clicking opens `/processes/{logId}/modules/{moduleId}`. Card body shows the module's lightweight summary (`frontend.panel` may export a `Summary` component; otherwise a default 2-line description). |
| **unavailable – log requirements unmet** | Greyed (`opacity-60`, no hover lift), `cursor-not-allowed` | Click is suppressed. A `Tooltip` lists the unmet `requirements.event_log` (e.g. *"Needs a `cost` column on events"*, *"Needs at least 100 events; this log has 23"*). A muted `Badge` reads "Requirements not met". |
| **unavailable – missing hard module dep** | Greyed | `Tooltip`: *"Requires the *Discovery* module – not currently installed. Open Settings → Modules to install."* with a link. |
| **degraded – missing optional module dep** | Full colour, but with an amber `Badge` "Limited" | Clickable. Inline note explains what's missing and what feature is unavailable. |
| **disabled by user (in Settings)** | Hidden by default; toggle in section header to show greyed | Clicking is suppressed; tooltip directs to Settings. |
| **failed to load (manifest / install error)** | Red-tinted card | Clickable to view error details; never throws in the rest of the UI. |

- **Filtering & search.** A small filter bar above the grid: *Show unavailable* `Switch`, *Show disabled* `Switch`, free-text filter. Persisted in `ui.store`.
- **Per-card top-right** `DropdownMenu` (kebab): *About this module*, *Configure*, *Open in new tab*.

### 7.5 Module page (`/processes/{logId}/modules/{moduleId}`)

Renders the module's `frontend.panel` against this specific log. Layout:

- **Breadcrumb:** `Processes / {log display name} / {module name}`.
- **Module header:** name, version, category `Badge`, *Configure* button (opens a `Sheet` with the module's config schema rendered as a shadcn `Form`), *Reset layout* button.
- **Body:** the module's customisable layout (see §7.7) – sections + widgets defined by the manifest, mutable per-user.
- **Side rail (optional):** modules can declare a `frontend.side_rail` entry – useful for filters, time-window selectors, etc.

Cross-linking: any widget can deep-link to another module on the same log via a stable URL (`/processes/{logId}/modules/{otherId}?focus=...`) – preserves the `logId` so the user always stays on this process.

### 7.6 Settings page (`/settings`)

Top-level shadcn `Tabs`: **General · Modules · About**.

#### 7.6.1 General

shadcn `Form` sections:

- **Appearance.** Theme (`Light` / `Dark` / `System`, `RadioGroup`), Density (`Comfortable` / `Compact`, `RadioGroup`), Accent colour (`RadioGroup` over a small palette using CSS-variable presets), Reduced motion (`Switch`, defaults to system).
- **Locale & imports.** Timezone (IANA name, defaulted from `Intl.DateTimeFormat().resolvedOptions().timeZone`), date format (`iso` / `us` / `eu`), default CSV delimiter, default CSV timestamp format. The CSV defaults pre-fill the import form so users don't re-pick on every upload.
- **Data & storage.** Live gauge powered by `GET /api/v1/system/storage` – filesystem total/used/free progress bar plus per-directory byte totals for `data/` and `modules/`, showing the absolute paths of each bind-mount.
- **Jobs.** Worker concurrency – a live slider backed by `GET`/`PUT /system/jobs`. Any user sees the current value; an **admin** can change it, which resizes the asyncio worker pool immediately (graceful – running jobs are never interrupted) and persists it in `system_settings` so the change survives a restart. This is a **system-wide** setting (one shared runtime for all tenants), not part of the per-user settings sync.
- **Telemetry.** Off by default; an opt-in switch (the platform is local-first, no telemetry without consent).

Settings live in client-side zustand stores (`useUi`, [apps/web/lib/stores/ui.ts](apps/web/lib/stores/ui.ts); `useVizSettings`) with the `persist` middleware writing to `localStorage` for instant reads. [apps/web/components/server-state-sync.tsx](apps/web/components/server-state-sync.tsx) then bridges these stores to per-user server state in `user_settings (key, value_json)`: each store is hydrated from the server on sign-in and changes are debounce-saved back, so prefs are per-account and follow the user across browsers instead of bleeding between accounts via shared `localStorage`.

#### 7.6.2 Modules

A grid of cards, one per **installed** module:

- **Card content:** name, category `Badge`, version, author, short description, status (`Active` / `Inactive` / `Failed to load`), *Configure* button, **enable/disable `Switch`**, kebab menu (*View manifest*, *Update*, *Uninstall* with confirm).
- **Filtering:** by category, status, free-text search.
- **Top-right action:** *Import module* button → `/settings/modules/import`.

Each module's *Configure* opens the deep page (`/settings/modules/{moduleId}`):

- **About:** rendered manifest (id, version, category, requires-python, author, license, link to docs if declared).
- **Requirements:** event-log requirements + dep status (✓ / ✗ for each `requirements.modules` and `optional_modules`).
- **Provides / consumes:** capability tables, plus a live xyflow graph ([apps/web/components/settings/capability-graph.tsx](apps/web/components/settings/capability-graph.tsx)) showing which other installed modules reference this one (edges are `provides → consumes` with the capability name as label).
- **Configuration:** the module's `config_schema` rendered as a shadcn `Form` with Zod validation. Save persists to `module_configs` in SQLite.
- **Dependencies:** Python + npm packages from the manifest's `dependencies` block, with their resolved versions from the lockfile, install size, and a *Reinstall* button.
- **Logs:** tail of the last 50 `ctx.logger.*` lines, streamed over `WS /api/v1/events` subscribed to `module.log.*` and filtered client-side by `payload.module_id`. The loader wraps every `ctx.logger` call to also publish onto that topic ([apps/web/components/settings/module-logs-tail.tsx](apps/web/components/settings/module-logs-tail.tsx)).
- **Danger zone:** *Disable for all logs*, *Uninstall*.
  Removes the module folder, its venv, and any cached results. The platform's own dependencies are unaffected.

##### Importing a new module (`/settings/modules/import`)

- Three input methods (`Tabs`):
  1. **Upload `.zip` / `.tar.gz` / `.tar` / `.tgz`** – drop zone, posts multipart to `POST /api/v1/modules/install`. The route streams the upload to `data/module-uploads/`, submits a `module.install.upload` job that unpacks via `_safe_extract`, validates `manifest.yaml`, calls `loader.load_one()`.
  2. **From git URL** – `POST /api/v1/modules/install/git` with `{ url, ref? }`. Job clones with `--depth 1` (optionally `--branch <ref>`), strips `.git/`, then same load path.
  3. **From PyPI** – `POST /api/v1/modules/install/registry` with `{ source: "pypi", id, version? }`. Job runs `uv pip install <spec>` against the platform venv, then re-scans `importlib.metadata.entry_points(group="mate.modules")` and loads any new module that declared one (§5.3 step 1). The `source: "npm"` variant raises `NotImplementedError` by design – an npm-only package ships no Python entry point for the loader to bind to, so there is nothing to mount in-process (use the Upload / Git / PyPI paths instead).
- All three return `{ "job_id": "..." }` so the frontend's existing dock/drawer surfaces progress and the toast actions work uniformly with import jobs.
- On success, a toast + automatic redirect to `/settings/modules/{moduleId}`.
- On failure, the dock row shows the offending stage + error message. The job handler `rmtree`s any half-extracted staging dir in `finally`.

#### 7.6.3 About

Platform version, license, an "Onboarding → Restart" button, and a one-click *Copy diagnostics* button. The button hits `GET /api/v1/system/diagnostics` and writes the returned JSON blob (platform version, Python version, OS, settings, installed module summaries with isolation mode) to the clipboard for pasting into a support thread.

### 7.7 Per-module page customisation

- Each module supplies a default `frontend.page_layout` in its manifest.
- **Layout persistence is wired.** The `module_layouts (user_id, log_id, module_id, layout_json)` SQLite table is live, with `GET / PUT /api/v1/modules/{id}/layout?log_id=...` and a `useModuleLayout(moduleId, logId)` + `useSaveModuleLayout(...)` hook pair in [apps/web/lib/module-layout.ts](apps/web/lib/module-layout.ts) (re-exported from `@mate/module-sdk-ts`). The drag-drop UI itself (`react-grid-layout`) is unrendered today because the shipping modules render monolithic panels rather than widget grids – once a module declares `frontend.widgets` and uses the hook to read/write its layout, the editor surface goes live without a server change.
- Widgets share a small contract: `Widget(props: { logId, moduleId, config }) → ReactNode`.
- **Cross-module widget reuse** – a widget exposed by one module can be embedded on another module's page, provided the source module is installed. `useWidget(sourceModuleId, widgetId)` from `@mate/module-sdk-ts` ([apps/web/lib/module-widgets.tsx](apps/web/lib/module-widgets.tsx)) resolves the bundle lazily via the same loader as panels, renders a `Skeleton`, then a placeholder card if the source module is missing or disabled. The bundler in [bundle-modules.mjs](apps/web/scripts/bundle-modules.mjs) already produces one bundle per `frontend.widgets[]` entry (`.dist/widget-<id>.js`).
- **Public SDK surface.** [packages/module-sdk-ts/src/index.ts](packages/module-sdk-ts/src/index.ts) re-exports `useWidget`, `useModuleLayout`, `useSaveModuleLayout`, the `api` / `rawFetch` HTTP helpers, the `subscribeBus` / `subscribeJob` WS helpers, and `cn` / `formatDuration` / `formatNumber` so module authors import from one place rather than reaching into host `@/` aliases directly.

### 7.8 Persistent chrome (sidebar + topbar)

- **Sidebar** (collapsible, persisted): nav links – *Processes* (default), *Settings*. Footer: theme toggle, density toggle, version label.
- **Topbar:** breadcrumb on the left, global cmd-K (`Command`) trigger and search in the centre, in-progress job indicator on the right (clicking opens the Jobs drawer – see §7.9.3).
- **Bottom-left job dock** is global – present on every page.

### 7.9 Jobs – toasts, dock & drawer

Three coordinated surfaces, all driven by the same `useJobs()` hook (subscribes to `WS /events` filtered to `job.*` and to per-job `WS /jobs/{job_id}/stream` for live progress):

#### 7.9.1 Toast notifications (`Sonner`)

A toast fires on every `job.*` lifecycle event:

| Event | Toast variant | Content |
|---|---|---|
| `job.queued` | `info` | *"Import queued – Order-to-Cash 2024"* with action *View* (opens the drawer scrolled to that job). |
| `job.started` | neutral | *"Importing Order-to-Cash 2024…"* with inline `Progress` bar inside the toast (Sonner supports custom JSX). Auto-dismisses on completion. |
| `job.completed` | `success` | *"Imported Order-to-Cash 2024 · 184,620 events · 8 s"* with action *Open* (deep-links to `/processes/{logId}`). |
| `job.failed` | `error` | *"Import failed – Order-to-Cash 2024"* with actions *Retry* and *Details* (opens drawer + expands the failed job). Persists until dismissed. |
| `job.cancelled` | `warning` | *"Import cancelled – Order-to-Cash 2024"*. |

Notes:

- Toasts are **debounced**: rapid-fire `job.queued` events (e.g. bulk uploads) collapse into a single *"3 imports queued"* toast.
- Toasts respect `prefers-reduced-motion` and the user's *Notifications* preference in Settings → General (default: all on; users can mute non-error toasts).
- The Sonner `<Toaster />` mounts once in `apps/web/components/providers.tsx` with `richColors`, `closeButton`, top-right position. Variants map to CSS variables, no hardcoded colours.

#### 7.9.2 Bottom-left dock (always visible while jobs are active)

Built on shadcn `Card` + `Progress`. Two display modes:

- **Pill (collapsed, default).** Compact: spinner + active count + the foremost job's title truncated + percentage. *"⟳ 2 jobs · Order-to-Cash 2024 · 47%"*. Pulse animation while progress is moving; static when stalled.
- **Stack (expanded on hover).** Up to 3 most recent active jobs as small rows: title, mini-progress, ETA, cancel `Button` (icon-only, ghost variant).

The whole dock is **a single clickable target** – clicking anywhere (pill or stack) opens the **Jobs drawer** (§7.9.3). Right-click opens a context menu: *Pause queue*, *Clear finished*, *Open drawer*.

The dock auto-hides when there are no active or recently finished jobs (last 30 s); it does not flash on/off – it slides in/out with a 150 ms ease.

#### 7.9.3 Jobs drawer (`Sheet`, side="left")

Opens from the dock click or via a global cmd-K command (*Show jobs*). Full-height side sheet, ~420 px wide.

**Header.** Title *Jobs*, count badges (*Running 2 · Queued 5 · Finished 18*), `DropdownMenu` for *Pause queue* / *Resume queue* / *Cancel all queued* / *Clear finished* / *Open job log*.

**Body.** A virtualised list (TanStack Virtual) grouped by status, default order: **Running → Queued → Finished (most recent first)**. `Tabs` at the top let the user filter to one status. A search field filters by title.

Each job row renders a shadcn `Card` (compact) with:

- **Title.** The job's display title (e.g. *"Import – Order-to-Cash 2024"*; modules can supply their own job titles via the SDK, e.g. *"Discovery – Inductive miner on Order-to-Cash 2024"*).
- **Subtitle.** `{type} · {module_id?} · {target}` – short technical breadcrumb.
- **Status badge.** `queued` / `running` / `paused` / `completed` / `failed` / `cancelled`, colour-coded against CSS variables.
- **Progress.** shadcn `Progress` bar with `current / total` (e.g. *"94,210 / 184,620 events"*) and percentage.
- **Stage line.** Current `stage` from the WS payload (`parsing`, `normalizing`, `indexing`, etc.) and the latest `message`.
- **Timing.** Started `Xs ago`, **estimated time remaining** (see §7.9.4), throughput (e.g. *"23,400 events/s"*).
- **Actions.** `Cancel` (running/queued), `Retry` (failed), `Open` (deep-link to the resulting log/page on completed), `Copy job ID`, kebab `DropdownMenu` for *View payload*, *View logs*, *Re-prioritise* (move queued job up/down).
- **Expandable details** (`Collapsible`): full payload (truncated JSON viewer), recent log lines (last 50), error stack trace if failed.

**Empty states.**

- No jobs ever: *"No jobs yet. Import an event log to get started."* + primary CTA.
- No active jobs but finished present: shows *Finished* group only, with a *Clear* button.

**Keyboard.** `j` then `j` opens the drawer; `Esc` closes; `↑/↓` navigate; `Enter` opens row details.

#### 7.9.4 Time estimation

Each running job streams `{ current, total, started_at }`. The frontend computes ETA with a **moving-average rate** over the last *N* progress samples (default 20) – handles uneven progress tick rates and slows-downs without jumping.

```
elapsed   = now - started_at
rate      = ema(samples)         # events / second, exponentially-weighted, α = 0.3
remaining = (total - current) / rate
eta_text  = formatDuration(remaining)   // "~12 s", "~3 min", "~1 h 4 min"
```

If `total` is unknown (e.g. streaming source where line count isn't known yet) the row shows an indeterminate `Progress` and *Estimating…* in place of ETA. Once `total` is known mid-job, the bar transitions smoothly to determinate.

Backend hint: progress payloads include an optional `eta_seconds` so a module can override the heuristic when it has better information (e.g. a known per-event cost). Frontend prefers backend ETA when present.

#### 7.9.5 Backend support for the drawer

To power the drawer, the API exposes:

| Path | Purpose |
|---|---|
| `GET /jobs?status=&type=&since=&limit=` | Paginated list with filters; used to populate the drawer on open and after reconnects. |
| `WS /events` | Topic-filtered stream (`job.*`) – drives toasts and incremental drawer updates without per-job sockets. |
| `WS /jobs/{job_id}/stream` | High-frequency progress for the focused job (used by the toast inline bar and the drawer row that's currently visible / expanded). |
| `POST /jobs/{job_id}/cancel` | Cooperative cancellation. |
| `POST /jobs/{job_id}/retry` | Re-enqueues a failed job with the same payload. |
| `POST /jobs/queue/pause` `POST /jobs/queue/resume` | Whole-queue control (does not interrupt running jobs). |

The Job model in SQLite (§8) gains: `title`, `subtitle`, `module_id?`, `eta_seconds?`, `rate?`, `priority`, `parent_job_id?` (for module-spawned subjobs).

---

## 8. Job & Progress Architecture

### 8.1 Job model & status

- **Job model** in SQLite: `id, type, title, subtitle, module_id?, payload_json, status, progress_current, progress_total, stage, message, rate?, eta_seconds?, priority, parent_job_id?, created_at, started_at?, finished_at?`. Status values: `queued | running | paused | completed | failed | cancelled`. The `title` / `subtitle` are user-facing strings that the producing module supplies (defaults are computed from `type` + `payload`).

### 8.2 Job types

| Type | When | Who creates | Module-scoped | Purpose |
|---|---|---|---|---|
| `import` | User uploads an event log via `/processes/import` | Platform (core) | No | Parse and normalise XES / CSV / XML / OCEL into Parquet. Multi-stage: `parsing` → `normalizing` → `indexing`. |
| `module` | Module defines a `@job`-decorated route or event handler | Module (SDK `@job`) | Yes (module_id required) | Long-running module computation (discovery on large logs, expensive aggregations, etc.). Title supplied by the module's `@job(title=...)` decorator. |

**Notes:**
- The `module_id` field is `NULL` for `import` jobs, required for `module` jobs.
- When a module declares `@job(progress=True)` on a handler, the SDK automatically inserts a Job row, assigns it a UUID v7, and returns `{ job_id }` to the client (non-blocking). For handlers without `@job`, the request blocks and returns the result directly.
- Module jobs inherit the same progress streaming, cancellation, and drawer integration as import jobs. The frontend treats them uniformly.

### 8.3 Job lifecycle & worker pool

- **Producer.** REST endpoints (import, module `@route` with `@job`) insert a Job row, push a marker onto an in-process `asyncio.Queue`.
- **Worker pool.** In-process asyncio tasks (configurable `WORKER_CONCURRENCY`, default 2). Progress is persisted to SQLite every N events (default 1000) and broadcast over the WebSocket fan-out.
- **Stream.** WebSocket subscribes to a per-job `asyncio.Event`; falls back to SQLite polling if the connection drops.
- **No Redis / Celery / RQ / Dramatiq.** Keeps the stack to two services and a single Python process per scale unit.
- For heavy CPU work (e.g. inductive mining on a million-event log), `JobRuntime` exposes a lazily-initialised `ProcessPoolExecutor` sized to `worker_concurrency`. Module authors offload via `await ctx.run_in_process(fn, *args, **kwargs)` ([context.py](packages/module-sdk-py/src/mate/sdk/context.py)) – standard pickling rules apply (importable callable, picklable args/return), so authors typically declare a module-level pure function for the hot loop and call it from an otherwise-async handler. Still no broker, still single binary.

---

## 9. Async / Sync Decisions

| Operation | Mode | Why |
|---|---|---|
| HTTP endpoints | async | I/O-bound (DB, file) |
| Module `@route` handlers (sync `def`) | FastAPI's built-in `run_in_threadpool` | `@route` registers a regular FastAPI route – sync→threadpool is provided by Starlette, no SDK code |
| Module `@on_event` and `@job` handlers (sync `def`) | `asyncio.to_thread` via SDK auto-wrap (§5.5) | These run outside FastAPI's request lifecycle (event bus, job queue) – SDK provides the equivalent guarantee |
| Event-log parsing (XES → Parquet) | async, chunked, with line-progress; CPU-heavy bits via `to_thread` | progress reporting + non-blocking event loop |
| Module compute (pm4py) | `asyncio.to_thread` (or `ProcessPoolExecutor` for the heaviest) | pm4py is sync, CPU-bound, sometimes single-threaded GIL-bound |
| DuckDB queries | sync API wrapped in `to_thread`, **per-thread connection pool** | DuckDB is single-threaded per connection |
| Bus / capability calls | async | uniform |
| Frontend ↔ backend | async fetch + WS | progressive UX |

**Connection pooling.** One DuckDB connection per worker thread, recycled (`contextvars`-based pool). SQLite uses `aiosqlite` with WAL mode; FastAPI dependencies hand out scoped sessions.

---

## 10. Local Hosting (Docker)

```yaml
# docker-compose.yml
services:
  api:
    build: ./apps/api
    volumes:
      - ./data:/app/data
      - ./modules:/app/modules           # hot-reload in dev
    environment:
      DATA_DIR: /app/data
      DATABASE_URL: sqlite+aiosqlite:////app/data/metadata.db
    ports: ["8000:8000"]

  web:
    build: ./apps/web
    volumes:
      - ./modules:/app/modules           # same bind as api – bundler writes .dist/ here at container start
    environment:
      INTERNAL_API_URL: http://api:8000
      NEXT_PUBLIC_API_URL: http://localhost:8000
    ports: ["3000:3000"]
    depends_on: [api]
```

Two services. No Redis, no DB container, no Nginx in dev. The `data/` bind-mount is the user's persistent storage – back it up by copying the folder. Both containers share the `modules/` bind-mount: the API serves bundled assets under `/api/v1/modules/{id}/assets/...`, and the web container's entrypoint runs the module bundler (esbuild via [bundle-modules.mjs](apps/web/scripts/bundle-modules.mjs)) before handing off to Next, so every installed module's `.dist/` is fresh by the time the first request hits.

---

## 11. Built-in modules

Several modules ship bundled in `modules/` and load at platform startup. The **foundation** modules below double as the **acceptance test** for the module SDK – all are authored through the same public SDK that any third-party module would use, with no platform-side privileges.

| Module id | Category | Scope |
|---|---|---|
| `discovery` | foundation | pm4py inductive / heuristics / alpha; outputs Petri net / DFG / process tree / prefix tree |
| `performance` | foundation | Lead / sojourn / wait time, throughput, bottleneck detection |
| `ocel_discovery` | foundation | Object-centric (OCEL) discovery over the events / objects / relations / o2o tables |

The platform also bundles several **advanced** modules in `modules/` – `complexity`, `complexity_over_time`, `cv4cdd` (CV-based concept-drift detection), `concept_drift_explainer`, `log_evolution`, and `process_comparison` – each loaded the same way and owned per-user like any install. Further third-party modules are added via Settings → Modules → Import (§7.6.2): zip / tar.gz upload, git URL, or the `mate.modules` entry point.

---

## 12. Adding a New Module

1. Create a folder under `modules/` – the folder name is arbitrary (convention: same as the module's `id`, but not enforced).
2. Create `modules/<folder>/manifest.yaml` – id, version, category, requirements, provides, consumes, **dependencies**, frontend.
3. Create `modules/<folder>/module.py` – implement `Module` class using the SDK. Handlers may be written as `async def` or plain `def`: `@route` handlers ride FastAPI's built-in threadpool, and `@on_event` / `@job` handlers are auto-wrapped by the SDK (§5.5). For operations expected to run more than a few seconds, add `@job(progress=True)` (§5.6) to get a persisted job, progress streaming and a non-blocking `job_id` response.
4. *(optional)* Create `modules/<folder>/panel/` and widget files for the frontend page.
5. *(optional)* Declare event/payload types in `events.py`.
6. Restart (or rely on hot reload in dev). On first boot the platform creates `modules/<folder>/.venv/` and `modules/<folder>/.dist/` automatically – nothing to install by hand, nothing leaks into the platform.

**Removing a module:** delete the folder. All its dependencies, bundles, caches and lockfiles go with it. The platform's own dependencies are unaffected.

The platform discovers the module automatically. **No edits to `main.py`, no hardcoded frontend registry.**

---

## 13. Default User Flow

1. **Land on `/processes`.** First-time users see the empty state with a primary *Import event log* CTA.
2. **Import an event log** via *Import event log* → `/processes/import` → drop file → (CSV: column-mapping wizard) → submit. The platform allocates a `logId` (UUID v7), creates a **Job**, and returns to `/processes`. The new entry appears in the list, greyed and non-clickable, with an inline progress bar.
3. **Bottom-left dock** subscribes to `WS /jobs/{job_id}/stream` and shows `currentLine / totalLines` for the running import.
4. On completion: toast, row un-greys, becomes clickable.
5. **Clicking a process** opens `/processes/{logId}` – a sectioned grid of module cards grouped by category (`Foundation → Attribute → External Input → Advanced Process Analytics → Other`). On a fresh v1 install no modules are present, so each section renders its *empty state* with an inline CTA pointing to `/settings/modules/import`. Once modules are installed: available modules are clickable; unavailable modules are greyed with an inline tooltip reason; modules with missing optional deps show an amber "Limited" badge.
6. **Clicking a module card** opens `/processes/{logId}/modules/{moduleId}` – the module's deep page for that specific log, customisable per user.
7. **Settings (`/settings`)** is reachable from the sidebar at any time. Tabs: *General* (theme, density, locale, jobs, telemetry), *Modules* (the entry point for installing the first module – empty on a fresh install – with enable / disable / configure / uninstall, plus *Import module*), *About*.

---

## 14. Migration Notes (vs. `oldinstructions.md`)

The previous design document described a Postgres + JSONB graph with hardcoded built-in modules registered in `main.py`. The differences worth flagging:

- **Postgres → SQLite + DuckDB + Parquet.** Dropped the second container; gained columnar analytics.
- **Built-in modules in `main.py` → manifest-discovered modules.** Removed the registration boilerplate; modules are now extension-point packages, and the bundled foundation modules (Discovery, Performance, Object-Centric Discovery) are authored through the same public SDK any third-party module would use (see §11).
- **Hardcoded frontend module registry → manifest-driven dynamic imports.** Frontend no longer needs edits per module.
- **Event-log workspace + per-process detail.** The old `/discover` workspace and any analytics shipped with the platform are not carried over – analytics is the responsibility of modules that live alongside the platform, not inside it.
- **Job + progress system.** Was implicit; now first-class with WebSocket streaming and a bottom-left progress dock.
- **Module-to-module communication.** Was absent; now a typed event bus + capability registry.
- **OCEL.** Now supported end-to-end: `.jsonocel` / `.xmlocel` / OCEL 2.0 `.sqlite` imports parse into object-centric Parquet tables under `ocel/` (events, objects, relations, o2o), surfaced by the Object-Centric Discovery foundation module – kept separate from the case-centric XES/CSV pipeline.

---

## 15. Out of Scope

- **True streaming / real-time event ingestion (CDC).** Ingestion is batch-import based. *Watched Folders* add a storage-backed source that is polled and auto-imports new files, but there is no live event stream / change-data-capture path that ingests events as they happen.
- **A couple of narrow module-runtime limits**, documented where they apply rather than repeated here: npm-only module installs (§7.6.2 – no Python entry point to bind to) and DataFrame transfer / synchronous `registry.has()` under `subprocess` isolation.
