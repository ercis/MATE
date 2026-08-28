# S3 Offload — Scalable Storage

The storage backend: what it does, how it is configured, and the phased plan
that built it. Scope: **S3 authoritative, local disk a bounded cache;
hydrate-to-local on read; multi-node-ready but single-node now.** Code:
`apps/api/src/mate/api/storage/`.

## Configuration (env-only)

The backend is selected **exclusively in the platform env** (`.env` /
compose-interpolated vars → `mate.api.config.Settings`). There is no admin UI
and no DB-stored config: the connection (including the secret) is deployment
config like the Keycloak credentials, it must be identical for pre-boot CLIs
(`db_backup restore`) that run before any DB exists, and keeping it out of the
DB avoids the secret-at-rest/encryption-key dance entirely. Changing it =
edit `.env` → recreate the api (`docker compose up -d api`; a plain `restart`
keeps the old env).

| Variable | Default | Meaning |
| --- | --- | --- |
| `STORAGE_MODE` | `local` | `local` = single copy under `data/`; `s3` = bucket authoritative, local disk a cache |
| `STORAGE_S3_ENDPOINT` | – | Endpoint URL incl. scheme (`https://rgw.example.org`, `http://minio:9000`). **Empty = AWS S3 proper** (boto3 derives the regional endpoint); required for every other provider |
| `STORAGE_S3_BUCKET` | – | Bucket name (required in s3 mode) |
| `STORAGE_S3_REGION` | – | Required by AWS (SigV4 scope); most self-hosted providers ignore it; Cloudflare R2 wants `auto` |
| `STORAGE_S3_ACCESS_KEY` / `STORAGE_S3_SECRET_KEY` | – | Credentials |
| `STORAGE_S3_PATH_STYLE` | `true` | Path-style addressing (`host/bucket/key`) – needed by Ceph RGW / MinIO without wildcard DNS; set `false` on AWS (virtual-host style) |
| `STORAGE_S3_USE_SSL` | `true` | Only meaningful when the endpoint URL carries no scheme |
| `STORAGE_S3_VERIFY` | *(empty)* | TLS verification: empty = system CAs, `false` = disable, or a CA-bundle path (on-prem internal CA) |
| `STORAGE_S3_PREFIX` | *(empty)* | Key prefix – several deployments can share one bucket |
| `STORAGE_S3_QUOTA_BYTES` | `0` | Total-bucket ceiling; imports 507 once reached. `0` = no quota |
| `LOCAL_CACHE_MAX_BYTES` | `0` | Local working-cache budget for the eviction reaper. `0` = never evict |
| `CACHE_EVICT_DRY_RUN` | `true` | Reaper logs candidates without deleting – soak first, then set `false` |
| `CACHE_EVICT_MIN_AGE_SECONDS` | `600` | Never evict a dir accessed within this window |
| `JOB_RETENTION_DAYS` | `0` | Prune terminal jobs older than N days (bounds `metadata.db`). `0` = keep all |

**Is there one standard way to connect?** The S3 *wire protocol* (AWS REST +
SigV4) is a de-facto standard, and one boto3 client covers AWS S3, Ceph RGW,
MinIO, Backblaze B2, Wasabi, Cloudflare R2, DO Spaces, … What differs per
provider is only parameter *values*, which the table above captures: AWS needs
no endpoint but a real region and virtual-host addressing; self-hosted stacks
need an explicit endpoint, usually path-style, sometimes a custom CA; R2 wants
`region=auto`. No provider needs extra connection parameters beyond these.

### What lives in the bucket (S3 mode)

Per-user key layout mirrors the on-disk tree, fully tenant-isolated:

```
{prefix}/users/{uid}/event_logs/{lid}/{events,cases,meta,original}…
{prefix}/users/{uid}/event_logs/{lid}/ocel/{events,objects,relations,o2o}.parquet
{prefix}/users/{uid}/module_results/{lid}/{mid}/{key}.{json,parquet,bin}
{prefix}/_system/metadata.db                # hourly SQLite snapshot (Phase 2)
{prefix}/_system/modules/{mid}.tar.gz       # uploaded-module sources (Phase 2)
```

Synced: event-log Parquet (`events`/`cases`), OCEL Parquet (4 tables),
`meta.json`, retained original uploads, module result caches (+ filter
variants), the metadata-DB snapshot, uploaded-module source archives.
Watched folders additionally *read* arbitrary bucket prefixes as an import
source. **Never synced** (local-only, rebuilt on demand): module `.venv`s,
esbuild `.dist` bundles, `data/uv-python/` runtimes.

## The mirror problem this design fixes

A naive `s3` mode is a **write-through mirror**: a log/output dir is written
locally, then uploaded; on a read-miss the whole dir is pulled back.

### Why it did not relieve the VM

1. **Local copy was never evicted.** Write lands locally first, then uploads;
   reads pull the whole dir down, mark it hydrated, and never delete it. Turning
   S3 on added a second copy + upload cost — it did not shrink the VM.
