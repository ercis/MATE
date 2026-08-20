# Mate Extensibility Strategy: Polyglot Modules, ProM Integration, and an Authenticated MCP Server

**Status:** Lead-architect forward plan. Planning only — no code.
**Scope:** Three architecture threads, adversarially reviewed, integrated into one actionable plan.
**Audience:** Mate (FandFPMIF) engineering team.

This document supersedes the three standalone design docs as the *coordinating* plan. The standalone docs remain the detailed references:
- `docs/design/polyglot-module-architecture.md`
- `docs/design/prom-integration-module.md`
- `docs/architecture/mcp-server-design.md`

All file paths below are under `/Users/t_zimm11/Code/mate/FandFPMIF/` unless noted. Where a claim is load-bearing it is grounded to a Mate file/line or a research URL.

---

## 1. Executive summary

Mate already has a strong, half-finished extensibility spine. The `SubprocessBridge` (`apps/api/src/mate/api/modules/subprocess_host.py`) plus its worker (`subprocess_worker.py`) already speak a **language-neutral wire protocol**: newline-delimited JSON-RPC over a `0o600` AF_UNIX socket, with a bidirectional `ctx.*` callback surface and — critically — a **Parquet-on-shared-filesystem data plane** that never pickles a DataFrame across the boundary (`subprocess_host.py:393-403`). The bytes on that socket carry no Python. What is Python-specific is *only the bootstrap*: the host hard-assumes a venv interpreter at `<folder>/.venv/bin/python3` (`_worker_python`, `subprocess_host.py:493-503`), packages with `uv` (`installer.py`), and discovers handlers by Python import + decorator reflection. This is the single most important strategic fact in this document: **we do not need to adopt gRPC, GraalVM, WASM, or Docker to be polyglot. We need to promote our existing protocol to a documented contract and make the launch/packaging steps dispatch on a manifest-declared runtime.**

The recommended direction is therefore *evolutionary, not revolutionary*, and deliberately conservative on the speculative parts:

- **Thread 1 (Polyglot):** Promote the existing wire protocol to a frozen, versioned, documented contract. Add a `dependencies.runtime` manifest block that declares how to build and launch a non-Python worker. Make `install_module` and `_instantiate` dispatch on it. Ship **two isolation tiers only for v1 — T0 (subprocess in a process group, current behavior) and T1 (T0 + OS hardening: rlimits, `--network none`-equivalent, scoped workdir)**. Defer Docker/microVM tiers (T2/T3), Arrow Flight, and per-language SDK fan-out beyond one reference SDK. The adversarial review's headline finding is adopted as a hard invariant: **secrets in `ctx.config` must never cross to a non-trusted-tier worker** — today the host ships `config_json` (which can contain a user's self-hosted AI API key under `config_json["ai"]`, `manifest.py:165,234`) verbatim to the worker (`subprocess_host.py:360`).

- **Thread 2 (ProM):** Build a **selective wrapper** around 3-4 high-value, ProM-only plugins (flagship: the Data-Petri-Net decision miner), *not* a generic ProM bridge. Host ProM out-of-process as a single long-lived **Java 8 JVM** behind a ProM4Py-style REST shim, and drive it from a Python module that is an ordinary `isolation: subprocess` module today (Shape A, zero loader changes) and a polyglot runtime later (Shape B, rides Thread 1). The review's two blocking gaps are adopted: **(a) a concurrency/tenant-isolation design before any plugin ships** (one shared JVM serving N users is a defect, and the current `cancel_active()` `killpg` would let one user's cancel kill another's run), and **(b) all licensing conclusions demoted to "pending legal review,"** with `auto_update: off` and "never bundle the GPL core in a shipped image" as hard defaults.

- **Thread 3 (MCP):** Build an authenticated **Streamable HTTP** MCP server mounted in-process at `/mcp`, reusing Mate's existing Keycloak RS256 + JWKS auth chokepoint and `sub`-keyed tenant isolation. Tools are the primary surface (Claude connectors consume tools only). The server **must re-assert module ownership** at list+call time — the loader's `_bind_route` does not (`loader.py:1024`), a confirmed gap. Tool input schemas come from the live **`/openapi.json`** (kept fresh at `loader.py:619`), not from Pydantic models. The in-app assistant graduates from today's nav-only router (`ai_nav.py`) to an in-process MCP client. Two reviewer fixes are load-bearing: **Keycloak token-exchange is not enabled** (`docker-compose.yml:21` runs `["start", "--import-realm"]` with no `--features` flag), so the **dual-audience token** path is the primary in-app auth route, not token-exchange; and **open DCR is deferred** — launch with pre-registered confidential clients + Keycloak-owned consent (`registrationAllowed: false` today, `flows-funds-realm.json:6`).

