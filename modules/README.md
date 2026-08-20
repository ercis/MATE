# Authoring a Mate Module

This is the practical guide for building a module. For the platform-level rationale (why Parquet, why DuckDB, why per-module venvs) read [`/INSTRUCTIONS.md` §5](../INSTRUCTIONS.md). This document is the contract: what to put on disk, what the platform calls, what your module is allowed to call back.

---

## 1. What a module is

A module is a self-contained folder under `modules/<folder>/`. The platform discovers it on startup, installs its declared dependencies into the folder itself, registers its routes / event handlers / jobs, and renders its frontend panel on the per-process page (`/processes/{logId}/modules/{moduleId}`).

A module is **not**:

- A patch to the platform – you never edit `apps/api` or `apps/web` to ship a module.
- A long-running service – the platform owns the FastAPI process, the asyncio event loop, the job queue, and the WebSocket fan-out. Your code runs inside them.
- A privileged citizen – the same SDK is used by core, third-party, and user modules. There are no internal hooks reserved for first-party code.

To remove a module: delete the folder. Everything it added (its venv, its bundled JS, its lockfile) lives inside the folder.

---

## 2. Folder layout

```
modules/<folder>/
├── manifest.yaml           # required – registration, requirements, deps, frontend
├── module.py               # required – entry point: subclass of Module
├── pyproject.toml          # optional – synthesised from manifest if absent
├── package.json            # optional – synthesised from manifest if absent
├── events.py               # recommended – Pydantic schemas for emitted events
├── tests/                  # pytest tests for your handlers
├── panel/
│   └── index.tsx           # frontend module page entry (see manifest.frontend.panel)
├── widgets/
│   └── *.tsx               # reusable widgets advertised in manifest.frontend.widgets
├── .venv/                  # auto-created; gitignored
├── .dist/                  # auto-bundled JS; gitignored
├── node_modules/           # gitignored
└── uv.lock                 # committed – pins your Python deps
```

`<folder>` is arbitrary on disk; the manifest's `id` is authoritative.

---

## 3. `manifest.yaml`

Every field is validated by the SDK ([`packages/module-sdk-py/src/mate/sdk/manifest.py`](../packages/module-sdk-py/src/mate/sdk/manifest.py)). Manifest errors fail loud at startup.

```yaml
id: my_module                       # lowercase snake_case, globally unique
name: My Module                     # human-readable
version: 0.1.0
category: foundation                # foundation | attribute | external_input | advanced | other
description: One-line summary shown on the module card.
author: You
license: MIT
keywords: [my topic, synonym, domain term]   # optional – helps MATE AI route chat
                                             # messages to this module. Omit and the
                                             # platform derives them from name/description.

requirements:
  event_log:                        # checked against the log's detected schema
    log_model: case_centric         # case_centric (default) | object_centric – see below
    required_columns: [case_id, activity, timestamp]
    optional_columns: [resource, end_timestamp]
    min_events: 100
    min_cases: 5
  modules: []                       # hard deps – must be loaded
  optional_modules:                 # soft deps – used if present
    - id: discovery
      reason: Activity labels are taken from discovery if available.

provides:                           # capabilities you publish on the registry / bus
  - my_module.compute_something

consumes:                           # bus topics / capabilities you depend on
  - log.imported

dependencies:
  python:
    requires-python: ">=3.12"       # validation gate for in_process (see §8); selector for subprocess
    packages:                       # private to this module – installed into .venv
      - "scikit-learn>=1.5"
    inherit:                        # reuse the platform's already-installed copy
      - pm4py
      - pandas
      - duckdb
    isolation: in_process           # in_process (default, ABI-locked to platform Python) | subprocess
  npm:
    - "d3-sankey@^0.12"

frontend:
  panel: ./panel/index.tsx
  widgets:
    - id: kpi-card
      entry: ./widgets/KpiCard.tsx
  page_layout:
    - section: KPIs
      widgets: [kpi-card]

permissions:
  - read:event_log
  - write:module_results
```