2. **The biggest bloaters bypass S3 entirely** (VM-only, never synced):
   `metadata.db` (SQLite, grows unbounded with `jobs`/`analytics_event`; also a
   SPOF — lose the VM and every S3 object is orphaned), module `.venv/`
   (100 MB–2 GB each), `data/uv-python/` runtimes (200–400 MB per interpreter),
   `data/uploaded_modules/` packages.
3. **Hydration is whole-tree, per-process.** A cold read of a 10 GB log pulls
   all 10 GB before DuckDB touches it (DuckDB reads local paths only, no
   httpfs). `_hydrated` is per-process, so each worker/VM re-downloads a full
   copy.

Plus: no data migration on backend switch (old data stranded); `quota_bytes`
exists but is unenforced.

## Goal / invariant

VM local disk bounded by **active working set + module runtime artifacts**, not
total-data-ever. S3 holds the complete, restorable truth. The model flips from
`local = forever copy, S3 = mirror` to `S3 = authoritative, local = LRU cache`.

---

## Phase 1 — Mirror → cache-with-eviction  ✅ implemented

The core. Makes the local tree a reclaimable cache. Code:
`apps/api/src/mate/api/storage/eviction.py`.

- **Disk budget.** `LOCAL_CACHE_MAX_BYTES` (default `0` = disabled / keep
  every copy), env-only like the rest of the storage config. Independent of the
  S3-side `STORAGE_S3_QUOTA_BYTES`.
- **Access bookkeeping.** Effective last-access per cache dir =
  `max(in-process atime, newest file mtime)`. mtime is the restart-surviving
  fallback, so no new table / migration in Phase 1.
- **Reaper.** A periodic sweep (`cache_evict_interval_seconds`, default 60 s):
  when local cache bytes exceed the budget, delete least-recently-used dirs down
  to 90 % of the budget. Each delete is local-only — the bytes survive on S3 and
  re-hydrate on next read.
- **Lease guard (correctness-critical).** Every read/write of a cache dir takes
  a refcount lease for its duration (`EventLogAccess`, `ObjectCentricLogAccess`,
  `ResultCache`). The reaper never evicts a leased dir, and a reader blocks
  briefly if it races an in-flight eviction of the same dir — so a tree can
  never be deleted out from under a running DuckDB scan or a Parquet rewrite.

**Hard invariants (enforced):**

- Eviction runs **only in S3 mode**. In local mode the local copy is the only
  copy; the reaper no-ops.
- A dir is deleted locally **only after its S3 prefix is confirmed non-empty** —
  a remote copy provably exists to re-hydrate from.
- **`cache_evict_dry_run` defaults `true`**: the reaper logs what it would evict
  and deletes nothing. Soak first, confirm the candidate set, then flip to
  enable deletes.
- A dir accessed within `cache_evict_min_age_seconds` (default 600 s) is never
  evicted — guards against deleting a tree whose upload is still in flight.

Outcome: with S3 on + a budget set, the VM disk is bounded and idle data is
reclaimed.

---

## Phase 2 — Close the bypass holes (the real GB-scale hogs)  ✅ implemented

1. **`metadata.db` → S3 backup** (`storage/db_backup.py`). Hourly + on-shutdown
   SQLite online-backup snapshot to `{prefix}/_system/metadata.db`, so S3 is a
   complete restorable picture and losing the VM no longer orphans the bucket.
   SQLite stays single-node. **Restore is operator-invoked, not automatic** —
   `alembic upgrade head` runs in the entrypoint before the app, so a fresh
   (empty-schema) DB already exists by lifespan; a restore there would be too
   late and racy. Recovery is a pre-boot CLI step (see *Operating* below).
2. **Jobs retention** (`jobs/maintenance.py`). The daily sweeper
   (`main._retention_loop`, formerly analytics-only) now also prunes terminal
   `jobs` older than `JOB_RETENTION_DAYS` (0 = keep forever). Active jobs are
   never touched; `parent_job_id` is `SET NULL` so deletion order is safe.
3. **Uploaded module archives → S3** (`storage/module_archive.py`). On install,
   the module *source* (sans `.venv`/`.dist`/`.installed-hash`) is tar.gz'd to
   `{prefix}/_system/modules/{mid}.tar.gz`. At boot (S3 mode) any *owned* upload
   whose dir is missing is re-materialised before the loader runs, which then
   rebuilds the venv/bundle locally. The archive is deleted on last-owner
   uninstall. Restore + GC are both scoped to the `module_installs` set so they
   never fight over a dir.
4. **Uploaded-module GC** (`modules/maintenance.py`). At boot (S3 mode) any
   `uploaded_modules/{mid}` dir with zero install rows is removed, reclaiming its
   venv + bundle + source. **Deferred:** capping `data/uv-python/` to
   live-manifest interpreters — `uv` self-manages that cache; lower value, do
   later.

### Operating

