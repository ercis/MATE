# Performance Metrics (Java)

Timing metrics for a case-centric event log - and the platform's **reference
JVM module**: the backend is a single self-contained Java jar, no Python code
at all.

## What it reports

| Dataset | Route | Shape | Content |
| --- | --- | --- | --- |
| `kpis` | `/kpis` | `kpi` | Cases, events, activities, variants, events per case, avg/median/P90/max cycle time, log span, throughput per day (+ processing time and waiting share when the log has `end_timestamp`, + distinct resources when it has `resource`). |
| `activity-performance` | `/activities` | `table` | Per activity: occurrences, cases, total/avg/median/P90 **dwell** time and its share of all dwell. Ranked by total dwell, so the top rows are the bottlenecks. |
| `handoff-performance` | `/transitions` | `table` | Per directly-follows pair: occurrences, cases, total/avg/median/P90 wait between the two steps and its share. |

"Dwell" is the wall-clock time from an event until the case's next event -
waiting plus work. A case's last event has no dwell and is excluded from the
averages. With an `end_timestamp` the event's own duration is reported
separately as processing time, so waiting can be read off against it.

## How it runs

- `manifest.yaml` declares `runtime: {kind: jvm, jar: dist/performance-metrics-all.jar}`.
- The platform launches the jar as a worker process (`java -Xmx512m -jar ...`)
  speaking the wire protocol in [`modules/PROTOCOL.md`](../PROTOCOL.md).
- On every `log.imported` a precompute job computes all three result sets
  (progress + cooperative cancellation throughout) and caches them as JSON.
- The routes serve that cache and compute on a miss, so a log imported before
  the module was installed still answers.
- `panel/index.tsx` is the module's own page: a KPI strip plus a ranked
  activities / hand-offs view. Plain TSX like any other module's panel - the
  frontend never knew the backend is a jar.
- The manifest's `datasets:` entries additionally let the platform's generic
  visualizations render the same three results as dashboard cards, with no
  widget code of our own.

## How it reads the log

Everything goes through `ctx.eventLog().duckdbFetch(sql, params)` - the
platform runs the SQL against the filter-applied `events` view and returns
JSON rows. That is the module system's default data path: the jar needs no
Parquet reader, no DuckDB driver, and never sees a local copy of the log.
Every query aggregates in SQL, so what crosses the socket is one row per
activity or hand-off - never one per event - and the result stays far under
the protocol's 256 MiB frame ceiling on any log size. Row output is capped
(500 activities, 300 hand-offs) and the response flags `truncated`.

The alternative path, `ctx.eventLog().materialize()` (a Parquet file on the
shared filesystem, read with DuckDB JDBC), is for algorithms that genuinely
need every row - a miner, not a set of aggregates.

## The jar (provenance)

`dist/performance-metrics-all.jar` is a **committed build artefact** - the api
image ships only a JRE and `modules/` is bind-mounted, so the platform cannot
build it. Source lives in
[`packages/module-sdk-jvm/examples/performance-metrics`](../../packages/module-sdk-jvm/examples/performance-metrics);
rebuild with

```bash
make sdk-jvm   # needs a JDK 17+ on the dev machine
```

which recompiles the SDK + example and copies the fat jar back here. The
conformance suite (`apps/api/tests/test_worker_conformance.py`) exercises the
same SDK internals, so meaningful drift between source and committed jar fails
tests.

## Limits

- Case-centric logs only (`log_model: case_centric`); OCEL logs never see it.
- Durations assume a parseable `timestamp`; rows with a null in
  `case_id`/`activity`/`timestamp` are dropped before any aggregate.
- Cases still in flight are not detected as such - their last recorded event
  ends the cycle time.