Rules the manifest validator enforces:

- `id` is lowercase snake_case and globally unique across all modules.
- A package cannot appear in both `dependencies.python.packages` and `dependencies.python.inherit` – pick one.
- Hard-dep cycles in `requirements.modules` abort startup.
- Two modules declaring the same `id` is a startup error.

`inherit` exists so process-mining modules don't reinstall pandas/numpy/pm4py per module – those weigh hundreds of MB. Anything not inherited is fully isolated to your `.venv`.

### `log_model` – case-centric vs object-centric (declare it!)

Every module declares which **log model** it operates on via `requirements.event_log.log_model`:

| Value | Log shape | Reads from |
|---|---|---|
| `case_centric` (default) | Classic event log – one `case_id` per trace, flat `activity`/`timestamp` events. | `ctx.event_log` |
| `object_centric` | OCEL – events relate to many objects of different types; no single case notion. | `ctx.object_log` |

This is the **single isolation switch** between the two worlds. The platform makes a module available *only* on logs whose model matches: a `case_centric` module never appears for an OCEL log, and an `object_centric` module never appears for a case-centric log ([`availability.py`](../apps/api/src/mate/api/modules/availability.py) checks this first and short-circuits, so the column/count checks below only run when the model already matches). Because of that gate, the loader binds exactly one of `ctx.event_log` / `ctx.object_log` – reach for the one your declared model implies.

The field **defaults to `case_centric`**, so a classic process-mining module works without declaring it – but declare it explicitly anyway (every bundled module does) so the model a module targets is visible on its manifest at a glance. `required_columns`, `optional_columns`, `min_events`, and `min_cases` apply to the case-centric event table; for `object_centric` modules gate on `min_events` (and the OCEL counts surfaced on the log) rather than case-centric columns.

---

## 4. `module.py` – the entry point

```python
from mate.sdk import Module, ModuleContext, on_event, route, job


class MyModule(Module):
    id = "my_module"                           # must match manifest.id

    @route.get("/kpis")
    async def get_kpis(self, ctx: ModuleContext) -> dict:
        cached = await ctx.cache.get("kpis")
        if cached is not None:
            return cached
        kpis = await self._compute(ctx)
        await ctx.cache.set("kpis", kpis)
        return kpis

    @on_event("log.imported")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        await self._compute(ctx)

    @route.post("/recompute")
    @job(progress=True, title="My Module – recompute")
    async def recompute(self, ctx: ModuleContext) -> dict:
        await ctx.progress.update(0.0, "Loading log")
        async with ctx.event_log as log:
            df = await log.pandas()
        await ctx.progress.update(0.5, "Computing")
        kpis = self._reduce(df)
        await ctx.cache.set("kpis", kpis)
        await ctx.progress.update(1.0, "Done")
        return kpis
```

Rules:

- One subclass of `Module` per module file. The loader instantiates it exactly once per process – never instantiate it yourself.
- The class attribute `id` must equal `manifest.yaml::id`.
- Decorators only attach metadata. There is no `register(...)` call – the manifest is the registration.
- Handlers may be `async def` or plain `def`. Sync handlers are auto-wrapped so they cannot block the event loop:
  - `@route.*` rides FastAPI's built-in `run_in_threadpool`.
  - `@on_event` and `@job` are wrapped by the SDK with `asyncio.to_thread` ([`decorators.py`](../packages/module-sdk-py/src/mate/sdk/decorators.py)).
- For anything expected to run more than a few seconds, add `@job` so the user sees a toast / dock entry / progress bar instead of a hung request.

### `@route.*`

Mounts at `/api/v1/modules/{id}/<path>`. The HTTP method comes from the decorator (`route.get`, `route.post`, `route.put`, `route.patch`, `route.delete`).

```python
@route.get("/things/{thing_id}")
async def get_thing(self, ctx: ModuleContext, thing_id: str) -> Thing: ...
```

Path parameters and request bodies use FastAPI semantics. Pydantic models for request/response are first-class.