All storage settings are env-only (see *Configuration* above). The recurring
flows:

- **Enable S3 on an existing deployment**: set `STORAGE_MODE=s3` +
  `STORAGE_S3_*` in `.env`, recreate the api (`docker compose up -d api`),
  probe with `python -m mate.api.storage.migration check`, then push the
  already-imported data up once with `… migration to_s3` (copy-only,
  re-runnable). New writes sync automatically; only pre-existing data needs
  the one-time push.
- **Actually reclaim disk**: set `LOCAL_CACHE_MAX_BYTES`, soak with
  `CACHE_EVICT_DRY_RUN=true` (watch `storage.eviction.*` logs), then set it
  `false`. Without a budget, S3 mode only mirrors — the VM stays big.
- **Switch back to local**: while the env still says `s3`, run
  `… migration to_local` (pulls everything down), then set
  `STORAGE_MODE=local` and recreate.
- **DB restore on a fresh VM** (before `alembic upgrade head`):
  ```
  STORAGE_MODE=s3 STORAGE_S3_ENDPOINT=… STORAGE_S3_BUCKET=… \
  STORAGE_S3_ACCESS_KEY=… STORAGE_S3_SECRET_KEY=… STORAGE_S3_PREFIX=… \
    uv run python -m mate.api.storage.db_backup restore
  ```
  The CLI reads exactly the same `STORAGE_*` env config as the app — no DB
  needed to bootstrap. Restore no-ops if the local DB already exists, so it's
  safe to run unconditionally in an entrypoint.

The admin **Storage insights** panel (Admin → Overview, `GET
/api/v1/admin/insights/storage`) stays read-only: backend mode, bucket
usage/object count vs quota, per-user totals.

## Phase 3 — Migration + quota  ✅ implemented

1. **Backend-switch migration CLI** (`storage/migration.py`). `python -m
   mate.api.storage.migration to_s3|to_local|check` walks every per-log +
   per-module cache dir and pushes it to S3 (`to_s3`, after a local→s3 switch)
   or pulls it down (`to_local`, before an s3→local switch), printing per-dir
   progress. **Copy-only** — it never deletes the source, so it's safe to
   re-run; the "switch + delete" hazard simply doesn't exist. Both directions
   require S3 active in the env (run `to_local` before flipping back). A CLI
   rather than a job on purpose: backend switches are operator work tied to an
   env edit + restart, and a huge copy must not race the job-execution
   timeout.
2. **Enforce the quota** (`storage/quota.py`). `STORAGE_S3_QUOTA_BYTES` is
   the total-bucket ceiling. The event-log upload route rejects up-front (507)
   when usage meets/exceeds it — before staging bytes. Usage (an S3 LIST) is
   cached for a 30 s TTL and invalidated on delete (frees space), so it's not a
   LIST per write. A guardrail, not a fence: an unknown usage (S3 error) never
   blocks. No-op unless S3 active + a quota is set.

## Phase 4 — Hydration performance  ✅ implemented

1. **Lazy `original.*` hydration** (`storage/sync.py`). Data hydration
   (`hydrate_log` → `download_prefix(skip=_is_original)`) now omits the retained
   `original.{ext}` upload — the common path (modules, dashboards, the events
   editor) never pulls those potentially huge bytes. The four paths that actually
   need the original — re-import, remap, duplicate, admin-download — call a new
   `hydrate_original` on demand (admin-download pulls *only* the original, never
   the parquet). Independent of the `_hydrated` data marker, so the two fetch
   independently.
2. **Parallel + integrity transfers** (`storage/s3.py`). `upload_dir` /
   `download_prefix` fan their per-object loops across a thread pool
   (`_TRANSFER_CONCURRENCY=8`; boto3 clients are thread-safe, S3 I/O drops the
   GIL) — a many-file OCEL log transfers in a fraction of the serial wall-clock.
   `download_prefix` verifies each file's byte length against the listed `Size`
   and raises `StorageError` on a short read (so the caller re-hydrates).
   `upload_dir` uses `upload_file`, which auto-switches to multipart above ~8 MB,
   so large Parquet uploads correctly. `download_prefix` gained the `skip`
   predicate used by (1).

## Multi-node-ready seams (design now, build later — out of scope)

- S3-authoritative + local-as-cache already makes app nodes stateless w.r.t.
  user data: any node hydrates on demand; per-node `_hydrated` is fine (S3 is
  shared truth). Free from Phase 1.
- Keep metadata access behind SQLAlchemy async (no SQLite-specific SQL) so
  SQLite→Postgres is a config swap when going multi-node.
- Known remaining multi-node blockers (in-process event bus, asyncio job queue)
  are left as-is — the next epic.

## Sequencing

`P1 (cache+evict+lease)` → `P2.1/2.2 (metadata durability + retention)` →
`P3 (migration + quota)` → `P2.3/2.4 (module artifacts)` → `P4 (perf)`.
P1 ships standalone value; the rest is independent after it.
