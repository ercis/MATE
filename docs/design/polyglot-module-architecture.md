# Language-Agnostic (Polyglot) Module Architecture for Mate

**Status:** Architecture planning / RFC. Conceptual only — no code.
**Scope:** How Mate (`FandFPMIF`) supports modules written in any language, by **generalizing the existing `subprocess` isolation mode into a language-neutral worker contract** rather than replacing the module system.
**Author:** Architecture planning thread.
**Date:** 2026-06-29.

---

## 0. TL;DR — the recommendation in one paragraph

Mate already has, in `apps/api/src/mate/api/modules/subprocess_worker.py` + `subprocess_host.py`, a **bidirectional newline-delimited JSON-RPC protocol over an `AF_UNIX` socket**, with a **Parquet-on-shared-filesystem data plane** for DataFrames. That wire format is *already* ~70% language-neutral; nothing on the socket is Pythonic. What is Python-specific is **bootstrap** (`_worker_python()` hard-codes `<folder>/.venv/bin/python3`, `subprocess_host.py:493-503`), **packaging** (`installer.py` assumes `uv venv`), and **handler discovery** (Python import + decorator reflection in `subprocess_worker.py:68-134`). The recommended design therefore does **not** introduce gRPC, GraalVM, WASM, or Docker sidecars as the primary mechanism. It **promotes the existing socket+Parquet protocol to a published "Worker Contract v1"**, adds a manifest-declared `runtime`/entrypoint block, makes `install_module` and `_instantiate` **dispatch on runtime** instead of assuming `uv`/venv, and keeps every existing Python module working byte-for-byte unchanged (an absent `runtime` field = the legacy Python path). Docker (ephemeral/sidecar) and gRPC/Arrow-Flight are folded in as **optional, additive transport/sandbox tiers behind the same contract**, not as competing architectures. GraalVM and WASM are explicitly rejected for the heavy-numeric process-mining core, with cited reasons.

---

## 1. Context: what Mate's module system actually is

The bound facts below are load-bearing for every later decision. All paths under `/Users/t_zimm11/Code/mate/FandFPMIF`.

### 1.1 Two isolation modes, one binding path

`DependenciesPython.isolation: Literal["in_process","subprocess"]` (`packages/module-sdk-py/src/mate/sdk/manifest.py:52`). The loader branches once:

```python
# loader.py:915-927  _instantiate
if d.manifest.dependencies.python.isolation == "subprocess":
    bridge = SubprocessBridge(d.manifest, d.folder)
    instance = await bridge.start()
    self._bridges[d.id] = bridge
else:
    instance = self._import_module_class(d)
```

Crucially, a subprocess module yields a **`SubprocessModule` duck-type** (`subprocess_host.py:54-138`), not a real `mate.sdk.Module` subclass, and the loader binds it **identically** — `_bind` (`loader.py:1002-1022`) only walks `dir(instance)` and reads decorator metadata off `type(instance)`. So `@route`/`@job`/`@on_event` mount the same way whether the code is in-process or in a worker. **This is the seam we extend.** A polyglot worker is just another producer of the same `ready` handler descriptor and the same wire calls; the loader never needs to know what language it is.

### 1.2 The wire protocol (the part that is already neutral)

- **Transport:** host is the server (`asyncio.start_unix_server`, `subprocess_host.py:182`), `chmod 0o600` (`:185`); worker dials in (`asyncio.open_unix_connection`, `subprocess_worker.py:482`). Worker runs in its own process group (`start_new_session=True`, `:221`) so the host can `killpg` the whole subtree.
- **Framing:** one JSON object per line; `json.dumps(msg)+b"\n"` (`subprocess_worker.py:415-418`), read by `readline()` (`:422`). Buffer cap raised to `RPC_STREAM_LIMIT = 256 MiB` (`:37`) because cache/duckdb JSON exceeds asyncio's 64 KiB default.
- **Envelope:** request `{"id","method","params"}`; success `{"id","result"}`; error `{"id","error":{"message","traceback"?}}`; notification `id:null` (only `ready`). Bidirectional, per-side id spaces (`WireConnection`, `:380-478`).
- **Host→worker methods:** `call`, `shutdown` (`subprocess_worker.py:502-503`).
- **Worker→host methods (the `ctx.*` surface):** `ctx.event_log.duckdb_fetch`, `ctx.event_log.materialize`, `ctx.bus.emit`, `ctx.cache.{get,set,exists,delete}`, `ctx.registry.call`, `ctx.progress.update`, `ctx.logger.log`, `ctx.cancel.check` (`subprocess_host.py:451-462`). Every params dict carries `ctx_token` so the host re-associates the callback to the correct live `ModuleContext` (`:349`, `:388`).
- **Cancel sentinel:** the magic string `"__ff_job_cancelled__"` in `error.message` (`subprocess_host.py:47`, `subprocess_worker.py:42`). Symmetric and language-neutral.

### 1.3 The data plane (the strongest neutrality win)

DataFrames **never** ride the socket. `ctx.event_log.materialize` makes the host write the filter-applied log to Parquet in the per-call `workdir` and return a **path string** (`subprocess_host.py:393-403`); the worker reads it locally (`subprocess_worker.py:185-221`). Parquet is cross-language (Arrow readers exist for Java/Node/Go/Rust/C++). There is **zero pickling** across the boundary — `_jsonify` is `model_dump`/passthrough JSON only (`subprocess_host.py:506-512`).

### 1.4 The non-neutral parts (everything that must change)