### `@on_event(topic)`

Subscribes to a bus topic. Topics are dotted strings; wildcards are not supported on the subscriber side – subscribe to the exact topic you care about.

Built-in platform topics include `log.imported`, `log.deleted`, and `job.queued|started|progress|completed|failed|cancelled`. Module-emitted topics are namespaced by module id (`my_module.something`).

### `@job(...)`

Stack `@job` on top of a `@route` or `@on_event` handler to make it asynchronous and observable.

| Param | Default | Effect |
|---|---|---|
| `progress` | `False` | Enables `ctx.progress.update(...)` streaming. |
| `title` | derived | Toast + drawer headline. May be a `(ctx, payload) -> str` callable. |
| `subtitle` | derived | Drawer subtitle. |
| `priority` | `0` | Higher = scheduled sooner. |
| `cancellable` | `True` | Whether the *Cancel* button is enabled. |
| `result_url` | `None` | URL template for the toast's *Open* action on success. |

When `@job` wraps a route, the route returns `{ "job_id": "..." }` immediately and the work runs on the platform's queue; the frontend handles the response generically.

---

## 5. `ModuleContext` – what every handler receives

Defined in [`packages/module-sdk-py/src/mate/sdk/context.py`](../packages/module-sdk-py/src/mate/sdk/context.py). All fields are typed Protocols – depend on the Protocol, not the implementation.

```python
@dataclass
class ModuleContext:
    log_id: str                     # the log this invocation is scoped to ("" for global routes)
    module_id: str
    event_log: EventLogAccessProtocol
    bus: EventBusProtocol
    registry: ModuleRegistryProtocol
    cache: ResultCacheProtocol
    config: ModuleConfigProtocol
    progress: ProgressReporterProtocol
    logger: structlog.BoundLogger
    workdir: Path                   # scratch space, auto-cleaned on completion
```

### `ctx.event_log`

Lazy access to the log. Always use the async-context-manager form so the platform can manage file handles and DuckDB connections:

```python
async with ctx.event_log as log:
    rows = await log.duckdb_fetch(
        "SELECT activity, count(*) FROM events GROUP BY 1 ORDER BY 2 DESC"
    )
    df = await log.pandas()         # or .polars(), .pm4py()
```

Prefer DuckDB for aggregations (millions of rows in milliseconds). Use pandas/polars when you need DataFrame semantics. Use `pm4py` only when an algorithm needs the pm4py event log object – it's the heaviest.

### `ctx.open_event_log(log_id)` – reading a *second* log

`ctx.event_log` is bound to the one log the invocation is scoped to. When a module genuinely needs another log too (comparison, benchmarking), `ctx.open_event_log(other_log_id)` returns a second `EventLogAccess` with the **same** interface:

```python
async with ctx.event_log as base, await ctx.open_event_log(other_id) as other:
    base_df = await base.pandas()
    other_df = await other.pandas()
```

It is the only sanctioned cross-log accessor and it **enforces tenant isolation**: the target must be a case-centric log owned by the same user, else it raises (a log belonging to another user is reported as "not found" – never confirmed). The view honours the target log's own committed Events-tab filter, exactly like the primary one. Don't reach into `mate.api.*` internals to open a log yourself – that bypasses the ownership check.

### `ctx.cache`

Per-`(log_id, module_id)` result cache. Use it to memoise expensive computations across requests:

```python
if not await ctx.cache.exists("kpis"):
    await ctx.cache.set("kpis", await self._compute(ctx))
return await ctx.cache.get("kpis")
```

Caches are invalidated automatically when the log changes (re-import) or when the module config changes.

### `ctx.config`

User-set configuration, validated against your `config_schema`. Read with `ctx.config.value` (full dict) or `ctx.config.get(key, default)`.

### `ctx.progress`

Inside a `@job(progress=True)` handler, emit progress to the dock + drawer + WebSocket stream. Pick whichever style fits – all three render a live bar, none requires you to know the total up front:

```python
# 1. Fraction (0.0–1.0, no total) → determinate percentage bar.
await ctx.progress.update(0.42, "Computing fitness")

# 2. Absolute counts (current + total) → determinate bar *and* an ETA.
await ctx.progress.update(current=4200, total=10000, stage="replay")

# 3. Unknown scope → pass an integer running counter so the row reads
#    "{n} processed" instead of sitting on a dead "Estimating…" pulse.
await ctx.progress.update(current=processed_so_far)
```

A `float` `current` in `[0, 1]` with no `total` is read as a fraction (mapped to 0–100); an `int` `current` is always a running counter (`update(current=1)` is "1 processed", not "100%"). Progress is **optional and never enforced** — omit it and the job still runs, just with an indeterminate "Working…" bar (and a stall hint if it stays silent past ~3 min). But for long jobs you *should* **emit *something* live** – never leave the bar silent for minutes.

### Precompute ordering (running after another module)

A module precomputes on import by stacking `@on_event("log.imported")` (or `ocel.imported`) + `@job`; all such jobs for one import run **in parallel**. To run *after* another module, subscribe to its reserved `<module_id>.completed` event — the platform auto-emits it when that module's precompute job succeeds, so the producer does nothing special:

```python
@on_event("discovery.completed")   # runs only after `discovery` precompute succeeds
@job(progress=True, title="My overlay")
async def precompute(self, ctx, payload): ...
```

Declare the edge so it validates and shows up in the jobs UI: `consumes: [discovery.completed]` (+ `optional_modules: [discovery]`). The log stays in `processing` until the **whole** chain finishes; if an upstream fails the platform **skips** your job instead of stranding the log. A `@on_event` *without* `@job` (e.g. a cheap cache refresh) is fire-and-forget and never gates a log.

### `ctx.workdir`

A temporary directory unique to this invocation. Cleaned automatically on completion.

### `ctx.logger`

A `structlog.BoundLogger` already bound with `module_id` and `log_id`. Use it; `print()` is dropped.

---

## 6. Communicating with other modules

Two patterns. Both are typed.

### (a) Event bus – fire-and-forget, fan-out

```python
# emitter
await ctx.bus.emit("my_module.kpi.computed", {"log_id": ctx.log_id, "kpis": kpis})

# subscriber (in another module)
@on_event("my_module.kpi.computed")
async def react(self, ctx: ModuleContext, payload: dict) -> None: ...
```

Topics you emit must be listed in your manifest's `provides:` (or be one of the platform's built-in topics). Topics you subscribe to must be listed in your `consumes:`. The platform validates this at startup, so missing-dep bugs surface at boot – not at runtime.

Define payload shapes as Pydantic models in your `events.py` and use them on both sides. The bus rejects untyped emits.

### (b) Capability registry – typed RPC for synchronous queries

When you need a result back from another module, use the registry instead of round-tripping through the bus:

```python
if ctx.registry.has("conformance"):
    fitness = await ctx.registry.call("conformance.compute_fitness", log_id=ctx.log_id)
else:
    ctx.logger.warning("conformance not installed; skipping fitness annotation")
```

Capabilities you publish go in `provides:`. Capabilities you call must be in `consumes:` (hard) or `optional_modules:` (soft). The platform refuses to mount a module that calls undeclared capabilities.

Rule of thumb:
- Use the **bus** for "this happened, anyone interested can react." It is one-way.
- Use the **registry** for "I need a value." It is request/response.

---

## 7. Frontend

The platform loads each module's frontend bundle from `modules/<folder>/.dist/` – your panel and widgets are bundled with esbuild at platform startup, not as part of the Next.js build. You don't import anything from `apps/web`.

### Panel

`manifest.frontend.panel` is the entry rendered on `/processes/{logId}/modules/{moduleId}`. Minimum shape:

```tsx
// modules/<folder>/panel/index.tsx
import type { ModulePanelProps } from "@mate/module-sdk-ts";

export default function Panel({ logId, moduleId, config }: ModulePanelProps) {
  // render whatever you want; common building blocks (process visualiser,
  // KPI card, ECharts wrapper, time-window picker) come from the TS SDK
  return <div>...</div>;
}
```

