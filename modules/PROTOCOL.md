# Mate module worker protocol (v1)

This document specifies the wire protocol between the Mate platform (the
**host**) and a module's **worker process**. It is written so that a module
SDK can be implemented from scratch in any language without reading the
platform source. The reference implementations are:

- Python: `apps/api/src/mate/api/modules/subprocess_worker.py` (worker) and
  `subprocess_host.py` + `ctx_rpc.py` (host),
- JVM: `packages/module-sdk-jvm` (`mate-sdk-jvm`).

A worker-bridged module is any module the platform runs out-of-process: every
non-Python `runtime:` (jvm, ...) and Python modules with
`dependencies.python.isolation: subprocess`. The host mounts the module's
routes/jobs/event handlers from metadata the worker advertises at startup;
afterwards each handler invocation is one `call` request to the worker, and
every `ctx.*` service the handler touches is a request from the worker back
to the host over the same socket.

## 1. Versioning

The protocol version is a single integer, sent by the worker in the `ready`
message (`params.protocol`). A missing field means `1`. The host rejects a
worker whose protocol is **newer** than the newest it supports (the module
fails to mount with a clear error); older-but-supported versions are accepted.
This document specifies version **1**.

Compatibility policy: additive changes (new optional fields, new ctx methods)
do not bump the version - workers must ignore unknown fields, hosts answer
unknown methods with an error the worker surfaces to the author. The version
bumps only on breaking changes to framing or existing message shapes.

## 2. Process launch contract