1. **Bootstrap.** `_worker_python(folder)` returns `<folder>/.venv/bin/python3` (`subprocess_host.py:493-503`); `_spawn_worker` launches `python <script> <socket> <folder>` (`:205-215`). There is **no manifest field for "how to start the worker."** This is the single biggest blocker.
2. **Packaging.** `installer.py` is `uv venv` + `uv pip install` + force-install `mate.sdk` (`:242-266`). None of this applies to a jar, an `npm ci`, a Go binary, or a container image.
3. **Discovery.** The reference worker imports `module.py`, finds a `Module` subclass, reads decorators (`subprocess_worker.py:68-134`). But its *output* — the `ready` JSON `handlers` list (`:99-134`, `:505`) — is already neutral; the host rebuilds specs from pure JSON (`subprocess_host.py:75-138`). A foreign worker simply *emits* the same JSON; it never needs the decorator machinery.

### 1.5 Deployment baseline

The API runs in `python:3.12-slim` with `uv` (`apps/api/Dockerfile`), no `docker.sock` mounted today (`docker-compose.prod.yml`). All data is on-disk and per-user under `data/users/{user_id}/` (CLAUDE.md). The only real subprocess module today is **agentsimulator** (`modules/agentsimulator/manifest.yaml`: `requires-python: ">=3.12,<3.13"`, pinned numpy 1.x/pandas 2.x), which exists precisely because it needs a *different dependency ABI* than the platform — the canonical use case the polyglot design must keep serving.

---

## 2. Goals, non-goals, invariants

### 2.1 Goals
- A module written in **Node, Java/JVM, Go, Rust, .NET, or any runtime** that can open a Unix socket, frame JSON, and read Parquet, runs as a first-class Mate module: `@route`/`@job`/`@on_event`, full `ctx.*` surface, jobs/progress/cancel.
- **Reuse** `loader._instantiate` binding, the `SubprocessBridge` lifecycle, the cancel state machine, the Parquet data plane, and the SSE fan-out unchanged in shape.
- **Zero churn** for existing Python modules — bundled or third-party.
- A **published contract** (`Worker Contract v1`) that third parties can implement without reading host source.

