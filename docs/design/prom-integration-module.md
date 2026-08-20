# Design: ProM Integration Module for Mate (with auto-update)

Status: PROPOSAL / architecture planning. No code is changed by this document.
Audience: Mate platform maintainers + module authors.
Scope: a flagship non-Python module that surfaces ProM functionality inside Mate without losing the body of research already implemented as ProM plugins.

All Mate paths are relative to `/Users/t_zimm11/Code/mate/FandFPMIF`.

---

## 0. TL;DR / Recommendation

1. **Selective wrapping, not a generic bridge.** Ship one Mate module, `prom`, that exposes a *small, curated* set of high-value ProM plugins (Data-Petri-Net decision mining first; then organizational/social-network mining; DECLARE and Fuzzy/ETM gated on demand). Do **not** mirror ProM's ~338 packages into Mate's UI. pm4py has closed the genetic-miner, DECLARE, and OCEL gaps, so the generic-bridge payoff has shrunk to a few niche, Java/GUI-bound capabilities ([pm-ecosystem research]; pm4py CHANGELOG https://raw.githubusercontent.com/pm4py/pm4py-core/release/CHANGELOG.md). RapidProM is the cautionary precedent: a "generic bridge" still ends up wrapping plugins one-by-one *and* drags in the full ProM runtime (https://www.promtools.org/doku/rapidprom/home.html, https://arxiv.org/abs/1703.03740).

2. **Host ProM out-of-process behind a tiny REST/RPC surface, in its own JVM-8 container.** ProM mutates the system classloader at boot (`Boot.boot()` reflectively calls `URLClassLoader.addURL`), needs Java 8, and is not embeddable in a non-Java host classloader ([prom-architecture research], `Boot.java`; GraalVM is a non-starter for ProM — [graalvm-polyglot research]). The clean pattern is a **long-lived ProM JVM that boots once and exposes a ProM4Py-style HTTP surface** (`ProM4PyContext`, MIT, 2–4× faster than pm4py, separate process — https://www.vdaalst.com/publications/p1495.pdf). Exchange **XES in / PNML+results out** on a shared filesystem, exactly as both tools already speak ([pm-ecosystem research]).

3. **Ride the existing subprocess-isolation boundary, extended for a non-Python runtime.** The `prom` module is the **flagship non-Python module**: a Mate "worker" that is a thin Python shim today (so it ships with zero loader changes), with a documented path to a true polyglot runtime later. The Mate↔worker wire is unchanged JSON-RPC over a Unix socket with Parquet handoff (`subprocess_host.py` / `subprocess_worker.py`); the *worker*↔*ProM JVM* leg is the new HTTP boundary, internal to the module.

4. **Auto-update by polling the ProM package repository**, diffing `name`+`version`, mirroring zips into platform storage, and re-pointing a pinned local `packages.xml` — modeled on Mate's existing watched-folder poller (`apps/api/src/mate/api/ingest/watch.py::scan_watch`) and the `model_store` large-binary convention (cv4cdd). Default to **pinned ProM 6.15**; auto-pull is opt-in, staged, and never auto-enables a new plugin in the UI.

5. **Licensing: host, don't ship the GPL core.** The ProM 6 core is GPL; packages are typically L-GPL but per-package (`license=` attribute). Out-of-process REST keeps Mate's code at arm's length from the GPL link boundary; pure self-hosting (SaaS) does not trigger classic GPL network-use copyleft, but **shipping a Mate appliance/VM image that bundles the ProM core does** ([prom-headless-update-license research]). Mirroring a package zip is itself a redistribution act under that package's license.

---

## 1. Decision: generic bridge vs selective wrapping

### 1.1 Decision matrix

| Criterion | Full generic ProM bridge | **Selective plugin wrapping (RECOMMENDED)** |
|---|---|---|
| Coverage | ~338 packages, hundreds of plugins | 3–4 curated plugins, grows on demand |
| Build cost | Per-plugin glue anyway (RapidProM proves this) + a generic typed schema | Per-plugin glue only for the few you surface |
| Mate UX fit | Mate panels are bespoke per capability (see `modules/discovery/panel/`); a generic catalog has no panel | Each wrapped plugin gets a real Mate panel + widgets |
| pm4py overlap | High — duplicates discovery/conformance/genetic/DECLARE/OCEL pm4py already has | Low — only fills genuine pm4py gaps |
| Failure surface | Every plugin's serialization quirks (ProM4Py: alignments needed a *custom* endpoint because transition IDs are assigned post-load — https://www.vdaalst.com/publications/p1495.pdf) | Bounded; each wrapper validated against pm4py via `export_prom5` |
| Maintenance | Tracks all of ProM's churn | Tracks only the wrapped plugins |
| Auto-update value | High (new packages auto-appear) | Moderate (auto-update feeds a *review queue*, not the live UI) |

### 1.2 Justification

- **pm4py already covers the mainstream and most former gaps.** Alpha/IM/IMf/IMd/Heuristics/ILP/genetic discovery, token-replay + alignments + footprints, DECLARE discovery/conformance, full OCEL 2.0 (OC-DFG, OC Petri nets, OC conformance) ([pm-ecosystem research], pm4py CHANGELOG). Mate's bundled `discovery`, `performance`, `ocel_discovery`, `process_comparison` modules already sit on pm4py. Wrapping ProM for these is pure duplication.
- **The residual ProM-only value is concentrated and the wrong shape for a generic bridge.** It is Java/GUI-bound, niche, and best surfaced as a handful of purpose-built Mate panels:
  1. **Data-aware Decision Mining → Data Petri Nets** — the single strongest ProM-only capability; pm4py has only decision-point heuristics, no first-class Data-Petri-Net discovery + data-aware alignment (Decision Mining in ProM — https://www.researchgate.net/profile/Wil-Aalst/publication/221585988_Decision_Mining_in_ProM/links/02e7e517a54ce89458000000/Decision-Mining-in-ProM.pdf; Data-Aware via Alignments — https://www.researchgate.net/publication/282281825_Data-Aware_Process_Mining_Discovering_Decisions_in_Processes_Using_Alignments).
  2. **Organizational / Social-Network mining** — five metrics (handover of work, subcontracting, working-together, similar-task, reassignment); pm4py is thin here (https://processmining.org/old-version/social.html).
  3. **DECLARE depth incl. online monitoring** — only if Mate needs declarative; *first evaluate Declare4Py* (pure Python, lower integration cost) for everything except ProM's online-monitoring plugin (https://github.com/ivanDonadello/Declare4Py).
  4. **Fuzzy Miner / ETM** — only on concrete user demand.
- **Explicitly do NOT bridge for:** OCEL/object-centric (pm4py at parity-or-ahead — https://www.ocel-standard.org/tool-support/libraries/pm4py/) and mainstream discovery/conformance.
- **The generic bridge is not wasted, just deferred.** The auto-update mechanism (Section 4) tracks the *whole* repository and feeds a **maintainer review queue**; new high-value plugins are promoted into the curated set deliberately, not auto-exposed.

**RECOMMENDATION: a selective `prom` module with a small, growable wrapper set, exchanging XES/PNML/JSON with a long-lived headless ProM service.** The generic capability (call any CLI plugin by name) exists *internally* (the worker can reach any ProM CLI plugin via the `/plugin` endpoint), but only curated plugins are mounted as Mate routes/panels.

---

## 2. How ProM is hosted and invoked headlessly

### 2.1 The JVM/OSGi reality (why out-of-process, JVM-8, single boot)

- ProM 6's framework is **context-agnostic** ("there exist objects, actions, views, and classes") and cleanly separates algorithm from GUI — which is exactly why headless invocation is possible (ProM 6 paper — https://ceur-ws.org/Vol-615/paper13.pdf).
- **It is not OSGi for plugin loading.** `org.processmining.framework.boot.Boot.boot()` uses a plain `URLClassLoader`, scans enabled packages in dependency order on parallel threads, and adds every package JAR to the system classloader by **reflectively calling `URLClassLoader.addURL` (the "PathHacker")**, then invokes the `@Bootable` method ([prom-architecture research], `Boot.java`). Consequences: a *flat* classpath, loose plugin isolation, and a boot trick that is **illegal on Java 9+ without `--add-opens` and blocked around Java 17**. **Pin Java 8** (https://promtools.org/troubleshooting-the-installation/).
- **Do not embed ProM in a non-Java host classloader**, and do not attempt GraalVM native-image: `URLClassLoader` + annotation scanning + Ivy-resolved plugin jars violate native-image's closed-world assumption, and GraalPy↔Java interop only works on the JVM distribution anyway — GraalVM buys nothing here and adds risk ([graalvm-polyglot research]).

### 2.2 Hosting topology

A dedicated, long-lived **ProM service container** (`mate-prom`), separate from the Mate `api` container (compose precedent: `docker-compose.yml` already runs `keycloak`, `keycloak-db`, `api`, `web` as separate services).

```
┌─────────────────────────── Mate api container (Python 3.12) ───────────────────────────┐
│  ModuleLoader → SubprocessBridge("prom")  ── Unix socket JSON-RPC + Parquet handoff ──┐ │
│                                                                                        │ │
│  ┌──────────────── prom module worker (the Mate "worker") ─────────────────────────┐  │ │
│  │  speaks Mate wire protocol (ready/call/ctx.*)                                    │  │ │
│  │  translates a curated call → HTTP to the ProM service; reads PNML/JSON back      │──┼─┘
│  └─────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
        │ HTTP (internal compose network)                  │ shared volume (XES/PNML/JSON artifacts)
        ▼                                                   ▼
┌──────────────────── mate-prom container (JDK 8) ─────────────────────────────────────────┐
│  ProM 6.15 install + curated packages                                                    │
│  boots ONCE: Boot.boot(...) → headless GlobalContext (ProM4PyContext-style)               │
│  HTTP surface:  POST /cache   POST /plugin   GET /plugins   GET /healthz   GET /packages  │
│  PackageManager (headless) for auto-update                                                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Why a separate container, not a JVM spawned by the worker:
- **One boot, reused across calls** — the package scan + classloading is expensive; per-invocation `java -jar` pays it every call ([prom-architecture research], path 4 is "simplest to isolate but pays full startup cost every call"). A warm JVM is the ProM4Py model (2–4× faster than pm4py — https://www.vdaalst.com/publications/p1495.pdf).
- **License arm's-length** — Mate's Python code never links ProM JARs (Section 5).
- **Crash isolation** — a ProM plugin OOM/hang restarts `mate-prom` without touching Mate's `api`.
- **Resource caps** — `mate-prom` gets its own `--memory`/`--cpus`/`--pids-limit`/wall-clock budget; the worker enforces an HTTP timeout and the host can hard-cancel the Mate worker independently ([docker-polyglot-isolation research], §5).

### 2.3 The concrete invocation mechanism (named plugin → PNML/BPMN/results)

The `mate-prom` HTTP surface is a thin shim over ProM's **CLI plugin layer** — the same layer RapidProM/ProM4Py build on. ProM's CLI lists callable plugins with signatures (e.g. `alpha_miner(XLog) -> (Petrinet, Marking)`) and runs them by mapped method name ([prom-headless-update-license research], §1a; ProM CLI: `org.processmining.contexts.cli.CLI`). Internally the shim uses the documented headless context pattern:

```java
PluginContext uipc = new HeadlessDefinitelyNotUIPluginContext(
        new ConsoleUIPluginContext(), "plugin_name");   // [prom-headless research §1a/1b]
```

Endpoints (ProM4Py-shaped — https://www.vdaalst.com/publications/p1495.pdf):

- `POST /cache` — upload/serialize an input object (an XES log file, a PNML net), returns a **cache key**. Objects never cross by value; results are referenced by key.
- `POST /plugin` — generic: `{ "plugin": "<cli_name>", "args": ["<cacheKey1>", ...], "params": {...} }` → calls *any* ProM CLI plugin, returns the **output cache key(s)** + a small typed metadata blob (plugin signature, return types).
- `GET /plugins` — the CLI plugin catalog (name + `parameterLabels`/`returnTypes` from `@Plugin`/`@PluginVariant` annotations) — feeds the maintainer review queue and capability discovery.
- `GET /export/{key}?format=pnml|bpmn|xes|json` — materialize a cached provided object to disk as PNML/BPMN/XES/JSON in the shared volume; returns the **path**. (ProM CLI exports PNML with **no layout info** — Mate re-lays-out client-side, as `modules/discovery/panel/` already does for DFGs/Petri nets.)
- `GET /healthz`, `GET /packages`, `POST /packages/update` (Section 4).

**Worked example — Data-Petri-Net decision mining** (the flagship wrapper). User has discovered a Petri net in Mate's `discovery` module (pm4py). The `prom` panel offers "Enrich with decision rules":

1. Worker pulls the filtered log: `ctx.event_log.pm4py()` → Parquet handoff over the Mate socket (`subprocess_host.py` `materialize` writes Parquet into `ctx.workdir`, worker reads it). Worker writes **uncompressed XES** (ProM CLI requires uncompressed XES) into the shared `mate-prom` volume. It also serializes the pm4py net to **PNML using `export_prom5=True`** (pm4py's exporter has an explicit ProM-compat flag and ProM-style invisible-transition naming — https://pm4py-source.readthedocs.io/en/latest/pm4py.objects.petri_net.exporter.variants.html).
2. Worker `POST /cache` the XES path → `keyLog`; `POST /cache` the PNML path → `keyNet`.
3. Worker `POST /plugin` `{ "plugin": "discovery_of_the_process_data_flow_decision_tree_miner", "args": ["keyLog", "keyNet"] }` (exact CLI name resolved from `GET /plugins`).
4. ProM runs the data-aware alignment + decision-tree (Weka J48) discovery → Data Petri Net provided object → returns `keyDPN`.
5. Worker `GET /export/keyDPN?format=pnml` (guards + decision rules embedded) and a `?format=json` rules sidecar; reads them from the shared volume.
6. Worker parses the DPN back, caches the result via `ctx.cache.set(...)`, emits `prom.decision_mining.completed` on the bus, returns to the Mate panel.

**XES/OCEL/PNML exchange with Mate's pm4py world (the two boundaries):**
- **Mate api ↔ worker:** unchanged. Event-log DataFrames cross as **Parquet on the shared filesystem** (never pickled, never on the socket) — `ctx.event_log.pm4py()` renames `case_id/activity/timestamp` → XES columns worker-side (`subprocess_worker.py`, `event_log_access.py:206-213`). OCEL is a **gap**: there are no subprocess wire RPCs for `ctx.object_log.*` yet ([sdk-boundary-surface research], §3.2/§5) — so v1 of `prom` is **case-centric only** (`requirements.event_log.log_model: case_centric`), which is fine because we explicitly do NOT bridge ProM for OCEL anyway.
- **Worker ↔ ProM service:** files on a shared volume — **XES** for logs, **PNML/BPMN** for models, **JSON** for tabular results — addressed by cache key/path. PNML round-trips to pm4py through `export_prom5`; pm4py's changelog explicitly tracks ProM alignment-result consistency, so results are validatable against pm4py.

---

## 3. Riding the polyglot-module architecture

This is the **flagship non-Python module**. The grounding brief's verdict is that Mate's subprocess wire is ~70% language-neutral; the non-neutral parts are *bootstrap/packaging*, not protocol ([subprocess-isolation-protocol research]). The design exploits that: it can ship **today with zero loader changes** as a Python worker, and graduate to a declared non-Python runtime later. Two deployment shapes:

### 3.1 Shape A — Python worker shim (RECOMMENDED for v1; zero platform change)

The Mate `prom` worker is an ordinary **`isolation: subprocess` Python module**. It is a `mate.sdk.Module` subclass; its handlers are HTTP clients to `mate-prom`. Nothing in `loader.py` / `installer.py` / `subprocess_host.py` changes — the worker is `python3` in its own `.venv` (just `mate.sdk` + `requests`/`httpx` + `pm4py` for PNML round-trips). The "polyglot" part (Java) lives entirely behind the internal HTTP boundary, in a container the worker does not manage Python-side.

Why this is the right v1:
- The single biggest polyglot blocker in the host is that there is **no manifest field for "how to start the worker"** — `_instantiate` (`loader.py:915-927`) and `_worker_python` (`subprocess_host.py:493-503`) hard-assume `<folder>/.venv/bin/python3` ([subprocess-isolation research], "Python-specific" #1). Shape A sidesteps that entirely.
- It reuses **every** existing facility: Parquet handoff, `ctx.cache`, `ctx.progress`, two-phase soft/hard cancel (`killpg` the worker; the worker `POST /cancel/{runId}` to ProM or just abandons the HTTP call), bus emit, `@route`+`@job` enqueue stubs.

### 3.2 Shape B — Native polyglot runtime (FUTURE; the generalization)

To make the worker itself non-Python (e.g. a JVM worker that *is* the ProM host, collapsing the two boundaries into one), the host needs three additions — already enumerated by the research as the polyglot gap:

1. A **manifest-declared worker runtime/entrypoint** (new `dependencies.runtime` block — see §3.4), consumed by a new branch in `_instantiate` instead of assuming a venv python.
2. **`install_module` runtime dispatch** (`installer.py`) keyed off that runtime: build a jar / `docker build` instead of `uv venv` + `uv pip install`.
3. The **`ready` JSON descriptor + ctx-RPC method table documented as the public contract** so a Java/container worker emits the same `ready` payload and speaks the same `ctx.*` methods — no bytes on the socket change ([subprocess-isolation research], "What a container worker would need").

For ProM specifically, Shape B's payoff is modest (you still need a JVM-8 process for ProM; you'd just move the HTTP boundary inside it), and Arrow Flight could replace the Parquet handoff for the worker↔ProM leg if throughput ever matters (Arrow Flight is the decisive winner for Python↔Java DataFrames — [other-polyglot-patterns research], §c). **Recommendation: build Shape A; let `prom` be the forcing function that justifies the Shape B manifest/runtime/installer work, but do not block v1 on it.**

### 3.3 Mapping onto manifest / loader / ModuleContext / frontend

**Manifest** (`modules/prom/manifest.yaml`) — Shape A, valid against today's schema (`packages/module-sdk-py/src/mate/sdk/manifest.py`):

```yaml
id: prom
name: ProM
version: 0.1.0
category: advanced
description: >-
  Surfaces high-value ProM 6 research plugins (Data Petri Net decision mining,
  social-network mining, declarative discovery) over a headless ProM service.
author: ProM (promtools.org) — see per-plugin licenses
license: GPL-3.0-only          # see Section 5; the hosted ProM core is GPL
# NOT confidential-safe: data leaves the api process for the ProM JVM container.
isConfidentialSafe: false

requirements:
  event_log:
    log_model: case_centric    # OCEL deliberately out of scope (Section 2.3)
    required_columns: [case_id, activity, timestamp]
    min_events: 50
    min_cases: 2

provides:
  - prom.decision_mining.data_petri_net
  - prom.social_network.handover
  - prom.declare.discovery
consumes:
  - log.imported
  - discovery.petri_net.inductive   # reuse a pm4py-discovered net as input

dependencies:
  python:
    requires-python: ">=3.12,<3.13"
    isolation: subprocess          # Shape A: Python worker in its own venv
    packages:
      - "httpx>=0.27"
      - "pm4py==2.7.11.13"         # PNML round-trip via export_prom5
      - "pyarrow>=17"              # read the host→worker Parquet handoff
    inherit: []
  npm: []

# Proposed NEW optional block (Section 3.4) — ignored by today's loader
# (Manifest.model_config extra="ignore"), wired by the prom service, not the loader.
prom_service:
  image: "mate-prom:6.15"
  endpoint: "http://mate-prom:8090"
  prom_version: "6.15"
  auto_update: review            # off | review | auto  (Section 4)
  packages:                      # curated, pinned
    - { name: "DataPetriNets", rev: "pinned" }
    - { name: "SocialNetwork",  rev: "pinned" }

frontend:
  panel: ./panel/index.tsx
  side_rail: ./side-rail.tsx
  widgets:
    - id: decision-rules
      entry: ./widgets/DecisionRules.tsx
      title: Decision rules
      description: Guards discovered on the data-aware Petri net.
      icon: GitBranch
      default_w: 7
      default_h: 9
      min_w: 6
      min_h: 7
    - id: handover-network
      entry: ./widgets/HandoverNetwork.tsx
      title: Handover-of-work network
      icon: Network
      default_w: 8
      default_h: 10

permissions:
  - read:event_log
  - write:module_results

config_schema:
  properties:
    decision_tree_min_support:
      type: number
      title: Min support
      minimum: 0
      maximum: 1
      step: 0.05
      default: 0.1
      ui: { widget: slider }
```

**Loader.** Shape A needs **no loader change**: `_instantiate` (`loader.py:918-921`) sees `isolation: subprocess`, builds a `SubprocessBridge`, the worker dials in and emits `ready`, and `_bind` mounts `@route`/`@job`/`@on_event` identically to an in-process module (loader only reads `dir(instance)` + decorator metadata). The `prom_service` block is `extra="ignore"`-dropped by `Manifest` today; it is read by a small **`prom` service supervisor** (a platform-side helper, NOT the generic loader) that ensures `mate-prom` is up and healthy before the worker's first `call`. (Shape B would add the loader/installer branches in §3.2.)

**ModuleContext.** The worker uses the existing `ctx.*` surface verbatim ([sdk-boundary-surface research], §3): `ctx.event_log.pm4py()`/`materialize` (Parquet handoff for the XES export), `ctx.cache.get/set` for DPN/rules results (note: tabular cache values must use the Parquet-path convention, not raw socket JSON — a known polyglot constraint, §3.6 of the boundary brief), `ctx.progress.update` (poll cancel on each tick), `ctx.bus.emit` for `prom.*.completed`, `ctx.registry.call` to consume a `discovery`-produced net, `ctx.workdir` as the staging dir for XES/PNML before they go to the shared `mate-prom` volume. `user_id` stays host-side and is never seen by the worker or forwarded to ProM — preserved invariant.

**Frontend panel model.** Identical to `discovery`/`agentsimulator`: a `panel/index.tsx` mounted at the module's page, optional `side_rail`, and dashboard `widgets` listed in the manifest (surfaced by `GET /api/v1/modules/cards`). Panels may only import `@/` paths in `apps/web/lib/runtime-externals.json`; bundled by `apps/web/scripts/bundle-modules.mjs`. Since subprocess `@route`s cannot take typed request bodies, run parameters are written via `PUT /api/v1/modules/prom/config` and read by the `@job` handler from `ctx.config` — the exact pattern AgentSimulator documents (`modules/agentsimulator/manifest.yaml:104-106`). Long ProM runs are `@route`+`@job` so the route returns `{ "job_id": ... }` and progress streams over SSE (`GET /api/v1/jobs/{id}/stream`).

### 3.4 Proposed manifest/protocol additions (concrete)

- **`dependencies.runtime`** (Shape B, future) — `{ kind: "python"|"jvm"|"container", entrypoint?: str, image?: str }`. `_instantiate` branches on `kind`; `install_module` dispatches build (`uv` / jar / `docker build`). Backward compatible: absent ⇒ `python`.
- **`prom_service`** (manifest, v1) — `{ image, endpoint, prom_version, auto_update, packages[] }`. Read by the prom supervisor, not the loader. `extra="ignore"` makes it inert on older platforms.
- **ProM-service protocol messages** (worker↔`mate-prom`, internal): `POST /cache`, `POST /plugin`, `GET /plugins`, `GET /export/{key}`, `GET /healthz`, `GET /packages`, `POST /packages/update`. JSON control + files on the shared volume. No change to the Mate socket bytes.

---

## 4. Auto-update: tracking the ProM package repository

### 4.1 What we track and how

ProM's package system is fully programmatic and unauthenticated ([prom-headless-update-license research], Topic 2):
- Repository index `packages.xml` is a **repository-of-repositories** (~338 `<repository url="Name/packages.xml"/>` entries). Versioned mirrors: `…/packages615/packages.xml`, `…/packages614/…`, plus a QUT failover mirror (`http://sefmining01.qut.edu.au/Packages/packages.xml`).
- Per-package descriptor carries `name, version, os, url(zip), desc, org, license, author, auto, hasPlugins, logo` and Ivy-style `<dependency org name rev="latest" changing transitive/>`.
- The headless API is `org.processmining.framework.packages.PackageManager` (singleton): `getAvailablePackages()`, `getInstalledPackages()`, `update(boolean, Boot.Level)`, `findOrInstallPackages(String...)`, `cleanPackageCache()`, plus a `main()`/`CommandLineInterface` — so packages can be managed without the GUI.

### 4.2 Mechanism (modeled on Mate's watched-folder poller)

Mate already has the exact shape of "poll a remote source on an interval, diff, ingest, record on a row": `apps/api/src/mate/api/ingest/watch.py::scan_watch` (manual `POST /watched-folders/{id}/scan` endpoint **and** a background poller share one core; a whole-scan failure is recorded, not fatal). And `model_store` (cv4cdd) is the precedent for **large binaries that live in platform storage, not the repo**, uploaded once and shared across the platform.

The `prom` auto-update reuses both ideas:

1. **Poller** — a background task (mirroring the watch poller) hits the pinned `packages<NN>/packages.xml` on an interval (default daily; ProM nightly builds at ~03:15). It **diffs `name`+`version`** against a local manifest of mirrored packages. New/changed entries are **never auto-applied** by default.
2. **Mirror** — for each accepted package, download the zip from its `url`, store under platform-shared storage (e.g. `data/prom/mirror/<Name>/<Name>-<ver>-all.zip`), and **rewrite a local `packages.xml` to local file URLs** (the documented manual-mirror approach). `mate-prom` points `PACKAGE_URL` at the local mirror — Mate controls exactly which versions ProM can see, and gets reproducibility + offline operation.
3. **Apply** — drive `mate-prom`'s `POST /packages/update` (which calls `PackageManager.update(...)`/`findOrInstallPackages(...)` headlessly), then `POST /packages/reboot` to re-scan (ProM's classloader does not hot-add packages; safest is a controlled JVM restart of `mate-prom`, which the supervisor sequences with a health gate).
4. **Surface** — new plugins land in a **maintainer review queue** (`GET /plugins` diff), not the live UI. Promotion into the curated set (a new `@route`/panel) is a deliberate, human step.

### 4.3 Versioning / pinning / safety

Decision matrix for the `auto_update` mode (per `prom_service.auto_update`):

| Mode | Behavior | Use when |
|---|---|---|
| `off` (safest default for shipped appliances) | Pinned `prom_version: 6.15` + pinned curated packages; no poll | Reproducible, citable, offline, GPL-shipped appliance |
| `review` (RECOMMENDED default for hosted) | Poll + mirror + diff into review queue; **no auto-apply** | Hosted Mate that wants to track new research without surprise behavior |
| `auto` | Poll + mirror + apply to a **staging** `mate-prom`, run a smoke suite, promote on green | Power deployments with a CI gate |

Safety rules:
- **Default to pinned ProM 6.15** (stable, citable). ProM Lite auto-updates packages and is "a moving target… should not be used to refer to in any publication" — avoid for reproducible research.
- **Pin curated packages by exact version**; treat `rev="latest"`/`changing="true"` dependencies as a supply-chain risk — resolve them to concrete versions at mirror time and record the resolution.
- **Mirror = reproducibility + a license-review chokepoint** (every mirrored zip is inspected for its `license=` before it is served — Section 5).
- **Staged apply only** — never mutate the live `mate-prom` in place; apply to staging, smoke-test (curated plugins run against a fixture log, results diffed vs the pinned baseline), then swap. Roll back by re-pointing `PACKAGE_URL` to the previous mirror snapshot.
- **Integrity** — ProM packages are unauthenticated HTTP zips with no signatures; record a checksum per mirrored zip and pin it (analogous to the subprocess venv `.installed-hash` discipline in `installer.py`).

---

## 5. Licensing (GPL/LGPL) implications

From [prom-headless-update-license research], Topic 3 (sources: https://www.process-mining-summer-school.org/prom/, https://promtools.org/development/documentation/packages-in-prom-6/):

- **ProM 6 core = GPL.** Software that *uses/links* the core is itself subject to GPL copyleft when distributed.
- **Packages/plugins = typically L-GPL, but per-package** — a package "may be distributed under a different (and even a conflicting) license than the core" and the actual license is the `license=` attribute in `packages.xml`. Some packages inherit stricter terms from third-party libs. **Never assume L-GPL; check each `license=`.**
- **L-GPL plugins are callable from proprietary code** and redistributable under your own license, provided you don't ship *modified* plugins under a non-L-GPL license and you preserve relink-ability.

Implications for Mate:

1. **Host, don't ship the core.** The single most important distinction is **shipping binaries vs hosting a service**. Mate is "locally-hosted" (`CLAUDE.md`). If a customer *runs* Mate (hosting), classic GPL is **not** triggered by network use — ProM core is GPL, not AGPL, so the SaaS loophole applies to pure hosting. If Mate is **shipped as an appliance/VM image that bundles the ProM core**, GPL obligations attach to that distributed unit.
2. **Out-of-process REST keeps Mate at arm's length.** Mate's Python code talks HTTP to a separately-running ProM JVM and never links ProM JARs — the ProM4Py shim is **MIT** and runs ProM in a separate process. The GPL still governs the ProM process Mate hosts/ships, but Mate's application code stays outside the link boundary.
3. **Mirroring is redistribution.** When Mate mirrors a package zip (Section 4) it redistributes under that package's `license=`; honor source-availability and notices per package. Make the mirror step a **license gate**: refuse to mirror a package whose `license=` is not on an allowlist without maintainer sign-off.
4. **Manifest honesty.** `modules/prom/manifest.yaml` `license:` reflects the *hosted ProM core* (GPL-3.0-only); a per-plugin license map is surfaced in the panel's "About" so users see, e.g., `DataPetriNets: L-GPL`.
5. **Confidentiality.** `isConfidentialSafe: false` — data leaves the `api` process for the ProM container. The module is hidden under "Show only confidential modules". (Even self-hosted, this is the honest setting because data crosses a process/container boundary the user should opt into.)

---

## 6. Risks

1. **ProM's flat-classpath / Java-8 boot is brittle.** `URLClassLoader.addURL` reflection breaks on Java 9+; package conflicts can corrupt the single classpath. *Mitigation:* pin JDK 8 in `mate-prom`, pin a curated package set, never hot-add packages into a running JVM (restart-and-rescan).
2. **Per-plugin serialization quirks.** ProM4Py reports the **alignment plugin is "incompatible"** with the generic schema (transition IDs assigned post-load) — some high-value plugins need *custom* endpoints, not the generic `/plugin`. *Mitigation:* selective wrapping already assumes per-plugin glue; budget a custom endpoint per awkward plugin; validate each against pm4py via `export_prom5`.
3. **PNML has no layout.** ProM CLI PNML export omits layout. *Mitigation:* re-lay-out client-side (the `discovery` panel already does this for nets/DFGs).
4. **Auto-update supply chain.** Unauthenticated HTTP zips, `rev="latest"` dependencies, no signatures. *Mitigation:* mirror + pin + checksum + staged apply + license gate (Section 4.3).
5. **OCEL gap.** No subprocess wire RPCs for `ctx.object_log.*` ([sdk-boundary-surface research], §5). *Mitigation:* v1 is case-centric only, which aligns with the decision to not bridge ProM for OCEL.
6. **Tabular cache over the socket.** `ctx.cache.set` crosses as JSON, so a subprocess worker cannot cache a DataFrame directly. *Mitigation:* cache DPN/rules as JSON + the Parquet-path convention; large artifacts live in the shared volume / `ctx.cache` Parquet store, not the socket.
7. **Two new failure domains** (ProM JVM + the worker↔ProM HTTP leg) outside Mate's existing cancel/health model. *Mitigation:* worker enforces an HTTP wall-clock timeout; supervisor health-gates `mate-prom`; Mate's two-phase cancel kills the worker (`killpg`) and the worker abandons/`POST /cancel`s the ProM run; `mate-prom` restart is independent of `api`.
8. **Latency.** Even a warm JVM adds an HTTP round-trip + XES write/read per call. *Mitigation:* `@job` (async, progress-streamed) for everything; cache aggressively per `(user, log, module)`; reuse pm4py-discovered nets as PNML input instead of re-discovering in ProM.
9. **Licensing footgun on appliance builds.** Bundling the GPL core into a shipped image. *Mitigation:* keep `mate-prom` an *optional, separately-pulled* container (like `model_store` binaries), not baked into the shipped Mate image; document the shipping-vs-hosting distinction in `DEPLOY.md`.
10. **Maintenance drift.** Wrapped CLI plugin names/signatures change across ProM versions. *Mitigation:* resolve plugin names from `GET /plugins` at runtime (don't hardcode), and pin the ProM version; the smoke suite catches signature drift before promotion.

---

## 7. Phased implementation outline (phases only)

- **Phase 0 — Spike & validate the boundary.** Stand up `mate-prom` (JDK 8 + ProM 6.15) booting once with a minimal `/healthz`, `/cache`, `/plugin`, `/export`. Manually run one plugin (e.g. social-network handover) XES-in/JSON-out. Validate a pm4py→PNML (`export_prom5`) → ProM → PNML→pm4py round-trip. No Mate integration yet.
- **Phase 1 — Flagship wrapper (Shape A).** Ship `modules/prom/` as a subprocess Python worker with **one** capability: Data-Petri-Net decision mining. Real panel + `decision-rules` widget. `@route`+`@job`, config via `ctx.config`, results via `ctx.cache`, completion on the bus. No loader changes. Pinned ProM, `auto_update: off`.
- **Phase 2 — Service supervision + a second capability.** Add the `prom` supervisor (health-gate `mate-prom` before first `call`; restart policy; resource caps). Add social-network mining (5 metrics) + its `handover-network` widget. Add the per-plugin license map to the panel "About".
- **Phase 3 — Auto-update (review mode).** Implement the poller + mirror + diff modeled on `scan_watch`; pin/checksum/license-gate; surface new plugins in a maintainer review queue. `auto_update: review` default for hosted; `off` for shipped appliances. `GET/POST /packages*` on `mate-prom`.
- **Phase 4 — Staged auto-apply.** `auto_update: auto`: staging `mate-prom`, smoke suite (curated plugins vs pinned baseline on a fixture log), promote-on-green, rollback by mirror-snapshot swap.
- **Phase 5 — Demand-gated capabilities.** DECLARE (after a Declare4Py vs ProM cost check) and/or Fuzzy/ETM, each only on concrete user need; each its own panel/widget.
- **Phase 6 (optional) — Shape B polyglot runtime.** Use `prom` to justify the host work: `dependencies.runtime` manifest block, `_instantiate`/`install_module` runtime dispatch, documented `ready`/`ctx.*` public contract, and (if throughput demands) Arrow Flight on the worker↔ProM leg. Collapse the two boundaries into a single JVM worker.

---

## 8. Key file references

Mate (extension points this rides on):
- Manifest schema (where `prom_service`/`runtime` slot in): `packages/module-sdk-py/src/mate/sdk/manifest.py`
- Decorators (`@route`/`@job`/`@on_event`): `packages/module-sdk-py/src/mate/sdk/decorators.py`
- ModuleContext (the `ctx.*` surface the worker uses): `packages/module-sdk-py/src/mate/sdk/context.py`
- Loader / subprocess instantiation: `apps/api/src/mate/api/modules/loader.py` (`_instantiate` 915-927, `_bind` 1002-1022)
- Subprocess wire (authoritative cross-process contract): `apps/api/src/mate/api/modules/subprocess_host.py`, `subprocess_worker.py`
- Per-module venv build/dispatch: `apps/api/src/mate/api/modules/installer.py` (155-278)
- Event-log Parquet handoff: `apps/api/src/mate/api/modules/event_log_access.py`
- Install pipelines (upload/git/registry — the auto-update analog): `apps/api/src/mate/api/modules/install_jobs.py`
- Watched-folder poller (auto-update model): `apps/api/src/mate/api/ingest/watch.py` (`scan_watch`)
- Large-binary store precedent: `modules/cv4cdd/manifest.yaml` (`model_store`, lines 88-99)
- Subprocess module precedent (config-via-`ctx.config`, pinned native deps): `modules/agentsimulator/manifest.yaml`
- Panel precedent (client-side net/DFG layout): `modules/discovery/panel/`
- Compose service layout: `docker-compose.yml`

ProM (specifics relied on):
- Boot / classloader hack: `Boot.java` — https://github.com/promworkbench/ProM-Framework/blob/main/src/org/processmining/framework/boot/Boot.java
- Headless CLI: `org.processmining.contexts.cli.CLI` — https://github.com/promworkbench/ProM-Contexts/blob/master/src/org/processmining/contexts/cli/CLI.java
- PackageManager (auto-update API): https://www.promtools.org/prom6/nightly/doc/org/processmining/framework/packages/PackageManager.html ; `PackageManager.java` — https://github.com/promworkbench/ProM-Framework/blob/main/src/org/processmining/framework/packages/PackageManager.java
- Package repo + descriptor: http://www.promtools.org/prom6/packages/packages.xml ; http://www.promtools.org/prom6/packages/CrossOrgProcMin/packages.xml ; mirror http://sefmining01.qut.edu.au/Packages/packages.xml
- ProM4Py (REST shim, MIT, perf): https://www.vdaalst.com/publications/p1495.pdf ; https://zenodo.org/doi/10.5281/zenodo.13622550
- ProM CLI headless tutorial: https://dirksmetric.wordpress.com/2015/03/11/tutorial-automating-process-mining-with-proms-command-line-interface/
- JVM / distributions: https://promtools.org/troubleshooting-the-installation/ ; https://promtools.org/doku/promlite.html
- Licensing: https://promtools.org/development/documentation/packages-in-prom-6/ ; https://www.process-mining-summer-school.org/prom/
- pm4py PNML `export_prom5`: https://pm4py-source.readthedocs.io/en/latest/pm4py.objects.petri_net.exporter.variants.html ; CHANGELOG https://raw.githubusercontent.com/pm4py/pm4py-core/release/CHANGELOG.md
- Decision Mining in ProM: https://www.researchgate.net/profile/Wil-Aalst/publication/221585988_Decision_Mining_in_ProM/links/02e7e517a54ce89458000000/Decision-Mining-in-ProM.pdf
- RapidProM (generic-bridge precedent): https://www.promtools.org/doku/rapidprom/home.html ; https://arxiv.org/abs/1703.03740