The host spawns the worker with a runtime-specific argv prefix (from the
module's runtime: the venv Python + worker script, `java -Xmx1g -jar
module.jar`, ...) and appends **two positional arguments**:

```
<prefix...> <socket_path> <module_folder>
```

The worker MUST read them as the **last two** program arguments:

- `socket_path` - filesystem path of a Unix stream socket the host is
  listening on. The worker connects to it (`AF_UNIX`, `SOCK_STREAM`).
- `module_folder` - absolute path of the module's folder (manifest, code,
  data files). For the JVM runtime the process `cwd` is also the module
  folder, but workers MUST use this argument rather than rely on `cwd`.

Environment: the worker inherits the host's environment plus any
runtime-specific additions (`PYTHONUNBUFFERED=1` for Python). Workers SHOULD
run with unbuffered/line-buffered stdout+stderr - both pipes are captured by
the host, logged, and mirrored into the per-job log ring, so `stderr` is the
right place for crash tracebacks.

After connecting, the worker MUST send `ready` (§4) within **30 seconds** of
being spawned, or the host kills it and the module fails to mount.

### Parent-death guard (required)

A worker MUST NOT outlive the host process. The host puts each worker in its
own session/process group and SIGKILLs the group on shutdown and job
hard-cancel, but that cannot cover a hard host death (SIGKILL, OOM). Workers
therefore MUST implement their own guard:

- poll the parent process at least every second (Linux: additionally
  `prctl(PR_SET_PDEATHSIG, SIGKILL)`); when the parent is gone (or the ppid
  reparented), kill the worker's own process group and exit with status 137;
- treat socket EOF as a shutdown signal: when the connection closes, exit
  promptly.

Workers that fork helpers MUST keep them in the worker's process group (or
reap them on exit) - the host's hard-cancel is `SIGKILL` to the group.

## 3. Transport, framing, message model

**Framing:** newline-delimited JSON. One message = one UTF-8 `JSON` object
serialized on a single line, terminated by `\n` (0x0A). JSON string escaping
guarantees no literal newline inside a frame. Maximum line length is
**256 MiB** in both directions; a larger frame kills the connection. Senders
MUST serialize whole frames - two messages' bytes must never interleave
(single writer, or a write lock).

**Messages** (no `jsonrpc` field - this is not JSON-RPC 2.0; no batching):

```jsonc
{"id": 7, "method": "ctx.progress.update", "params": { ... }}   // request
{"id": 7, "result": <any JSON>}                                  // success
{"id": 7, "error": {"message": "...", "traceback": "..."?}}      // failure
{"id": null, "method": "ready", "params": { ... }}               // notification
```

- `id` is a positive integer, monotonically increasing **per sender**. The
  two directions keep independent id spaces (host request `id: 3` and worker
  request `id: 3` are unrelated). A response echoes the request's `id`.
- The connection is **fully bidirectional and concurrent**: the host may have
  several `call`s in flight while the worker has several `ctx.*` requests in
  flight. Each side MUST dispatch inbound requests without blocking its read
  loop (the reference implementations run each request on its own task or
  thread) and MUST match responses to pending requests by id. Responses with
  unknown ids are dropped silently.
- A **notification** is a request with `"id": null`. The receiver MAY reply
  (the reference host replies `{"id": null, "result": true}`); the sender
  MUST ignore any response whose `id` is `null`.
- Unknown `method` → respond `{"id": ..., "error": {"message": "unknown method '<name>'"}}`.

**Error shape:** `message` (required, human-readable, conventionally
`"{ExceptionType}: {text}"`) and optional `traceback` (opaque diagnostic
string; the host logs it but does not parse it). Cancellation rides on this
shape - see §8.

## 4. Handshake: `ready`

After connecting, the worker introspects/collects its registered handlers and
sends one notification:

```jsonc
{"id": null, "method": "ready", "params": {
  "protocol": 1,
  "handlers": [
    {
      "attr": "discover",                       // unique handler name (dispatch key)
      "route": {"method": "GET", "path": "/model", "name": null},          // optional
      "on_event": {"topic": "log.imported"},                                // optional
      "job": {                                                              // optional
        "progress": true,        // handler reports progress ticks
        "priority": 0,
        "cancellable": true,
        "result_url": null,      // route path a finished job's UI links to
        "title": "Alpha Miner",  // static string or null
        "subtitle": null
      }
    }
  ],
  "guidance": {"system_prompt": "...", "user_prefix": "..."} // or null
}}
```

- `attr` is the name the host sends back in `call.handler`. One entry may
  combine `route`+`job` (a route that enqueues a job) or `on_event`+`job`
  (a precompute job per event); at least one of the three keys must be set.
- `route.method` ∈ `GET|POST|PUT|PATCH|DELETE`; `route.path` starts with `/`
  and is mounted at `/api/v1/modules/{module_id}{path}`.
- `job.title`/`subtitle` are static strings or `null` (`null` = the platform
  falls back to a generic label; dynamic per-payload labels are a
  Python-in-process-only feature and cannot cross the socket).
- `guidance`, when non-null, declares the module implements the duck-typed AI
  guidance hooks: the host will then invoke `call` with
  `handler: "guidance_payload"` (one positional arg-less call, `args: []`) and
  use the two strings as the module's AI system prompt / user prefix.
- `protocol`: see §1.

The host builds a stub object from `handlers` and mounts routes/jobs/event
subscriptions exactly as it would for an in-process module.

## 5. Host → worker methods

### `call`

Invoke one handler.

```jsonc
{"id": 4, "method": "call", "params": {
  "handler": "discover",          // `attr` from the ready list
  "ctx_token": "9f0c...e1",       // opaque; echo it on every ctx.* request
  "ctx": { ... },                 // context snapshot, §6
  "args": [ ... ],                // positional JSON args
  "kwargs": { ... }               // keyword JSON args
}}
```

- Event handlers receive the bus event's payload object as `args[0]`.
- Route handlers receive the platform-forwarded query values in `kwargs`
  (bridged routes take no typed request bodies - see §9 limitations).
- Job handlers stacked on a route receive the enqueued payload in `kwargs`.
- The worker runs the handler and responds with `{"id": 4, "result": <JSON>}`.
  The result MUST be JSON-serializable; for routes it becomes the HTTP
  response body. Errors/cancellation per §8.
- Calls may overlap; `ctx_token` scopes each call's context. The token is
  valid only for the duration of the call.

### `ping`

`{"id": N, "method": "ping", "params": {}}` → `{"id": N, "result": true}`.
Liveness probe; workers MUST answer it. (The host's primary liveness signal
remains process exit / socket EOF.)

### `shutdown`

`{"id": N, "method": "shutdown", "params": {}}` → `{"id": N, "result": true}`.
Graceful stop: the worker should stop accepting work and exit soon after
replying. The host waits ~2 s for the reply, then SIGTERMs the process, then
after 5 more seconds SIGKILLs the process group.

## 6. The context snapshot (`call.params.ctx`)

Immutable per-call facts the worker answers locally, without a round-trip:

| key | type | meaning |
|---|---|---|
| `log_id` | string | Event log the call is scoped to. `""` for log-independent routes. |
| `module_id` | string | The module's own id. |
| `workdir` | string | Absolute path of a per-call scratch directory on a filesystem **shared with the host**. Deleted by the host when the call ends. |
| `config` | object | The module's per-user config values (validated against the manifest's `config_schema`). |
| `capabilities` | string[] | Module ids + capability names visible to the calling user - answers `registry.has(...)` locally. |
| `events_path` | string? | Absolute path of the log's raw `events.parquet`. |
| `cases_path` | string? | Absolute path of `cases.parquet` (when the log has one). |
| `active_filter` | array? | The committed event-filter definition (JSON), `null`/absent = no filter. NOTE: `events_path` is the **unfiltered** file - use `ctx.event_log.materialize` or the `events` SQL view for filter-aware data. |
| `cache_dir` | string? | Absolute path of the module's result-cache directory for this log (shared filesystem). |

**Data-wall rule:** the optional keys may be **absent** (AI/restricted
contexts deliberately omit the raw-data paths). An SDK MUST surface "absent"
as an explicit error when the author touches that surface (e.g. a
`DataWallException`), and MUST NOT guess paths or fall back to defaults.

## 7. Worker → host: the `ctx.*` methods

Every request carries the `ctx_token` from the `call` being served. All
eleven methods below exist in protocol v1. Any of them can fail with the
cancel sentinel (§8) - that is the cooperative cancellation poll point.

| method | params (besides `ctx_token`) | result |
|---|---|---|
| `ctx.event_log.duckdb_fetch` | `sql`: string, `params`: array or null | rows as `[[col, ...], ...]` (JSON values) |
| `ctx.event_log.materialize` | - | string: path of a Parquet file with the **filter-applied** event log, written under `workdir` |
| `ctx.bus.emit` | `topic`: string, `payload`: object | null |
| `ctx.cache.get` | `key`: string | cache envelope (below) |
| `ctx.cache.set` | `key`: string, `value`: cache envelope | null |
| `ctx.cache.exists` | `key`: string | bool |
| `ctx.cache.delete` | `key`: string | null |
| `ctx.registry.call` | `capability`: string, `kwargs`: object | any JSON |
| `ctx.progress.update` | `current`: number, `message`: string?, `total`: number?, `stage`: string? | null |
| `ctx.logger.log` | `level`: `"debug"\|"info"\|"warning"\|"error"`, `payload`: object with an `event` string + arbitrary JSON fields | null |
| `ctx.cancel.check` | - | `false` (or the cancel-sentinel error, §8) |

Notes:

- **`duckdb_fetch`** executes on the host against the real log. Available
  views: `events` (the committed/ephemeral filter already applied - use this),
  `events_src` (raw), and `cases` (when a cases file exists). Canonical
  columns: `case_id`, `activity`, `timestamp` (+ optional `end_timestamp`,
  `resource`, `cost`, `role`, `lifecycle`, and the log's own extra columns).
  Column names for arbitrary queries can be fetched with DuckDB's
  `DESCRIBE SELECT ...`. Result rows must fit the 256 MiB frame - aggregate
  in SQL, or use `materialize` for bulk data.
- **`materialize`** is the bulk-data path: the host writes the filtered log
  as Parquet on the shared filesystem and returns the path; the worker reads
  it with any Arrow/Parquet/DuckDB library. The file lives in `workdir` and
  is cleaned up with it.
- **`ctx.logger.log`** is fire-and-forget in spirit: the host replies, but
  workers need not await the reply before continuing. A log frame that
  reaches the host after its `call` already completed is silently dropped -
  flush (await/order) your last log lines before returning when they matter.
- **`ctx.progress.update`**: `current` alone may be a fraction in `[0,1]`, a
  running counter, or paired with `total` for absolute progress.
- **`ctx.bus.emit`**: topics must be declared in the manifest's `provides:`.
  The platform stamps tenant fields (`user_id`, `log_id`) server-side -
  workers cannot and must not set them.
- **`ctx.registry.call`** is specified for cross-module capability RPC but
  currently dormant platform-side (no capabilities are bound); expect
  `LookupError`-style errors until that lands.

### Cache envelopes

`ctx.cache.get` results and `ctx.cache.set` values are wrapped:

```jsonc
{"kind": "json", "value": <any JSON>}          // portable
{"kind": "pickle", "path": "/...(.pkl)"}       // Python-only
```

`kind: "json"` is the only portable envelope. The `pickle` kind exists for
Python workers caching DataFrames/bytes; a non-Python SDK MUST only emit
`json` envelopes and MUST raise a typed error when a `get` returns `pickle`
(the value was written by Python code and is not readable elsewhere). A
`get` of a missing key returns `{"kind": "json", "value": null}`. For large
binary artefacts, write files into `cache_dir` directly and cache JSON
metadata pointing at them.

## 8. Errors and cancellation

**Handler errors:** if a handler throws, the worker responds with the error
shape - `message` SHOULD be `"{ExceptionType}: {text}"`, `traceback` SHOULD
carry the native stack trace (host logs it; only `message` reaches the API
caller). A failed job records `failed` with that message.

**Cancellation** is two-phase, driven by the host; the sentinel string is

```
__ff_job_cancelled__
```

1. **Soft (cooperative):** when a job is cancelled, the host flags it; from
   then on **every `ctx.*` request for that call fails** with an error whose
   `message` contains the sentinel. The SDK MUST convert that error into its
   cancellation signal (Python: `Cancelled(BaseException)`; JVM:
   `CancelledException extends Error`) - a type that survives the author's
   broad `catch (Exception)` - so the handler unwinds. When the handler
   unwinds with that signal, the worker MUST respond to the original `call`
   with `{"error": {"message": "__ff_job_cancelled__"}}` so the host records
   the job `cancelled` (not `failed`). `ctx.cancel.check` exists as an
   explicit poll for compute loops that don't otherwise touch ctx.
2. **Hard (kill):** if the call hasn't ended after a grace window
   (`subprocess_cancel_grace_seconds`, default 3 s), the host SIGKILLs the
   worker's **process group** and spawns a fresh worker. Any other call in
   flight on that worker dies too (the host fails them with a retryable
   error). Long CPU/native loops SHOULD therefore poll (`cancel.check` or any
   ctx call) at least every couple of seconds when cancellable.

A wall-clock backstop (`job_execution_timeout_seconds`, default 1800 s) runs
any still-running job through the same two phases and records a timeout.

**Worker crash / respawn:** if the worker process exits or the socket hits
EOF outside a deliberate shutdown, the host fails all in-flight calls and
**auto-respawns** the worker with exponential backoff (capped; the attempt
counter resets after the worker has been up for a stable period). If the
worker crash-loops past the attempt cap, the bridge enters a failed state and
every call errors immediately until the module is fixed/reloaded. Workers
should therefore be safe to restart at any time and treat every `call` as
potentially the first after a restart (no cross-call in-memory state).

## 9. Bridge-mounted module limitations (v1)

Relative to Python in-process modules:

- The proxied context has **no `user_id`**, no `open_event_log` (second-log
  access), and no OCEL/object-centric surface. Bridged modules operate on the
  one case-centric log the call is scoped to.
- Routes take **no typed request bodies** and no declared query parameters -
  the platform forwards only generic values (see §5 `call`). The module's
  per-user **config is the input channel**: the panel `PUT`s config via the
  platform API, then the handler reads it from `ctx.config`.
- Dynamic (callable) job titles/subtitles don't cross the socket - use static
  strings.
- Every `ctx.*` touch is an RPC: budget ~1-50 ms per call and batch
  accordingly (one `duckdb_fetch` with SQL aggregation beats a thousand).

## 10. Conformance checklist

An SDK/worker implementation is conformant when it:

1. connects to the socket from the launch argv and sends `ready` (with
   `protocol`, complete `handlers`, `guidance` when applicable) within 30 s;
2. answers `ping`, `shutdown`, and unknown methods per §5/§3;
3. dispatches overlapping `call`s concurrently and serializes frame writes;
4. keeps independent id spaces and drops unknown/`null`-id responses;
5. round-trips every `ctx.*` method in §7 with the exact param/result shapes;
6. surfaces absent snapshot keys as explicit data-wall errors;
7. emits only `json` cache envelopes and raises a typed error on `pickle`;
8. converts sentinel-bearing errors into a catch-`Exception`-proof
   cancellation signal and reports the sentinel back on the original `call`;
9. exits promptly on socket EOF and implements the parent-death guard
   (group-kill + exit 137);
10. handles frames up to 256 MiB and never interleaves writes.

The platform's conformance suite (`apps/api/tests/test_worker_conformance.py`)
exercises exactly this list against every bundled runtime.