### 2.2 Non-goals (this thread)
- Replacing the in-process fast path for Python modules (it is the right default and stays).
- Running modules over a real network (the contract is localhost/loopback only — see go-plugin's "not supported over a real network" caveat, [pkg.go.dev/go-plugin](https://pkg.go.dev/github.com/hashicorp/go-plugin)).
- Embedding ProM/GraalVM/WASM into the host process (rejected, §6).
- The MCP server / external tool surface (separate thread; this design only must not break the `user_id`-withheld invariant it relies on).

### 2.3 Invariants that MUST survive (security-critical)
- **`user_id` is never sent to the worker.** Today `ctx_meta` deliberately omits it (`subprocess_host.py:356-364`); all tenant enforcement (cache/registry/bus scoping, `user_id` force-stamp on `bus.emit`, `loader.py:133-146`) stays host-side. A polyglot worker must remain unable to address another tenant.
- **The host is the only DuckDB/filesystem authority.** The worker gets a filter-applied Parquet *path*, never raw log access or the user's data directory.
- **Cooperative + hard cancel both work.** Soft cancel = every `ctx.*` RPC is a poll point via `_guard_cancel` (`subprocess_host.py:470-480`); hard cancel = `killpg` + respawn (`:228-256`). Both must generalize to a non-Python worker (and to a container).

---

## 3. Candidate mechanisms and the decision matrix

The thread asks me to evaluate five candidate mechanisms against each other. I evaluate them as **answers to two separable questions**, because conflating them is the main error to avoid:

- **Q1 — Boundary/contract:** how does host code talk to module code in another language?
- **Q2 — Sandbox/packaging:** how is the foreign runtime built, launched, and confined?

The five candidates map onto these axes:

| Candidate | Primarily answers | Notes |
|---|---|---|
| Extend existing subprocess RPC to non-Python workers | Q1 (boundary) | Reuses everything Mate already has |
| Docker sidecar / ephemeral containers (Docker API) | Q2 (sandbox/packaging) | Orthogonal to the wire format — can run *under* the same contract |
| gRPC / Arrow-Flight sidecars | Q1 (boundary, alternative transport) | Could replace JSON+Parquet; heavyweight |
| GraalVM polyglot | Q1+Q2 (in-process VM) | Rejected for heavy numeric core |
| WASM / WASI Component Model | Q1+Q2 (in-process sandbox) | Rejected for heavy numeric core today |

### 3.1 Decision matrix (Q1 — the boundary)

Weighted for Mate's reality: large event-log DataFrames, a mature Python subprocess protocol already in production, a single-host on-disk deployment, third-party module authors of varying sophistication.

| Criterion (weight) | **Extend existing socket+Parquet RPC** | gRPC + Arrow Flight sidecar | GraalVM polyglot | WASM / Component Model |
|---|---|---|---|---|
| **Fit with current code** (×3) | ★★★★★ Already the protocol; only bootstrap/packaging change | ★★ New transport, IDL, codegen; replaces a working layer | ★ JVM host, doesn't match FastAPI/asyncio host | ★ New host runtime + WIT toolchain |
| **Security / isolation** (×3) | ★★★ OS process; `killpg`; +seccomp/container tiers available | ★★★ Same OS-process model | ★★ Shared VM heap; native-image hostile to dynamic loading | ★★★★★ Capability sandbox, best default isolation |
| **Data-transfer efficiency for large logs** (×3) | ★★★★ Parquet zero-copy via shared FS, no socket marshalling | ★★★★★ Arrow Flight zero-copy, 2–3 GB/s localhost | ★★★ in-memory but GraalPy C-ext copies | ★ 4 GB wasm32 wall; ~10× slower BLAS; no prod threads |
| **Dev ergonomics** (×2) | ★★★★ Author emits JSON + reads Parquet; matches a known pattern | ★★★ Must compile protobufs + Flight server per language | ★★ GraalPy C-ext pinning hell for pm4py/pandas | ★★ Immature JVM→WASM, version-pinned numerics |
| **Ops complexity** (×2) | ★★★★ No new infra; one warm worker per module (today's model) | ★★★ Extra ports, TLS/mTLS, Flight server lifecycle | ★ Oracle de-emphasizing Java/native-image | ★★ New runtime, partial syscall compat |
| **Maturity of pattern** (×1) | ★★★★ In production in Mate; mirrors go-plugin/LSP | ★★★★ Flight proven for Python↔Java tabular | ★★ Narrowing envelope, vendor pivot | ★★★ WASI 0.2 stable Jan-2024 but numerics unfit |

**Winner (Q1): extend the existing socket+Parquet RPC into a published Worker Contract v1.** It dominates on *fit with current code* (the most heavily weighted, because the protocol literally already exists and is in production for agentsimulator) and is competitive everywhere else. Arrow Flight is genuinely *better* on raw bulk-transfer throughput ([Arrow Flight intro](https://arrow.apache.org/blog/2019/10/13/introducing-arrow-flight/): >2–3 GB/s localhost, 10×–100× over REST per [TO THE NEW](https://www.tothenew.com/blog/accelerating-data-transfer-with-apache-arrow-flight/)), but Mate's data plane is **already** zero-copy-ish: it writes one Parquet file to a shared tmpfs/bind-mount and hands over a path, with no per-row marshalling at all (`subprocess_host.py:393-403`). The marginal win from Flight does not justify replacing a working transport, adding a gRPC/IDL toolchain to every module author's build, and re-implementing cancel/progress/bus over a second channel. **Arrow Flight is retained as an optional, opt-in data-plane upgrade (§5.6), not the baseline.**

### 3.2 Decision matrix (Q2 — sandbox/packaging tiers)

These are **not** mutually exclusive with the winner above; they sit *under* the same contract. The question is which to support and as what tier.

| Tier | Launch mechanism | Isolation strength | When | Cost |
|---|---|---|---|---|
| **T0 — native process** (default, = today) | `runtime`-declared entrypoint in module's own build dir | OS process + `killpg`, own process group | Trusted/curated modules; your own first-party polyglot modules | Lowest; reuses bridge as-is |
| **T1 — hardened native process** | T0 + seccomp/`no-new-privileges`/rlimits via a launcher wrapper | Stronger syscall surface reduction | Semi-trusted modules | Small |
| **T2 — ephemeral container** | Orchestrator service drives Docker API; per-call or warm-pool container; socket dir + workdir bind-mounted | Namespace + cgroup; `--cap-drop ALL`, `--pids-limit`, `--memory`, `--network none` | Untrusted-but-curated third-party modules | Medium; needs orchestrator + socket-proxy |
| **T3 — microVM** (gVisor `runsc` / Kata+Firecracker) | T2 with a hardened runtime class | Userspace-kernel (gVisor) or KVM microVM (Kata) | Actively adversarial / arbitrary third-party code | High |

Rationale and citations are in §7 (security). The key architectural point: **the same Worker Contract v1 runs at every tier** because the contract is "speak newline-JSON on this socket path, read/write Parquet at these paths." Going from T0 to T2 changes *namespace plumbing* (bind-mount the socket dir + workdir into the container, replace `killpg` with `docker kill`), **not the bytes on the wire** — exactly as the grounding's "what a container worker would need" section concluded.

---

## 4. Recommended architecture

### 4.1 One sentence

**Promote the existing Unix-socket JSON-RPC + Parquet protocol to a published, versioned `Worker Contract v1`; add a `runtime` block to the manifest; make `install_module` and `_instantiate` dispatch on `runtime`; keep the absent-`runtime` path bit-identical to today's Python flow; layer Docker/microVM sandbox tiers and an optional Arrow-Flight data plane behind the same contract.**

### 4.2 New manifest fields

Add a `runtime` block to `Dependencies` (`packages/module-sdk-py/src/mate/sdk/manifest.py`). It is **optional**; absent ⇒ today's Python behavior, so every existing `manifest.yaml` validates and behaves unchanged.

```yaml
# manifest.yaml (NEW, all under dependencies:)
dependencies:
  # ----- existing, unchanged -----
  python:
    isolation: subprocess        # required to be `subprocess` when `runtime` is present
    requires-python: ">=3.12"    # ignored for non-python runtimes
    packages: [...]
  npm: [...]

  # ----- NEW: language-neutral worker runtime -----
  runtime:
    language: node               # python | node | jvm | go | dotnet | binary | container
    contract: "1"                # Worker Contract major version the worker speaks
    # How to BUILD the worker's deps/artifact (dispatched by installer.py).
    build:
      kind: npm                  # uv | npm | maven | gradle | go | cargo | dotnet | docker | none
      # kind-specific knobs, validated per kind:
      # npm:    install: "ci"          (npm ci / pnpm i --frozen-lockfile)
      # maven:  goals: ["-q","package"]; artifact: "target/worker.jar"
      # go:     package: "./cmd/worker"; out: "bin/worker"
      # docker: dockerfile: "Dockerfile"; image_tag: "ff-mod-<id>:<version>"  (T2/T3 only)
    # How to LAUNCH the built worker (dispatched by subprocess_host._spawn_worker).
    # The two trailing args MUST be: <socket_path> <module_folder>, exactly as today.
    entrypoint: ["node", "dist/worker.js"]
    # Optional sandbox tier (default t0). t2/t3 require the orchestrator service.
    sandbox: t0                  # t0 | t1 | t2 | t3
    # Optional resource caps (enforced at t1+; advisory at t0).
    limits:
      memory_mb: 2048
      cpus: 2.0
      pids: 256
      wall_seconds: 1800
    # Optional: opt into the Arrow-Flight data plane instead of Parquet-by-path.
    data_plane: parquet          # parquet | arrow_flight
```

Notes:
- **`contract`** is the public protocol version the worker pins to. The host advertises supported versions; mismatch ⇒ load skipped with a clear error (mirrors LSP/MCP capability negotiation, [freeCodeCamp LSP](https://www.freecodecamp.org/news/what-is-the-language-server-protocol-easier-code-editing-across-languages)).
- A manifest with `runtime.language != python` **must** set `python.isolation: subprocess` (validation rule), because there is no in-process path for a non-Python module. Add a `model_validator` to `Dependencies` enforcing this and the `entrypoint`/`build.kind` consistency.
- `runtime.entrypoint` is the field whose **absence today is the core blocker** (§1.4). Its presence is what replaces the hard-coded `_worker_python`.

### 4.3 The worker handshake / protocol (Worker Contract v1)

This is the **existing protocol, frozen and documented**. No new bytes. Spelled out as the public contract a foreign worker implements:

1. **Connect.** Worker reads `argv` = `[<entrypoint args...>, socket_path, module_folder]`, opens `AF_UNIX` stream to `socket_path`. (Today: `subprocess_worker.py:482`.)
2. **Frame.** Newline-delimited UTF-8 JSON, one object per line. Match the host's 256 MiB line ceiling (`RPC_STREAM_LIMIT`). (Today: `:415-422`.)
3. **Announce `ready`.** Send notification `{"id":null,"method":"ready","params":{"handlers":[...], "contract":"1"}}`. Each handler descriptor is the existing neutral JSON (`subprocess_worker.py:111-132`):
   ```json
   {"attr":"kpis",
    "route":{"method":"GET","path":"/kpis","name":null},
    "on_event":{"topic":"log.imported"},
    "job":{"progress":true,"priority":0,"cancellable":true,
           "result_url":null,"title":"Computing KPIs","subtitle":null}}
   ```
   The host rebuilds `RouteSpec`/`EventSubscription`/`JobSpec` from this exactly as it does now (`subprocess_host.py:75-138`). **Callable title/subtitle remain unsupported across the boundary** (already the case) — serialize as `null`, host falls back to a static label.
4. **Serve `call`.** Host sends `{"id","method":"call","params":{handler, ctx_token, ctx:{log_id,module_id,workdir,config,capabilities}, args[], kwargs{}}}`. Worker dispatches to its handler. (Today: `subprocess_worker.py:490-500`.) **`user_id` is absent by design** and must stay absent.
5. **Call back via `ctx.*`.** Worker issues requests `{"id","method":"ctx.<...>","params":{"ctx_token", ...}}`; host re-associates by `ctx_token` and answers. Method table = §1.2 (extended in §4.7).
6. **Cancel.** On a soft cancel, the host answers the *next* `ctx.*` RPC with `{"error":{"message":"__ff_job_cancelled__"}}`; the worker must abort its handler and report the same sentinel on unwind. On hard cancel the host kills the process group / container. (Today: `subprocess_host.py:445-490`, `subprocess_worker.py:441-468`.)
7. **`shutdown`.** Host sends `{"method":"shutdown"}`; worker returns `true` and exits.

A **conformance test harness** (host-side, language-agnostic) drives a candidate worker through this sequence — this is the deliverable that makes the contract *publishable*.

### 4.4 How the host exposes `ModuleContext` capabilities over the wire

Unchanged in shape from `subprocess_host._ctx_handlers` (`:384-468`). The host already maps each `ctx.*` RPC name to a real `ModuleContext` method looked up by `ctx_token`, and wraps every one in `_guard_cancel` so it doubles as a cancel poll point. The polyglot generalization is purely **documenting these as the public method table** and adding the missing ones (§4.7). The host implementation file (`subprocess_host.py`) does not need to know the worker's language — it already doesn't.

### 4.5 How large DataFrames cross the boundary

**Keep the Parquet-by-path data plane as the baseline; it is already the polyglot strength.** `ctx.event_log.materialize` → host writes filter-applied Parquet to `workdir` → returns path → worker reads with its own Arrow/Parquet reader (`subprocess_host.py:393-403`, `subprocess_worker.py:185-221`). For a container worker (T2/T3), the *only* change is that `workdir` and the socket dir must be **bind-mounted into the container** so the path resolves on both sides — the file itself is unchanged. For symmetric writes (a worker producing a large tabular result), extend the convention so the worker writes Parquet into `workdir` and returns the **path**, which the host ingests (this also closes the `ctx.cache.set` tabular gap, §4.7).

Optional upgrade (`data_plane: arrow_flight`): for workers doing very large, streaming, multi-pass tabular exchange (e.g. a JVM Spark-backed module), the host can additionally stand up a localhost Arrow Flight endpoint and hand the worker a ticket instead of a path. Justified only by the cited Flight throughput numbers for Python↔Java; gated behind the manifest flag so it never burdens simple workers. ([Arrow Flight](https://arrow.apache.org/blog/2019/10/13/introducing-arrow-flight/), [PyArrow Flight cookbook](https://arrow.apache.org/cookbook/py/flight.html), [SparkArrowFlight](https://github.com/BryanCutler/SparkArrowFlight).)

### 4.6 Build / packaging (runtime-dispatched installer)

`install_module` (`installer.py:155-278`) becomes a **dispatcher keyed on `runtime.build.kind`**, with the existing `uv` path factored into a `kind: uv` (default for Python). Each builder's contract: produce a self-contained, runnable artifact in the module folder and return a marker the loader/host can find; cache on `dependencies_hash()` exactly as today (`.installed-hash`, `:202-226`).

| `build.kind` | Builder action | Launch artifact | Notes |
|---|---|---|---|
| `uv` (default) | `uv venv` + `uv pip install` + SDK install (today) | `.venv/bin/python3` | Unchanged; legacy path |
| `npm` | `npm ci` / `pnpm i --frozen-lockfile` in folder | `node dist/worker.js` | Cache on lockfile hash folded into `dependencies_hash` |
| `maven`/`gradle` | `mvn -q package` / `gradle build` | `java -jar target/worker.jar` | JVM present in image or T2 container |
| `go` | `go build -o bin/worker ./cmd/worker` | `bin/worker` | Static binary, simplest T0 |
| `cargo`/`dotnet` | analogous | binary / `dotnet worker.dll` | |
| `docker` | BuildKit build, deps in early layers, source last, `--mount=type=cache`, shared registry cache | image tag | Only valid for `sandbox: t2/t3` |
| `none` | no build | prebuilt `entrypoint` | For binary modules shipped ready-to-run |

`dependencies_hash()` (`manifest.py:264-271`) must fold the **whole `runtime` block** into the key so changing the entrypoint/build rebuilds. For `docker` builds, follow the cited cache discipline — deps in early layers, source last, BuildKit cache mounts, per-language base image, shared registry cache for ~70–90% build-time reduction ([Docker optimize cache](https://docs.docker.com/build/cache/optimize/), [BuildKit](https://github.com/moby/buildkit), [TestDriven.io](https://testdriven.io/blog/faster-ci-builds-with-docker-cache/)).

**SDK shipping for non-Python runtimes.** Today the installer force-installs `mate.sdk` into the venv so the reference worker has the wire library (`installer.py:255-266`). The polyglot equivalent is a **thin per-language worker SDK** (a small library, not the whole `mate.api` chain) that implements: socket connect, JSON framing at 256 MiB, the `ready` descriptor, the `ctx.*` proxies, the cancel sentinel, and a Parquet reader. Ship `@mate/worker-sdk` (Node), `mate-worker-sdk` (a tiny jar), etc. These are the foreign analog of `mate.sdk`'s wire half — and they are small, because **the contract is small**. Authors who don't want the SDK can implement the 7-step handshake directly.

### 4.7 Closing the `ModuleContext` gaps (from the boundary catalog)

The existing Python wire is *incomplete* vs the full `ModuleContext` Protocol. A published v1 contract should close these (each is a host-side `ctx.*` method addition in `subprocess_host._ctx_handlers`, plus a worker-side proxy):

1. **`ctx.object_log.*`** — no OCEL RPCs exist today (`object_centric_log_access.py` has no bridge). Add `ctx.object_log.duckdb_fetch` and `ctx.object_log.materialize_*` (per-table Parquet handoff, mirroring `event_log`). Required for polyglot OCEL modules.
2. **`ctx.open_event_log`** — no cross-log RPC. Add `ctx.open_event_log` returning a *second* ctx-scoped log handle/token (ownership-checked host-side as today, `loader.py:1431-1444`). The `user_id`-withheld invariant means the host enforces ownership; the worker only ever gets a token.
3. **`ctx.bus.subscribe`** — only `emit` is bridged. Streaming subscriptions need a host→worker push channel (the protocol is already bidirectional, so this is a new `ctx.bus.subscribe` that the host services by pushing `bus.event` notifications keyed by the subscription).
4. **`ctx.cache.set` with tabular values** — today `value` crosses as JSON (`subprocess_host.py:413-415`), so a worker can't cache a DataFrame. Extend with the Parquet-path convention: worker writes Parquet to `workdir`, calls `ctx.cache.set_table(key, path)`; host moves/ingests it into `data/users/{user_id}/module_results/...` (cache.py).
5. **`ctx.config`** — snapshot-only; document as immutable per-call (no change needed, but it must be in the published contract).
6. **`ctx.run_in_process`** — N/A for a worker (the worker *is* the offload boundary). Document that a polyglot worker uses its own concurrency; no RPC.

These additions are **backward compatible**: existing Python in-process modules use the real `ModuleContext` directly and are unaffected; the existing Python *subprocess* worker keeps using the subset it already implements.

### 4.8 Where the code changes land (exact files)

| Concern | File | Change |
|---|---|---|
| Manifest schema | `packages/module-sdk-py/src/mate/sdk/manifest.py` | Add `Runtime`/`RuntimeBuild` models + `runtime` field on `Dependencies`; validators (`language!=python ⇒ subprocess`, entrypoint/build consistency); fold `runtime` into `dependencies_hash()` |
| Instantiation dispatch | `apps/api/src/mate/api/modules/loader.py` (`_instantiate`, `:915-927`) | Branch already gates on `isolation == "subprocess"`; pass `manifest.dependencies.runtime` into the bridge; no change to `_bind` |
| Worker launch | `apps/api/src/mate/api/modules/subprocess_host.py` (`_spawn_worker` `:198-226`, `_worker_python` `:493-503`) | Replace `_worker_python` with a `_resolve_launch(runtime, folder)` that returns the manifest `entrypoint` (T0/T1) or invokes the orchestrator (T2/T3); keep socket/`ready`/cancel logic intact |
| Build dispatch | `apps/api/src/mate/api/modules/installer.py` (`install_module` `:155-278`) | Dispatch on `runtime.build.kind`; factor today's `uv` path into `kind: uv`; add npm/maven/go/docker builders; same `.installed-hash` caching |
| ctx gaps | `apps/api/src/mate/api/modules/subprocess_host.py` (`_ctx_handlers` `:384-468`) | Add `object_log.*`, `open_event_log`, `bus.subscribe`, `cache.set_table` handlers |
| Cancel for containers | `subprocess_host.py` (`cancel_active` `:228-256`, `_kill_worker_group` `:267-278`) | T2/T3: route hard cancel to `docker kill` instead of `killpg` |
| Orchestrator (new) | new `apps/api/src/mate/api/modules/container_orchestrator.py` | T2/T3 only: holds Docker API access behind a socket-proxy; builds/runs/destroys per-module containers |
| Worker SDKs (new) | `packages/worker-sdk-node/`, `packages/worker-sdk-jvm/`, … | Thin per-language wire libraries |
| Docs (new) | `modules/README.md` + `docs/worker-contract-v1.md` | Publish the 7-step handshake + ctx method table |

---

## 5. The two-question architecture, drawn

```
                        ┌──────────────────────────────────────────────┐
                        │  Mate API process (FastAPI, asyncio, py3.12)  │
                        │                                              │
   /api/v1/modules/...  │   loader._bind  ← SubprocessModule stubs     │
   bus / SSE / jobs ────┤   loader._instantiate (isolation=subprocess) │
                        │   SubprocessBridge  (host = socket server)   │
                        │   _ctx_handlers: event_log/cache/bus/...     │
                        └───────────────┬──────────────────────────────┘
                                        │ AF_UNIX socket (newline-JSON, 256 MiB)
                                        │ + Parquet files in shared workdir
          ┌─────────────────────────────┼─────────────────────────────┐
          │ T0/T1 native process        │       T2/T3 container        │
          │ entrypoint: [node,worker.js]│   orchestrator → Docker API  │
          │ own process group (killpg)  │   bind-mount socketdir+workdir│
          │  ┌───────────────────────┐  │   --cap-drop ALL --pids-limit │
          │  │ Worker (any language) │  │   gVisor/Kata for T3          │
          │  │ speaks Worker Contract│  │  ┌───────────────────────┐    │
          │  │  v1; reads Parquet    │  │  │ same worker, same wire│    │
          │  └───────────────────────┘  │  └───────────────────────┘    │
          └─────────────────────────────┴─────────────────────────────┘
```

The contract is identical across both columns; only launch + namespace plumbing differ.

---

## 6. Explicitly rejected mechanisms (with reasons + citations)

### 6.1 GraalVM polyglot — REJECTED for the module core
- **Wrong host runtime.** Mate's host is FastAPI/asyncio on CPython 3.12. GraalVM polyglot interop requires a **JVM host**; running pm4py/pandas under GraalPy needs **GraalPy-on-JVM** (the `java` module "is only available on the JVM distribution," not native standalone — [Interoperability docs](https://www.graalvm.org/jdk23/reference-manual/python/Interoperability/)). That would mean rebasing Mate's backend on a JVM — a non-starter.
- **C-extension tax.** GraalPy's native-extension support is **experimental, ABI-incompatible, must-rebuild-from-source**, with degraded C-extension performance ([Native-Extensions.md](https://github.com/oracle/graalpython/blob/master/docs/user/Native-Extensions.md), [Performance.md](https://github.com/oracle/graalpython/blob/master/docs/user/Performance.md)). pm4py on numpy/pandas/scipy is exactly the fragile case (pandas pulled an incompatible numpy 2.0.2, manual pin required — [JDriven](https://jdriven.com/blog/2025/03/Tabs-and-Brackets-Mixing-Java-and-Python-using-GraalPy)). No source confirms pm4py runs on GraalPy.
- **Vendor risk.** Oracle is **discontinuing GraalVM's Java/native-image features** for paid Oracle JDK and pivoting GraalVM to Python/JS ([biggo](https://finance.biggo.com/news/202509290122_Oracle_Discontinues_GraalVM_Java_Support)). Building the integration spine on the de-emphasized part is a bet against the vendor.
- **Verdict:** GraalVM buys Mate nothing a subprocess/container worker doesn't, and adds substantial risk.

### 6.2 WASM / WASI Component Model — REJECTED for the heavy-numeric core, REVISIT for light untrusted plugins
- **Best-in-class sandbox** (capability-based, in-process) — genuinely attractive *if* the workload were light glue logic.
- **Unfit for process-mining numerics today:** no production WASI threads (shared-everything-threads "not yet available in any WASI host runtime" — [wasi-threads](https://github.com/WebAssembly/wasi-threads)); OpenBLAS on WASM ~**10× slower**, single-threaded, no SIMD by default ([OpenBLAS #4023](https://github.com/OpenMathLib/OpenBLAS/issues/4023)); **4 GB wasm32 memory wall** (2 GB default without opt-in), Memory64 adds a 10–100% per-access penalty ([V8 4GB](https://v8.dev/blog/4gb-wasm-memory), [SpiderMonkey memory64](https://spidermonkey.dev/blog/2025/01/15/is-memory64-actually-worth-using.html)); JVM→WASM toolchains still Beta/browser-skewed ([Kotlin/Wasm](https://kotlinlang.org/docs/wasm-overview.html)).
- **Verdict:** Event logs routinely exceed the practical memory ceiling and need native BLAS/threads. Keep WASM on the roadmap as a **future T-level for untrusted lightweight plugins** once shared-everything-threads + WASI 0.3 mature; do not put the data-science core on it.

### 6.3 gRPC + Arrow Flight as the *baseline* boundary — REJECTED as default, RETAINED as opt-in
- Arrow Flight is the *right* tool for very large Python↔Java tabular streaming and wins the raw-throughput axis ([Arrow Flight](https://arrow.apache.org/blog/2019/10/13/introducing-arrow-flight/), [ACM benchmark](https://dl.acm.org/doi/fullHtml/10.1145/3527199.3527264)).
- But Mate's existing Parquet-by-path plane is **already** marshalling-free, and replacing the working JSON+socket control plane with gRPC/protobuf imposes an IDL + codegen burden on every module author and forces re-implementing cancel/progress/bus on a second channel.
- **Verdict:** make it the `data_plane: arrow_flight` opt-in (§4.5) for the rare heavyweight case; never the baseline.

### 6.4 ProM specifically — selective wrappers, not a generic bridge (adjacent note)
The research is clear that a *generic* ProM bridge is the wrong shape (RapidProM proves the generic bridge still wraps plugins per-plugin and drags in the full ProM 6 JVM; pm4py has closed the genetic-miner/DECLARE/OCEL gaps). When Mate does want a ProM capability (highest value: **data-aware decision mining → Data Petri Nets**, which pm4py lacks), the right vehicle is a **single `runtime: jvm` Mate module** that headlessly invokes one ProM CLI plugin with **XES in / PNML out** (`export_prom5`), exchanging artifacts via this same Worker Contract's Parquet/workdir plane — license-clean because ProM (GPL core) runs as a **separate process at arm's length**, never linked into Mate ([ProM4Py REST precedent, MIT](https://www.vdaalst.com/publications/p1495.pdf); [pm4py PNML export_prom5](https://pm4py-source.readthedocs.io/en/latest/pm4py.objects.petri_net.exporter.variants.html)). This validates the polyglot design: ProM-as-a-module is just a `jvm` worker, no special-casing in the host.

---

## 7. Security & sandboxing

The contract's isolation tiers (§3.2) map onto the cited research as follows.

- **T0/T1 (native process).** Today's model: own process group, `chmod 0o600` socket, `killpg` hard-stop. T1 adds OS confinement via a launcher wrapper: `--cap-drop`-equivalent (`prctl`/seccomp), `no-new-privileges`, rlimits (`nofile`/`nproc`/memory), wall-clock timeout enforced host-side (the bridge already has the timeout machinery). Process isolation is **not** a sandbox — a native worker runs as the API's OS user, exactly like go-plugin ([go-plugin](https://github.com/hashicorp/go-plugin)). Acceptable for first-party and curated modules.
- **T2 (ephemeral container).** For untrusted-but-curated third-party modules. **Isolate Docker access behind one orchestrator service** that holds the socket, ideally behind a **socket proxy** (Tecnativa) scoped to only `containers`/`images`/`exec` — plugin code never sees `docker.sock` ([OWASP rule #1](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html), [Tecnativa](https://github.com/Tecnativa/docker-socket-proxy)). Read-only socket mounting is **not** safe ([amf3](https://amf3.github.io/articles/virtualization/docker_socket/)). Run with `--cap-drop ALL --security-opt no-new-privileges --read-only --memory --memory-swap --cpus --pids-limit --network none`, plus an orchestrator wall-clock timeout ([Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/), [StackHarbor](https://stackharbor.com/en/knowledge-base/docker-resource-limits/)). Prefer a **warm pool of ephemeral containers, one untrusted call per container, destroyed after** ([Northflank ephemeral sandboxes](https://northflank.com/blog/ephemeral-sandbox-environments)). The socket dir + workdir are bind-mounted **read-write but scoped to the per-call temp dir**, never host paths.
- **T3 (microVM).** For arbitrary adversarial code: **gVisor `runsc`** (userspace kernel, RuntimeClass drop-in, ~10–30% I/O overhead) for untrusted-but-curated-at-scale, or **Kata + Firecracker** (KVM microVM, own guest kernel) for fully adversarial ([Northflank Kata/Firecracker/gVisor](https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor)).

**Tenant invariant enforcement is unchanged and host-side.** Because `user_id` never crosses to the worker and the host owns DuckDB + the filesystem + `bus.emit` user-stamping, **a compromised foreign worker cannot reach another tenant's data even at T0** — the worst a malicious T0 worker can do is misbehave within its own call's `ctx`, and escalate on the host OS (which T1/T2/T3 progressively contain). This is the design's strongest security property and it falls out of the *existing* architecture; the polyglot work must not weaken it (e.g. never add `user_id` to `ctx_meta`, never give a worker a raw log path outside `workdir`).

---

## 8. Migration path (keeps every Python module working unchanged)

The migration is structured so that at **every** phase, an existing `manifest.yaml` with no `runtime` block behaves bit-identically to today.

- **Phase 0 — Freeze & publish the contract (no behavior change).** Extract the wire protocol, `ready` descriptor, and `ctx.*` table into `docs/worker-contract-v1.md`. Fix the stale `python -m ...` docstring (`subprocess_worker.py:3-5`). Add a host-side conformance harness that runs the *existing* Python worker through the spec (regression baseline). No code path changes.
- **Phase 1 — Manifest `runtime` block + validation.** Add the `Runtime` models to `manifest.py` with validators; absent ⇒ legacy. Fold `runtime` into `dependencies_hash()`. Existing manifests unaffected (extra field is optional). Ship the SDK schema so authors can validate locally.
- **Phase 2 — Launch dispatch (T0).** Replace `_worker_python` with `_resolve_launch(runtime, folder)`; default (no `runtime`) returns `.venv/bin/python3` exactly as before. Now a `runtime.entrypoint` can launch a non-Python worker. Socket/`ready`/cancel logic untouched. **First polyglot smoke test:** a trivial `runtime: go` or `runtime: node` "hello-worker" emitting `ready` + serving one `@route`.
- **Phase 3 — Build dispatch + worker SDKs.** Factor `install_module` into a `build.kind` dispatcher (`uv` is the default, unchanged). Add `npm`/`go`/`maven` builders. Ship `@mate/worker-sdk` (Node) and one JVM SDK as references. Now a real polyglot module (e.g. a Node module) is end-to-end installable + runnable at T0.
- **Phase 4 — Close `ctx.*` gaps.** Add `object_log.*`, `open_event_log`, `bus.subscribe`, `cache.set_table` to `_ctx_handlers` and to the Python + foreign worker SDKs. The existing Python subprocess worker keeps using its current subset (additive, no break).
- **Phase 5 — Container tier (T2) behind the orchestrator.** Add `container_orchestrator.py` + socket-proxy; `build.kind: docker`; route hard cancel to `docker kill`; bind-mount socket dir + workdir. Gate on `sandbox: t2`. T0/T1 modules unaffected. This is the phase that introduces `docker.sock` access to the deployment — keep it optional and off by default (single-host installs that trust their modules never enable it).
- **Phase 6 — Hardened tiers (T1 seccomp, T3 gVisor/Kata) + optional Arrow-Flight data plane.** Opt-in, for untrusted-marketplace scenarios and heavyweight JVM tabular modules respectively.
- **Phase 7 — Showcase: ProM-as-a-`jvm`-module.** Implement the single highest-value ProM capability (data-aware decision mining → Data Petri Net) as a `runtime: jvm` module exchanging XES/PNML over the Parquet/workdir plane, validating the whole stack on a GPL, license-sensitive, non-Python runtime.

Backward-compatibility guarantees, restated: absent `runtime` ⇒ legacy Python; in-process modules never touch any of this; the `.installed-hash` cache key change forces a one-time rebuild of subprocess venvs but nothing more; the only Python subprocess module today (agentsimulator) keeps working because `runtime: python` (or absent) routes through the unchanged `uv`/venv path.

---

## 9. Risks & open questions

- **Orchestrator = root-equivalent.** Docker API access is root-on-host ([Docker Engine security](https://docs.docker.com/engine/security/)). Mitigation: single minimal orchestrator behind a socket-proxy; plugin code never sees the socket; consider rootless Docker/Podman for the daemon ([Rootless Docker](https://docs.docker.com/engine/security/rootless/)). **Open question:** does Mate's single-host, self-hosted posture even want T2/T3 by default, or only for a future module marketplace? Recommend: T0/T1 ship first; T2/T3 stay opt-in.
- **Build-time blowup.** Per-module images/jars/node_modules inflate install time and disk. Mitigation: per-language base images + BuildKit cache mounts + shared registry cache ([Docker optimize cache](https://docs.docker.com/build/cache/optimize/)). Cache key correctness via `dependencies_hash()` folding the full `runtime` block.
- **Cold-start latency at T2.** Container start on the call path. Mitigation: warm pool ([Northflank](https://northflank.com/blog/ephemeral-sandbox-environments)). The current model is one *long-lived* warm worker per module — preserve that for T0/T1; only T2's per-call-destroy trades latency for isolation.
- **`bus.subscribe` push semantics.** Streaming subscriptions to a worker need backpressure/queue policy mirroring the bus's bounded-queue/drop-oldest behavior (`events/bus.py`); naïve push could starve the worker or leak memory.
- **Worker SDK fan-out.** Maintaining N language SDKs is real cost. Mitigation: keep the contract tiny (7 steps + ~12 methods) so a from-scratch implementation is feasible; treat SDKs as conveniences, not requirements; lean on the conformance harness as the source of truth.
- **Version skew.** `contract` negotiation must be strict; a worker pinned to v1 against a v2 host must fail loudly at load, not mid-job. Mirror MCP/LSP capability negotiation discipline.
- **Callable `@job` title/subtitle** still can't cross the boundary (already true). Document as a contract limitation.
- **Arrow-Flight + tenant isolation.** If `arrow_flight` is enabled, the Flight endpoint must enforce the same per-call ticket scoping the Parquet path gets — a misimplemented Flight server could become a cross-tenant data path. Keep it localhost-only and ticket-scoped.

---

## 10. Why this is the right shape (closing argument)

Mate did not build a Python plugin system; it built a **host that speaks a small language-neutral RPC and hands data over the filesystem as Parquet**, and then happened to ship a Python reference worker. The grounding's own verdict — "the wire protocol, message framing, ctx-RPC method set, error/cancel conventions, the `ready` handler descriptor, and the Parquet data-plane are already language-neutral … the non-neutral parts are all bootstrap/packaging, not protocol" — is the whole thesis. The cheapest, lowest-risk, highest-fit path to polyglot is therefore **not** to adopt gRPC, GraalVM, WASM, or Docker as a new architecture, but to **name the contract Mate already has, fill its documented gaps, make bootstrap/packaging runtime-pluggable, and add sandbox tiers underneath it**. Docker, Arrow Flight, and (eventually) WASM all become *tiers and options behind one contract* rather than forks in the road — which is exactly how a module system should absorb new runtimes without rewriting its core.