Use the shadcn-themed building blocks in `@mate/module-sdk-ts` rather than re-implementing tables, KPI cards, charts. They consume the same CSS variables as the host app, so light/dark and density switches just work.

### Widgets

Widgets advertised in `manifest.frontend.widgets` can be embedded by other modules:

```tsx
import { useWidget } from "@mate/module-sdk-ts";

const ThroughputChart = useWidget("performance", "throughput-chart");
return <ThroughputChart logId={logId} config={{}} />;
```

`useWidget` lazy-loads, renders a `Skeleton` while loading, and a placeholder card if the source module is missing.

### Talking to your backend

Use the platform fetch helper – it injects the auth/session correctly and respects the API base URL:

```tsx
import { api } from "@mate/module-sdk-ts";
const kpis = await api.get(`/api/v1/modules/${moduleId}/kpis?log_id=${logId}`);
```

For real-time updates, subscribe to the `WS /events` stream filtered by your topic:

```tsx
import { useEvents } from "@mate/module-sdk-ts";
useEvents(["my_module.kpi.computed"], (env) => { ... });
```

---

## 8. Dependencies & isolation

### Python

The platform creates and owns `modules/<folder>/.venv`:

- On every boot, the platform hashes your `dependencies` block. If unchanged, it skips reinstalling – boots are near-instant. (For `in_process` modules the hash also folds in the platform's Python version, so a venv built under a different Python – e.g. a host-mode 3.13 venv bind-mounted into a 3.12 container – rebuilds automatically instead of crashing on import.)
- Your code resolves imports against `.venv/site-packages` first, then stdlib, then the platform's `inherit` set, then the SDK. Other modules' dependencies are not visible.

**Python version (important).** `in_process` modules (the default) are imported into the *platform's own interpreter*, so their venv is always built on **exactly that Python** – currently 3.12. Your manifest's `requires-python` is a **validation gate**, not an interpreter selector: if the platform's Python doesn't satisfy it, the module fails to load with a clear, actionable message. You therefore **never hand-pin `<3.13`** to dodge ABI mismatches – the platform pins the interpreter for you. Declare `requires-python` only when your dependencies genuinely can't run on a newer Python (e.g. a C-extension with no wheels for it); the gate then turns that into a fast, clear failure instead of a cryptic wheel-build error.

If you genuinely need a **different Python version** than the platform, set `isolation: subprocess`. The platform spawns a long-lived worker from your venv on its own interpreter (selected by `requires-python`; uv picks or auto-downloads it) and proxies handler calls over a Unix-socket JSON-RPC. The `ModuleContext` interface is unchanged – `@route`, `@job`, and `@on_event` all work, and `event_log.pandas()/polars()/pm4py()` stream the log to your process via a Parquet handoff. Caveats: each call adds 5–50 ms; `inherit` names are installed into your own venv (no shared interpreter to inherit from across a process boundary); job cancellation is best-effort; dynamic `@job(title=...)` callables fall back to a static label. Use subprocess only when you actually need a different Python or have a hard native-lib conflict (e.g. `numpy 1.x` while the platform ships `numpy 2.x`).

### npm

`pnpm install --dir modules/<folder>` runs at startup. Bundles land in `.dist/`. Same isolation story – your widgets bundle against your own `node_modules`.

### Don't touch

- `apps/api/pyproject.toml` and `apps/web/package.json` are off-limits to module authors. If a module modifies them, that's a bug. Your dependencies belong in your manifest.

### `.gitignore` per module

```
.venv/
.dist/
node_modules/
__pycache__/
```

Commit `manifest.yaml`, `module.py`, your tests, your frontend sources, and `uv.lock`.

---

## 9. Lifecycle a module goes through

1. **Discovery.** On boot the platform scans `modules/*/manifest.yaml` (one level deep) and any installed Python entry points exposing `mate.modules`.
2. **Validation.** Manifests parsed; dep graph built; cycles or missing hard deps abort startup.
3. **Materialise dependencies.** `uv sync` per module if its dep hash changed; same for the JS bundle.
4. **Topological load.** Hard-dep order. Your module's `Module` subclass is instantiated once.
5. **Mount.** Routes registered at `/api/v1/modules/{id}/*`, `@on_event` handlers subscribed, `@job` handlers registered with the queue.
6. **Per-log gating.** When a log is opened, the platform re-evaluates `requirements.event_log` against that log's model and schema. The `log_model` gate runs first – a module whose declared model doesn't match the log is **unavailable** and hidden from the grid (case-centric and object-centric modules never cross). If the model matches, the column / `min_events` / `min_cases` checks decide **available** vs **unavailable**, with a tooltip explaining what's missing.
7. **Hot reload (dev).** Watchdog on `modules/` re-loads changed files without a platform restart. Manifest dep changes trigger an in-place `uv sync` for that module only.

If a module fails to load (manifest error, install error, import error), the failure is reported on the per-module card and in `/settings/modules/{moduleId}` – the rest of the platform stays up.

---

## 10. Testing

Put pytest tests in `modules/<folder>/tests/`. The SDK ships test helpers that build a fake `ModuleContext` against a temporary log directory:

```python
# modules/my_module/tests/test_handlers.py
import pytest
from mate.sdk.testing import build_test_context, sample_log

from modules.my_module.module import MyModule


@pytest.mark.asyncio
async def test_get_kpis():
    log = sample_log(rows=1000)
    ctx = await build_test_context(log_id=log.id, module_id="my_module")
    out = await MyModule().get_kpis(ctx)
    assert "throughput" in out
```

Run them from the repo root:

```
uv run pytest modules/my_module/tests
```

The platform's CI also runs every module's tests against the platform's `inherit` set, catching version drift early.

---

## 11. Distribution

Three channels are supported by the *Settings → Modules → Import* flow:

1. **Zip / tarball** – drop `modules/<folder>/` into a `.zip` or `.tar.gz`.
2. **Git URL** – repo root must contain the module folder layout from §2.
3. **PyPI / npm** – publish a Python package exposing the `mate.modules` entry point. The platform discovers it without copying files into `modules/`.

For the first two, the platform unpacks into `modules/<id>/`, runs `uv sync`, runs the JS bundle step, and mounts. Failures roll back cleanly – a half-installed folder is deleted.

---

## 12. Author checklist

Before submitting a module:

- [ ] `manifest.yaml` validates (run `uv run python -c "from mate.sdk import Manifest; Manifest.load_yaml('modules/<folder>/manifest.yaml')"`).
- [ ] `requirements.event_log.log_model` is set to the model the module targets (`case_centric` or `object_centric`).
- [ ] `id` matches between `manifest.yaml` and `module.py`.
- [ ] Every emitted bus topic is in `provides:`; every subscribed topic is in `consumes:`.
- [ ] Every `ctx.registry.call(...)` target is in `consumes:` or `optional_modules:`.
- [ ] No imports from `apps/api/*` or `apps/web/*` – only `mate.sdk` and `@mate/module-sdk-ts`.
- [ ] Long operations use `@job(progress=True)` (progress is optional but recommended for long jobs).
- [ ] Precompute that must run after another module subscribes to its `<module_id>.completed` event (not a phantom topic nobody emits), and the job-backed `@on_event` is what gates the log — a `@on_event` without `@job` never holds `processing`.
- [ ] Sync `def` handlers are fine – don't reach for `asyncio.run` or `loop.run_until_complete`. The SDK auto-wraps.
- [ ] Tests run green against the platform's `inherit` versions.
- [ ] No platform-level files modified (`apps/api/pyproject.toml`, `apps/web/package.json`, etc.).
- [ ] `.venv/`, `.dist/`, `node_modules/` gitignored.

If all of those hold, dropping the folder into `modules/` and restarting is enough – the module is live.