The unifying thread: **one capability/registry abstraction** (today's `_UserScopedRegistry`, `loader.py:324-363`) is the source of truth for *which* modules a given `sub` may reach. Thread 1 hands modules to it regardless of implementation language; Thread 3 reads it to decide which tools to expose per user. Get that abstraction right and both the polyglot loader and the MCP tool layer fall out of it.

---

## 2. How the three threads relate

**Dependency order:**

1. **Thread 1 is the foundation for the *polyglot* form of Thread 2 but not its v1.** ProM ships first as a Python `isolation: subprocess` module (Shape A) that is an HTTP client to the ProM JVM — this needs **zero** loader or Thread-1 work. The *fully polyglot* ProM worker (a JVM that speaks the wire protocol directly, Shape B) rides Thread 1's `runtime` dispatch. So Thread 2 v1 is independent; Thread 2 v2 depends on Thread 1.
2. **Thread 3 (MCP) is largely independent of 1 and 2** but **shares the capability registry**. MCP needs no polyglot work and no ProM. It only needs the registry abstraction (already exists) and the OpenAPI schema (already exists). ProM and polyglot modules appear in MCP *for free* once they register capabilities, because they are ordinary modules to the registry.
3. **The shared spine is the capability/registry abstraction.** Both Thread 1 (which registers a module's handlers/capabilities regardless of language) and Thread 3 (which intersects the registry with `user_module_ids` to build the per-user tool set) read and write the same registry. This is the one cross-cutting component that must be designed coherently up front (§6).

**Sequencing diagram (text):**

```
                         ┌─────────────────────────────────────────────┐
                         │  SHARED SPINE: capability/registry           │
                         │  (_UserScopedRegistry, loader.py:324-363)    │
                         │  + live /openapi.json (loader.py:619)        │
                         └───────────────┬─────────────────────────────┘
                                         │ read/write
        ┌────────────────────────────────┼────────────────────────────────┐
        │                                 │                                 │
   THREAD 1                          THREAD 2                          THREAD 3
   Polyglot modules                  ProM module                       MCP server
        │                                 │                                 │
   [P0] freeze wire           (independent of T1 v1)              (independent of T1/T2)
        contract +            ─────────────────────────           ──────────────────────
        conformance harness          │                                 │
        │                       [P1] Shape A: Python                [P1] /mcp transport
   [P1] manifest `runtime`           subprocess module                  + PRM + auth bridge
        block + loader              = HTTP client to ProM JVM           (dual-audience)
        dispatch (T0)               (ZERO loader changes)               │
        │                            │                              [P2] static tools from
   [P2] build dispatch +        [P2] concurrency design:              /openapi.json
        one reference SDK            JVM pool / fair queue /            + re-assert ownership
        (Node)                       per-run isolation                 │
        │                            │                              [P3] dynamic per-module
   [P3] T1 hardening +          [P3] flagship: DPN decision           tools + list_changed
        ctx-gap closure              miner end-to-end                  │
        │                            │                              [P4] in-app assistant
        └──────────────► [v2] ProM as polyglot                          reuses MCP in-process
                         (Shape B) rides T1 runtime
                                          │
        DEFERRED OUT OF v1: T2/T3 (Docker/microVM), Arrow Flight,
        open DCR, Keycloak token-exchange, OCEL wire RPCs
```

The arrows that matter: the shared spine feeds all three; Thread 2 Shape B is the only hard cross-thread dependency (on Thread 1); everything else is independent and can be parallelized across sub-teams.

---

## 3. Thread 1 — Polyglot modules

### 3.1 Recommended mechanism and the decision-matrix verdict

**Verdict: promote the existing subprocess wire protocol to a documented, versioned polyglot contract. Do not adopt gRPC-as-baseline, GraalVM, WASM, or Docker for v1.**

Decision matrix across the researched options:

| Option | Maturity | Polyglot reach | DataFrame perf | Security default | Fit for Mate today | Verdict |
|---|---|---|---|---|---|---|
| **Promote existing wire protocol** (AF_UNIX + newline-JSON + Parquet-by-path) | Already in production for `agentsimulator` | High (any runtime with Unix sockets + a Parquet reader) | Strong (Parquet on shared FS, zero pickling) | Process isolation only (needs OS hardening added) | **Native** — only bootstrap/packaging is Python-specific | **ADOPT** |
| gRPC sidecar (go-plugin model) | Very high (Terraform/Vault/Nomad) | High | Poor unless paired with Arrow | Process-level, AutoMTLS available | Would replace a working transport for no new capability | Reject as baseline; borrow its *security blueprint* (SecureConfig checksums, per-call teardown) |
| Arrow Flight wire | High | High | Best-in-class (2-3 GB/s localhost, zero-copy) | n/a (data plane) | Solves a problem we don't have yet — Parquet-by-path already avoids the serialization tax | **Defer** to v2, only if throughput on a measured workload demands it |
| GraalVM polyglot / native-image | Real for JVM+JS/Python compute; **Oracle is de-emphasizing the Java/native-image story (Sept 2025)** | Narrow in practice | C-extension tax for pm4py/pandas; experimental, version-pinned | In-process, weak | Hostile to pm4py's native stack; ProM's `URLClassLoader` boot is closed-world-incompatible | **Reject** |
| WASM / Component Model | WASI 0.2 stable (Jan 2024), W3C Wasm 3.0 (Sep 2025) | High for glue logic | **Poor** — no production WASI threads, ~10x-slower BLAS, 4GB memory wall (or 2x penalty), JVM toolchains Beta | **Best-in-class sandbox** | Unfit for heavy-numeric process-mining core today | **Defer** — revisit for untrusted lightweight plugins when shared-everything-threads + WASI 0.3+ mature |
| Docker/microVM per call (T2/T3) | High | High | Good (gRPC/stdio/bind-mount) | Strong (gVisor) to gold-standard (Kata/Firecracker) | Introduces `docker.sock` (root-equivalent) to a product whose value prop is "no broker, no cloud, all on-disk"; only real use case (`agentsimulator`) is a trusted-ABI case, not untrusted code | **Defer out of v1** (see §3.5) |

**Why promote, not replace.** The research and both internal grounding briefs converge: the wire format, message framing, `ctx.*` method set, error/cancel conventions, the `ready` handler descriptor, and the Parquet data plane are *already* language-neutral. A Node/Java/Go worker that speaks newline-JSON over the Unix socket and reads/writes Parquet by path interoperates without changing a byte on the socket. Replacing this with gRPC buys nothing and discards a working, audited transport. Arrow Flight is the right *future* answer for bulk tabular transfer but is premature: Parquet-by-path already sidesteps the serialization tax that Flight exists to remove.

### 3.2 The language-neutral worker contract

This is the public contract a polyglot worker must implement. It is the existing Python realization, documented and frozen — **with one honesty correction the review demanded: the launch/argv shape is NEW surface, not "the existing protocol frozen."**

**Transport:** AF_UNIX stream socket, host is server, worker dials in. Newline-delimited UTF-8 JSON, one message per line. Stream buffer ceiling `RPC_STREAM_LIMIT = 256 MiB` (`subprocess_worker.py:37`) — match this flow-control constant.

**Message envelope:**
- Request: `{"id": int, "method": str, "params": dict}`
- Success: `{"id": int, "result": <json>}`
- Error: `{"id": int, "error": {"message": str, "traceback"?: str}}`
- Notification: `id: null` (only `ready`)
- Bidirectional; ids are local to each initiator (host id-7 and worker id-7 are distinct). Concurrent per-message dispatch.

**Launch contract (NEW — must be specified, not assumed):** Today `_spawn_worker` builds `cmd = [worker_py, worker_script, socket, folder]` (`subprocess_host.py:210-215`) and `subprocess_worker.main()` **hard-asserts `len(sys.argv) != 3` → `sys.exit(2)`** (`subprocess_worker.py:514`). The proposed `entrypoint: ["node", "dist/worker.js"]` followed by `<socket> <folder>` is a **new** convention. Required work: (a) define the argv shape as `[<entrypoint tokens...>, socket_path, module_folder]`; (b) **remove the `argc == 3` rigidity** in `main()` so the trailing two args are read positionally from the end, not asserted to be exactly positions 1-2. The docs must stop claiming "no new bytes / existing protocol frozen" for the launch path — it is genuinely new surface and gets its own spec section.

**Worker → host `ctx.*` callback surface (the 8 bridged methods today):**
`ctx.event_log.duckdb_fetch`, `ctx.event_log.materialize`, `ctx.bus.emit`, `ctx.cache.{get,set,exists,delete}`, `ctx.registry.call`, `ctx.progress.update`, `ctx.logger.log`, `ctx.cancel.check`. Each carries `ctx_token` so the host re-associates to the live `ModuleContext`.

**Handler discovery is neutral by result:** the worker emits a pure-JSON `ready` payload listing handlers (`{attr, route:{method,path,name}, on_event:{topic}, job:{...}}`); the host rebuilds specs from JSON (`subprocess_host.py:75-120`). A foreign worker *emits* this JSON directly and never needs Python decorator machinery. **Document the `ready` descriptor as the public contract.**

**Conventions a foreign author must replicate (review item #4 — budget this as the dominant cost, not a footnote):**
- 256 MiB line framing.
- Bidirectional id-spaced RPC including `fail_all_pending` semantics on peer death.
- The `Cancelled`-as-sentinel protocol: receive `{"error":{"message":"__ff_job_cancelled__"}}` → abort the handler; on own cancellation → send the same sentinel. **Note for porters:** Python's `Cancelled` is a `BaseException` (`errors.py`), a distinction with no clean analog in Go/Java/Node — this is the subtlest part of every port and must be called out explicitly in the contract.
- Logger RPC is **fire-and-forget** (`asyncio.create_task`, `subprocess_worker.py:357`) — a foreign worker must replicate non-blocking semantics or a logging burst serializes behind the RPC.
- `registry.has()` is answered locally from a **stale snapshot** shipped in `ctx_meta` — document it as snapshot, not live, so foreign authors don't assume liveness.
- `ctx.config` is a per-call immutable **snapshot** (no live reads).

**Invariant the worker must never violate:** `user_id` is **withheld** from the worker (`ctx_meta` omits it); all tenant enforcement stays host-side. A polyglot worker never sees or sets `user_id`.

### 3.3 Manifest and loader changes

**Manifest — add a `runtime` block under `Dependencies`:**

```yaml
dependencies:
  runtime:                      # NEW; absent ⇒ legacy Python (full backward compat)
    kind: python | node | jvm | container
    entrypoint: ["node", "dist/worker.js"]   # launch tokens; socket+folder appended
    build: ["npm", "ci"]                      # runtime-specific build step
    trust_tier: trusted | untrusted           # gates secret exposure (§3.5)
  python: { ... }               # unchanged; legacy path
  isolation: subprocess         # unchanged
```

**Backward compatibility is free:** absent `runtime` ⇒ legacy Python venv path, exactly as today. **The venv cache key needs no work** — `dependencies_hash()` does `self.dependencies.model_dump(by_alias=True)` (`manifest.py:270`), so a `runtime` sub-block under `Dependencies` is folded into the hash automatically. (Review correction: the standalone design overstated this as work to do; it is not.)

**Loader changes (two dispatch points):**
1. `installer.install_module` must dispatch on `runtime.kind`: today it always does `uv venv` + `uv pip install` and forcibly installs `mate.sdk` (`installer.py:242-266`). For `node` → `npm ci`; `jvm` → build/resolve a jar; `container` → `docker build` (deferred, §3.5). The **skip/cache rule** (`.installed-hash`) generalizes: hash matches AND the runtime's built artifact exists.
2. `_instantiate` (`loader.py:915-927`) currently branches only on `isolation == "subprocess"`. It must additionally consult `runtime.kind` so `SubprocessBridge._spawn_worker` launches the declared `entrypoint` instead of `_worker_python`. **The duck-typed `SubprocessModule` binding is unchanged** — `_bind` (`loader.py:1002-1022`) only reads `dir(instance)` + decorator metadata reconstructed from the JSON `ready` payload, so routes/jobs/events mount identically regardless of worker language. This is the load-bearing reason promotion is cheap.

### 3.4 Data transfer for large event logs

**Reads (host → worker):** already solved. `pandas()`/`polars()`/`pm4py()` cross as **Parquet files on the shared filesystem** addressed by path (`subprocess_host.py:393-403`). A foreign worker reads the same Parquet with its own Arrow/Parquet reader. Document the pm4py column renames (`case:concept:name`, `concept:name`, `time:timestamp`, `subprocess_worker.py:204-219`) so a foreign worker that wants pm4py-shaped frames can replicate them; a non-Python worker simply omits the `pm4py()` helper.

**Writes/results (worker → host) — review item #3, a real OOM/correctness hazard, adopted as a hard rule.** The deeper issue the standalone design under-stated: any worker-*produced* result still rides the socket as a single JSON line bounded only by 256 MiB:
- `ctx.cache.set` passes `params["value"]` over the socket as JSON (`subprocess_host.py:415`) — a worker **cannot** cache a DataFrame directly.
- `event_log_duckdb_fetch` returns fully-materialized rows as JSON (`subprocess_host.py:387-391`) — a 500 MB `duckdb_fetch` hits the 256 MiB wall and tears down the connection.
- `@job` return values cross as JSON.

**Mandatory v1 convention:** make the **Parquet-by-path data plane symmetric and mandatory for large results.** Add a worker→host "write Parquet to `workdir`, return the path" path for: (a) `cache.set` with tabular values, and (b) large `@job`/`duckdb_fetch` results. Cap and stream the JSON control plane: enforce a size ceiling on any single JSON line well below 256 MiB and reject/redirect oversized payloads to the Parquet path rather than letting them OOM or tear down the socket. This is not an optional nicety; it is the answer to "data-transfer bottleneck for large event logs" and must land in v1.

### 3.5 Security and sandboxing — the corrected model

**The headline fix (review item #1, blocking): secrets must never reach a non-trusted-tier worker.** The host ships `ctx_meta["config"] = ctx.config.value` verbatim (`subprocess_host.py:360`), and that config is `ModuleConfig.config_json`. A module declaring `ai_models.self_hosted: true` persists the user's OpenAI/API key under `config_json["ai"]` (`manifest.py:165,234`), and `concept_drift_explainer` already uses `ai_models`. So today a worker is handed the current tenant's secrets and can exfiltrate them via its result/bus/cache channels — even a `--network none` worker can leak through those channels. The "compromised worker cannot reach another tenant" claim was over-stated: the within-tenant secret surface is the real exposure.

**Mandatory invariants (add to §2.3 of the polyglot design before any of this lands):**
1. **Secrets/config never cross to a non-trusted-tier worker.** Introduce **config redaction**: for `trust_tier: untrusted`, the host strips secret-bearing keys (anything under `config_json["ai"]` and any key flagged secret in the manifest's `config_schema`) before building `ctx_meta`. A capability-scoped secret model (worker requests a named secret via an audited RPC the host can deny) is the longer-term shape; redaction is the v1 floor.
2. **`user_id` stays host-side**, as today.
3. **The bus `user_id` force-stamp stays host-side** (`loader.py:143`) — a worker cannot address another tenant.

**Two isolation tiers for v1:**
- **T0 — current behavior.** Subprocess in its own process group (`start_new_session=True`), `0o600` socket, `killpg` hard-cancel. Trusted modules only. This is exactly what `agentsimulator` runs on today.
- **T1 — T0 + OS hardening.** Add per-worker rlimits (memory, CPU, FDs, PIDs — fork-bomb protection), a scoped read-only-where-possible workdir, and egress restriction (`--network none`-equivalent via namespace or seccomp) for `trust_tier: untrusted`. T1 is the security floor for any future untrusted module and is achievable without Docker.

**Deferred out of v1 (review item #5, adopted): T2/T3 (Docker `docker.sock` / Kata-Firecracker microVM).** Introducing `docker.sock` turns the orchestrator into a **root-equivalent component** on a single-host, self-hosted product whose whole value prop (per CLAUDE.md) is "no broker, no cloud, all on-disk" — read-only socket mounting is security theater (`send()` bypasses filesystem perms), and even DinD needs `--privileged`. The socket-proxy mitigation (Tecnativa, scoped to `containers`/`images`/`exec`) is the correct pattern *if* we ever ship it, behind a dedicated orchestrator service that is the only component touching Docker. But there is **no untrusted-module marketplace today**; the sole subprocess module (`agentsimulator`) is a *trusted dependency-ABI* case, fully served at T0. The entire sandbox-tier apparatus is speculative until a third-party module catalog exists. When that day comes, the research's tiering applies: rootless+hardened for our own modules, gVisor for untrusted-but-curated, Kata/Firecracker for arbitrary third-party code.

### 3.6 Migration keeping current Python modules intact

- **Absent `runtime` ⇒ legacy.** Every one of the 12 bundled modules (all `isolation: in_process` except `agentsimulator`) is untouched. In-process modules never go near the worker contract.
- **`agentsimulator` is the canonical T0 proof.** It already runs as `isolation: subprocess`. It stays Python; adding a `runtime: {kind: python}` block is a no-op that documents the existing behavior.
- **No byte changes to the socket protocol** for existing workers. Only the *launch dispatch* and *installer dispatch* are new code paths, gated behind `runtime.kind != python`.
- **`SubprocessModule` duck-typing is unchanged**, so a polyglot module's routes/jobs/events appear in the loader, the registry, and `/openapi.json` exactly like a Python module's.

---

## 4. Thread 2 — ProM module

### 4.1 Recommended hosting/invocation approach

**Selective wrapper around 3-4 high-value ProM-only plugins, exchanging XES/PNML, over a single long-lived out-of-process Java 8 JVM behind a ProM4Py-style REST shim. Not a generic bridge.**

Rationale, confirmed by research: pm4py has *closed* the historical gaps (it now ships a genetic miner, DECLARE discovery/conformance, and full OCEL 2.0). The residual ProM-only value is concentrated and Java/GUI-bound — exactly the wrong shape for a generic bridge but a good fit for a few targeted wrappers. RapidProM is the cautionary precedent: the only generic-bridge attempt still wraps plugins individually *and* drags in the full ProM runtime. Manual JAR extraction is documented to fail/diverge (the TU/e forum thread).

**Topology:** one long-lived ProM 6.15 (or ProM Lite) install on **Java 8** (its `URLClassLoader.addURL` boot hack is illegal on Java 9+ without `--add-opens` and blocked around Java 17; GraalVM native-image is a non-starter for the same closed-world reason). Boot it once via the CLI/scripting context or a `ProM4PyContext`-style REST shim (`/cache` to stage objects, `/plugin` to call any CLI plugin by name). Mate's Python module talks HTTP to it. Data path: DuckDB → Parquet (host→worker) → XES (worker → shared volume) → ProM parse; results (PNML/Petri nets) back via the shared volume.

### 4.2 What to surface first (highest-value plugins)

In priority order (research-grounded):
1. **Data-aware Decision Mining → Data Petri Nets** — the single strongest ProM-only capability (transition guards/decision rules; alignment-based, Weka J48). **This is the flagship.**
2. **DECLARE depth (discovery + online monitoring)** — but evaluate pure-Python **Declare4Py** first; it likely beats a ProM bridge on integration cost for everything except ProM's online-monitoring plugin.
3. **Organizational / Social-Network mining (5 metrics:** handover, subcontracting, working-together, similar-task, reassignment) — cheap to wrap, pm4py is thin here.
4. **Fuzzy Miner / ETM** — only on concrete user demand.

**Explicitly do NOT bridge:** OCEL/object-centric (pm4py at parity-or-ahead via `discover_oc_petri_net` etc.) and mainstream discovery/conformance (pm4py covers, incl. genetic miner). No bridge value there.

### 4.3 Concurrency and tenant isolation — the blocking gap (review item #1)

**This must be designed before any plugin ships.** A `SubprocessBridge` runs **one** worker per module shared across all users (`_proc` is a singleton); the proposed `mate-prom` is likewise one long-lived JVM. ProM's flat mutable classloader and stateful `GlobalContext` are not built for parallel plugin runs. Two consequences the standalone design never addressed:
- **No isolation between concurrent users:** two users decision-mining at once contend on one JVM with a shared object pool.
- **Cross-user cancel kills innocents:** `cancel_active()` does `killpg` on the whole worker process group and respawns (`subprocess_host.py:228`). If User A cancels, the shared worker dies and User B's in-flight run dies with it. This is a tenant-isolation defect in a platform whose CLAUDE.md treats tenant isolation as an invariant.

**Required design (new "Concurrency & tenant isolation" section in the ProM design doc):**
- **Per-run object-cache isolation inside `mate-prom`** — each plugin invocation gets a fresh child `PluginContext`/object scope, with provided-objects cleared between runs (budget for OOM/restart cycling as the *normal* case, since a headless `GlobalContext` surviving many sequential runs without leaking is non-trivial — ProM4Py is a research artifact, not a hardened service).
- **A bounded job queue with fair per-user scheduling** in front of the JVM (or a small JVM pool).
- **Decouple ProM-run cancellation from worker `killpg`:** a single user's cancel must abort only their ProM run (cooperative cancel inside `mate-prom` keyed by run id), never the shared worker process. The hard `killpg` path is reserved for worker death/respawn, not user-initiated cancel.

### 4.4 Auto-update mechanism

Poll ProM's package repository `http://www.promtools.org/prom6/packages/packages.xml` (plain XML, no auth; QUT mirror `http://sefmining01.qut.edu.au/Packages/packages.xml` for failover), diff `name`+`version`, and drive `PackageManager.update()` / `findOrInstallPackages()` headlessly. Reuse Mate's existing precedents: the `scan_watch` poller pattern and the `model_store` pin/checksum/staged-apply safety model. **Safety defaults (review item #2, hardened):** **`auto_update: off` by default**; mirror-as-redistribution is a license gate (each package's `license=` field honored individually); pin + checksum + staged apply; never auto-pull into a running JVM without a controlled restart.

### 4.5 Licensing stance — demoted to pending legal review (review item #2)

The standalone design's "GPL SaaS loophole" claim is **overstated and must be softened**. Directionally, pure *network use* of GPL (not AGPL) ProM does not trigger copyleft. But Mate simultaneously proposes a Python worker tightly driving ProM, **mirroring/redistributing package zips**, and shipping appliance images — and the boundary between "arm's-length REST host" and "derivative/combined work" turns on facts the design hand-waves (Is `mate-prom` distributed with Mate? Does tight coupling to specific plugin signatures constitute a combined work?).

**Stance:**
- **Every licensing conclusion is an "engineering assumption pending legal review."**
- **Hard defaults (not recommendations):** `auto_update: off`; **never bundle the GPL ProM core in a shipped/appliance image**; out-of-process REST (ProM4Py-style, MIT shim) keeps client code at arm's length from GPL JARs.
- Note for completeness: pm4py is already GPL-3.0 and a pre-existing dependency (`apps/api/pyproject.toml`); ProM does not change that posture, but it compounds the copyleft footprint, reinforcing "host, don't ship."

### 4.6 How it maps onto Thread 1

- **Shape A (v1):** ProM module is an ordinary Python `isolation: subprocess` module that is an HTTP client to the ProM JVM. **Zero loader changes** — verified: the loader does not need to change for this. This is independent of Thread 1.
- **Shape B (v2):** ProM as a polyglot `runtime: {kind: jvm}` worker that speaks the wire protocol directly, eliminating the Python HTTP-client hop. **This rides Thread 1's `runtime` dispatch.** Defer to v2.
- **OCEL is out of v1** for ProM regardless of shape: there are **no `ctx.object_log.*` wire RPCs** (only case-centric `event_log` is bridged, `subprocess_host.py:451-463`). Honestly grounded, correctly deferred.

### 4.7 Data-transfer ceiling (review item #4)

The path includes **uncompressed XES** (worker → ProM), an order of magnitude larger than Parquet and slow to parse. For a multi-million-event flagship decision-mining run, throughput matters in v1. **Required:** specify a **size ceiling / sampling policy** for v1 (a `min_events`/`min_cases` floor exists but no ceiling), and **measure XES write+parse on a large log in P0 as a gating metric.** Arrow Flight remains a v2 option only if measured throughput demands it.

---

## 5. Thread 3 — MCP server

### 5.1 Transport

**Streamable HTTP**, the only standard transport for a remotely-hosted multi-client server (the old HTTP+SSE transport is deprecated). One endpoint at `/mcp` supporting POST and GET; SSE is an optional response mode *within* it, not a separate choice. **stdio is not applicable** (single-user, local).

Operational requirements:
- **Validate the `Origin` header on every connection** (DNS-rebinding defense).
- **Sessions are not authentication** — authenticate every inbound request from the token, not `Mcp-Session-Id`. Bind session data as `<user_id>:<session_id>` where `user_id` derives from the validated token, never from the client.
- **CORS ordering (review item #6, blocking):** Mate's app-level `CORSMiddleware` (`main.py:~357`, `allow_methods=["*"]`, `allow_credentials=True`) wraps any `app.mount("/mcp", ...)`. The mount does **not** automatically get its own stricter rules. The `/mcp` ASGI sub-app must do **independent Origin validation** and the plan must ensure the permissive app-level CORS does not undercut the MCP allowlist. Make middleware ordering explicit.

### 5.2 Tool/resource surface incl. dynamic per-module tools

**Tools are the primary surface.** Claude's hosted connectors consume **tools only** — resources and prompts are usable only by an in-app client we control. So everything externally reachable is a tool.

**Input schema source (review item #1, blocking correction):** schemas come from the live **`/openapi.json`** route schema, **not** "existing Pydantic request/response models." Only ~12 route files import Pydantic against ~48 `Query()` usages; most read tools (`mate.logs.events/variants/...`) take FastAPI query params with no Pydantic body to dump. The OpenAPI schema is the single source of truth and is kept fresh (`app.openapi_schema = None` on each module load/unload, `loader.py:619`).

**Tool catalog:**
- **Static platform ops** from `/openapi.json` (event logs, folders, dashboards, jobs, watched folders, AI, preferences; admin ops gated behind the `admin` realm role).
- **Dynamic per-module tools** from `loader.manifests()` provides/consumes + the same `/openapi.json`. Per-user visibility = intersect with `user_module_ids` and `ModuleConfig.enabled`, exactly as `build_user_destinations` (`ai_nav.py:228`) already does for nav. **ProM and polyglot modules appear here for free.**

**Dynamic registration:** declare `"tools": {"listChanged": true}`; emit `notifications/tools/list_changed` on that session's stream when a tenant enables/disables a module or grants change. **But never depend on clients honoring it** — design so a fresh connection always yields the correct per-user set, and treat live `listChanged` as an enhancement. Resolve `tools/list` from the **validated token's** tenant/scopes, not session state (the spec flags `list_changed` as a session-hijack abuse vector).

**External result-fetch path (review item #4, blocking — the real large-log/ProM bottleneck for external clients):** external tools-only clients **cannot dereference resources**, but several real outputs are *references*: job results via `result_url`, ProM outputs as PNML/Petri-net files on a shared volume, async tools that "return a reference to" a cache entry. An external LLM that gets `{job_id}` or `{result_uri}` and cannot read resources is stuck. **Required:** an explicit **tools-only result-fetch tool** (e.g. `mate.jobs.result`) that inlines or summarizes terminal/file-shaped results. `mate.jobs.wait` partly handles jobs; the terminal payload needs its own tool.

**Aggregation-first surface (recommended adjustment):** read tools inherit the existing 500/200/1000 caps (`event_log_data.py`), so a model analyzing a large log otherwise makes hundreds of paged `tools/call` round-trips — a real token+latency cost. Make **server-side aggregation tools the default external surface** (variants, activity stats, data-quality summaries already exist) and gate raw `events` paging behind `defer_loading`.

### 5.3 The Keycloak ↔ OAuth 2.1 auth bridge

The MCP server is an **OAuth 2.1 Resource Server** (validates tokens, never issues them); **Keycloak is the Authorization Server**, reusing Mate's existing realm.

**Flow:**
1. No token → **401** with `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"` + a `scope=` hint.
2. Serve **RFC 9728 Protected Resource Metadata** unauthenticated at `/.well-known/oauth-protected-resource`: `{resource: <mcp-url>, authorization_servers: [<realm-issuer>], scopes_supported: ["mcp:tools"]}`.
3. Keycloak provides RFC 8414 AS metadata / OIDC discovery, Authorization Code + PKCE (S256), and DCR — out of the box against the existing realm.
4. **The RFC 8707 gap (still absent in Keycloak 26.5):** Keycloak does not natively understand the `resource` parameter. **Workaround (matches an existing realm pattern):** define client scopes `mcp:tools` (etc.), add an **Audience protocol mapper** with `Included Custom Audience = <mcp-server-url>` to each, set it as a realm default scope so DCR clients inherit it. The realm *already ships* an `oidc-audience-mapper` for `flows-funds-api` (`flows-funds-realm.json:65`) — this replicates a working pattern.
5. **Resource-server validation:** the design's "reuse `_decode_token` verbatim with a different audience" is **wrong** (review item #2) — `_decode_token` (`dependencies.py:~108`) hard-codes `audience=settings.keycloak_audience`. A second resource server needs a **parameterized audience** (or a second decode path). Validate every token locally via the realm JWKS (`auth/jwks.py`): signature, `iss`, **`aud` contains the MCP URL**, required scope, `exp`.

**In-app auth path (review item #3, blocking — token-exchange is not free):** the standalone design *recommended* Keycloak token-exchange as the in-app path, but **token-exchange is a preview/feature-gated capability and is NOT enabled** — `docker-compose.yml:21` runs `["start", "--import-realm"]` with **no `--features` flag**. **Decision: the dual-audience token is the primary in-app path** (the in-app client already holds the user's Keycloak session and can present a token audience-bound to both the API and the MCP server). Token-exchange becomes an optional later optimization with its own P0 enabling+hardening task and risk row, not a v1 dependency.

### 5.4 Security defenses

- **Audience binding is the #1 rule:** accept only tokens with the MCP URL in `aud`; reject all others.
- **No token passthrough:** if the MCP server ever calls an upstream API, it acts as a separate OAuth client and must not forward the client's token (explicitly forbidden by the spec).
- **Re-assert module ownership (confirmed Mate gap):** `_bind_route` pops `__ff_user` and calls `_make_context` with **no `user_owns_module` check** (`loader.py:1024`); `user_owns_module` exists only on meta-routes (`modules.py`). The MCP tool layer **must re-assert ownership at list AND call time**, via the per-user `_UserScopedRegistry`/`user_module_ids`, *without* depending on a loader fix. This is the central security finding and is correctly scoped.
- **DCR deferral (review item #5, adopted):** "Adopt DCR now" contradicts its own High-severity confused-deputy risk and requires relaxing the realm's `registrationAllowed: false` + Trusted-Hosts policy. **Launch external connectors with manually pre-registered confidential clients per partner + Keycloak-owned consent.** Gate open DCR behind the confused-deputy hardening (per-client consent storage checked before the flow, exact `redirect_uri` matching, `__Host-` signed consent cookies, `state` set only after consent).
- **Least-privilege step-up:** minimal baseline scope; 403 + `WWW-Authenticate: insufficient_scope` step-up for privileged tools. Pairs with the per-module Keycloak scopes.
- **Tenant-neutral tool metadata:** never let user-supplied content define tool names/descriptions/annotations; scope tool *results* strictly to the caller's tenant.
- **Demo mode is non-production:** the `demo-access-token` bypass (`dependencies.py:53`) must be refused by the MCP server.
- **Greenfield volume (review item #8):** no MCP code exists today. The "reuse verbatim" framing undersells a substantial net-new surface: full Streamable-HTTP JSON-RPC server, PRM endpoint, 401/step-up state machine, in-process MCP client, `list_changed` bus wiring. Budget accordingly.

### 5.5 How the in-app assistant reuses it

Today's assistant (`ai_nav.py`) is **router + advisor only** — it navigates the browser and toggles a whitelisted set of client-side settings (`SETTING_WHITELIST`); it cannot mutate platform state. The in-app LLM loop already runs **server-side** with provider keys (`ai_nav.py`/`routes/ai.py`), so an **in-process MCP client** is feasible: it consumes tools/resources/prompts directly (no HTTP loopback, no token passthrough), reusing the same tool implementations the external server exposes. This is how the assistant graduates from "advisor" to "actor" — but the MCP server **must still validate audience exactly** for in-app calls (don't trust the in-app client more than an external one).

---

## 6. Cross-cutting concerns

### 6.1 Unified security model

The two sandboxing problems (untrusted-module isolation in Thread 1, MCP scoping in Thread 3) share one principle: **trust is enforced host-side, and the executing party never holds the keys to escalate.**

- **Tenant isolation is the invariant both threads inherit:** `sub`-keyed ownership with **404-on-mismatch** (`auth/ownership.py`, to avoid id enumeration), the bus `user_id` force-stamp (`loader.py:143`), `_UserScopedRegistry` hiding capabilities the user hasn't installed, and `user_id` withheld from subprocess workers. Thread 1 keeps these by never shipping `user_id` to a worker; Thread 3 keeps them by deriving identity from the token, not the session.
- **Secret containment is the new invariant Thread 1 adds:** secrets/config never cross to a non-trusted-tier worker (§3.5). This closes the intra-tenant exfiltration gap.
- **Module-ownership re-assertion is the gap both threads must close:** the loader's `_bind_route` skips `user_owns_module` (`loader.py:1024`). Thread 3 re-asserts it at the MCP layer; Thread 1's polyglot workers inherit the same host-side gate because binding is unchanged.
- **Layered confinement maps to trust tier:** T0 (process group) for trusted, T1 (OS hardening: rlimits, egress restriction) for untrusted-curated, gVisor/Kata deferred for arbitrary third-party code. MCP's least-privilege step-up scopes are the request-layer analog of the same tiering.

### 6.2 The capability/registry abstraction both rely on

The `_UserScopedRegistry` (`loader.py:324-363`) + live `/openapi.json` (`loader.py:619`) are the **single source of truth** for "which capabilities does this `sub` have." Design rules so both threads compose cleanly:
- A module registers handlers/capabilities **regardless of implementation language** (Thread 1 reconstructs specs from the JSON `ready` payload; the registry never sees the language).
- The MCP tool layer (Thread 3) **reads** the same registry to build the per-user tool set and re-asserts ownership against it.
- ProM (Thread 2) is an ordinary registry entry; its capabilities (e.g. `prom.decision_mining`) appear in MCP for free.
- `registry.has()` is a **snapshot** at call time, not live — document this so neither a polyglot worker nor the MCP layer assumes liveness mid-session.

### 6.3 Performance

- **Reads:** Parquet-by-path (Thread 1) and the existing 500/200/1000 read caps (Thread 3) are already efficient. No data-plane change needed for reads.
- **Writes/results:** the mandatory symmetric Parquet convention (§3.4) removes the 256 MiB JSON-line OOM hazard. This is the single highest-leverage Thread-1 perf fix.
- **ProM:** uncompressed XES write+parse is the bottleneck; gate with a size ceiling and measure in P0 (§4.7).
- **MCP agent loop:** aggregation-first tools (§5.2) avoid hundreds of paged round-trips on large logs.
- **Arrow Flight** is the documented future answer for bulk Python↔Java transfer (2-3 GB/s localhost, zero-copy) — adopt only if a *measured* workload demands it, not preemptively.

### 6.4 Ops and maintenance

- **Worker-SDK fan-out is the dominant Thread-1 cost** (review item #4), not a convenience. Each language SDK re-implements line framing, id-spaced RPC with `fail_all_pending`, the `Cancelled`-as-BaseException sentinel (no clean Go/Java/Node analog), the `ready` descriptor, and an Arrow/Parquet reader matching Mate's column renames. **Ship exactly one reference SDK (Node) in v1**; the conformance harness (P0) is itself a substantial deliverable, not a footnote.
- **ProM is a long-lived stateful JVM** — budget OOM/restart cycling as the normal operational case (§4.3).
- **MCP is greenfield** — budget the full net-new surface (§5.4).
- **Implementation trap (flag loudly):** the repo root `apps/`/`packages/` is the **old `flows_funds` namespace**; the `mate`-namespaced tree lives under `FandFPMIF/`. All three threads target `FandFPMIF`. It is easy to edit the wrong file.

---

## 7. Phased roadmap

Phases are ordered to retire the highest risk earliest. Each phase states what it delivers and the key risk it retires. **No code in this document.**

### Phase 0 — Spikes that retire the biggest unknowns (parallel across threads)

**T1.P0 — Freeze the wire contract + build the conformance harness.**
Delivers: a versioned, documented worker-contract spec (transport, envelope, **new launch/argv shape**, `ctx.*` method table, `ready` descriptor, cancel sentinel, snapshot semantics) and a conformance test harness that any worker (starting with the existing Python one) must pass.
Retires: the risk that the protocol isn't actually neutral, and the false "no new bytes" framing — by writing the launch contract down explicitly and removing the `argc==3` assumption.

**T2.P0 — Prove the flagship, not a soft target.**
Delivers: the **Data-Petri-Net decision miner** end-to-end through a headless ProM JVM (XES in, Data-PNML out), AND a measured XES write+parse benchmark on a large log as a **gating metric**.
Retires: the two unknowns every ProM-in-production effort fails on — that the flagship multi-input data-aware plugin likely needs a **custom endpoint** (not the generic `/plugin`), and that uncompressed-XES throughput may be unviable. If DPN needs bespoke wiring (likely), we learn it now.

**T3.P0 — Auth bridge spike + CORS/transport decisions.**
Delivers: a working PRM endpoint + audience-mapper scope + parameterized-audience token validation against the existing realm; an explicit decision to use **dual-audience tokens** as the in-app path (token-exchange deferred); confirmation that the `/mcp` mount overrides app-level CORS with its own Origin allowlist.
Retires: the token-exchange-not-enabled risk and the CORS-inheritance risk before any tool surface is built.

### Phase 1 — First usable deliverables

**T1.P1 — Manifest `runtime` block + T0 launch dispatch.** Loader dispatches launch on `runtime.kind`; absent ⇒ legacy. `agentsimulator` keeps running unchanged. Retires: backward-compat risk (proves legacy path is untouched).

**T2.P1 — ProM Shape A (Python subprocess module = HTTP client to the JVM), DPN only.** Zero loader changes. Retires: "can ProM be a normal Mate module" risk — answered yes, no loader work.

**T3.P1 — `/mcp` transport + auth + a read-only static tool slice.** Streamable HTTP, PRM, dual-audience validation, `Origin` checks, a handful of aggregation read tools from `/openapi.json`. Retires: greenfield-transport risk.

### Phase 2 — Hardening the foundations

**T1.P2 — Build dispatch + one reference SDK (Node).** `install_module` dispatches on `runtime.kind`; a Node worker passes the conformance harness. Retires: the SDK-fan-out cost risk (proves a non-Python worker interoperates).

**T2.P2 — Concurrency & tenant-isolation design, implemented.** Per-run object-cache isolation in `mate-prom`, bounded fair queue, **cancel decoupled from `killpg`**. Retires: the multi-tenant JVM defect (the blocking concurrency gap).

**T3.P2 — Static tools complete + ownership re-assertion + result-fetch tool.** Full static surface; `user_owns_module` re-asserted at list+call; a `mate.jobs.result` tools-only fetch path. Retires: the module-ownership gap and the external-client dead-end for reference results.

### Phase 3 — Dynamic, polyglot, and secure

**T1.P3 — T1 OS hardening + ctx-gap closure + mandatory Parquet results.** rlimits/egress restriction; **config redaction for untrusted tier (secret containment)**; symmetric Parquet for `cache.set`/large results; close the documented ctx gaps as needed (`object_log`, `open_event_log`, `bus.subscribe`). Retires: the intra-tenant secret-exfiltration gap and the large-result OOM hazard.

**T2.P3 — Flagship DPN to GA + 2nd/3rd plugin.** DPN hardened; add social-network mining (and DECLARE only after the Declare4Py vs ProM bake-off). Retires: "is the selective-wrapper thesis productizable" risk.

**T3.P3 — Dynamic per-module tools + `list_changed` + in-app assistant.** Per-user tool resolution from the token; aggregation-first external surface; in-process MCP client wired into the assistant so it becomes an actor. Retires: per-tenant dynamic-tool correctness risk.

### Phase 4 — Convergence

**T1.P4 — ProM Shape B (polyglot `runtime: {kind: jvm}`)** rides Thread 1, eliminating the Python HTTP hop. Retires: the only hard cross-thread dependency, last, when both sides are mature.

### Deferred out of v1 (explicitly)
T2/T3 Docker/microVM sandbox tiers; Arrow Flight; open DCR; Keycloak token-exchange; OCEL wire RPCs / ProM OCEL; per-language SDKs beyond Node. Each is revisited only when a concrete demand (untrusted-module marketplace, measured throughput wall, third-party connector partner) retires its speculative status.

---

## 8. Open questions for the maintainer

These need a human call before or during P0:

1. **Untrusted modules — real or hypothetical?** The entire T1/T2/T3 sandbox apparatus, config redaction, and gVisor/Kata tiering is speculative until a third-party module catalog exists. Is one planned? If not, we ship T0 only and keep config redaction as the sole untrusted-tier control. (Drives §3.5 scope.)

2. **Secret model: redaction or capability-scoped?** v1 floor is stripping `config_json["ai"]` and manifest-flagged secrets from untrusted-tier `ctx_meta`. Is an audited "worker requests a named secret, host can deny" RPC worth building in v1, or is redaction sufficient? (Drives §3.5.)

3. **ProM licensing — legal review trigger.** Every licensing conclusion is "pending legal review." Do we have counsel sign-off on: (a) hosting GPL ProM out-of-process, (b) mirroring package zips, (c) shipping appliance images that *exclude* the core? Until then, `auto_update: off` and "never bundle the core" are hard defaults. (Drives §4.5.)

4. **First reference SDK language.** Node is recommended (lowest friction, broad reach). Is Java more valuable first, given ProM Shape B will eventually want a JVM worker anyway? (Drives T1.P2.)

5. **DECLARE: Declare4Py vs ProM.** Pure-Python Declare4Py likely beats a ProM bridge except for online monitoring. Do we need ProM's online-monitoring plugin specifically, or is Declare4Py sufficient? (Drives §4.2 priority 2.)

6. **MCP external partners — who, and how many?** Pre-registered confidential clients per partner is the v1 path. How many partners, and is the operational cost of per-partner registration acceptable vs. accelerating open-DCR + confused-deputy hardening? (Drives §5.4.)

7. **In-app assistant scope expansion.** Graduating the assistant from advisor to actor (in-process MCP client) lets it mutate platform state — exactly what `ai_nav.py`'s `SETTING_WHITELIST` deliberately forbids today. What is the intended action surface, and what human-in-the-loop confirmation is required? (Drives §5.5.)

8. **ProM throughput ceiling policy.** If P0 shows uncompressed-XES write+parse is too slow at scale, do we (a) impose a hard event/case ceiling with sampling, (b) accept Arrow Flight complexity earlier, or (c) limit ProM to smaller logs by design? (Drives §4.7.)
