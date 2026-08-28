# Actor Performance

Decomposes waiting time between process steps by **actor behavior**, implementing

> E. L. Klijn, I. Tentina, D. Fahland, F. Mannhardt:
> *Decomposing Process Performance based on Actor Behavior*, ICPM 2024.
> https://doi.org/10.1109/ICPM63005.2024.10680657

For every case transition (activity → next activity) the module classifies how the work
moved between actors and aggregates waiting time per class:

| class | meaning |
|---|---|
| `continuation` | same actor, back-to-back in their own work stream |
| `interruption` | same actor, but they worked on something else in between |
| `handover_idle` | different actor who had been idle (or has no earlier work) |
| `handover_prioritized` | different actor squeezed it in while mid another task |
| `handover_deprioritized` | different actor finished other work first |

Pipeline (per run, fully transient): event log → prepared CSV → Event Knowledge Graph
in Neo4j (promg) → task instances → behavior classification on case df-edges →
per-transition aggregation → module cache. The graph is **wiped before and after every
run** and runs are serialized behind a module-global lock, so the shared sidecar never
holds user data at rest and tenants never see each other's graphs.

## The Neo4j sidecar

Neo4j is a separate server process - it is **not** bundled with the platform.

- **Docker stack:** enable the compose profile - `COMPOSE_PROFILES=graph` (+
  `NEO4J_PASSWORD`, optional `NEO4J_HEAP`) in `.env`. See `DEPLOY.md` §4b.
- **Host-mode dev (`make dev`):** run the sidecar once by hand:

```bash
docker run -d --name mate-neo4j \
  -p 127.0.0.1:7687:7687 \
  -e NEO4J_AUTH=neo4j/mate-graph-dev \
  -e NEO4J_PLUGINS='["apoc"]' \
  -e APOC_IMPORT_FILE_ENABLED=true \
  -e NEO4J_apoc_import_file_enabled=true \
  -e NEO4J_server_memory_heap_max__size=2G \
  -v "$(pwd)/data/neo4j-import":/var/lib/neo4j/import \
  neo4j:5.26-community
```

Only APOC **core** is needed (auto-installed by `NEO4J_PLUGINS`): promg's
`apoc.load.csv` import (APOC Extended in Neo4j 5) is replaced by built-in `LOAD CSV`
in this module. The `./data/neo4j-import` mount is the CSV handoff between the API
process and the server - both must see the same directory.

Connection resolution per field: module settings → `MATE_NEO4J_URI` /
`MATE_NEO4J_PASSWORD` / `MATE_NEO4J_IMPORT_DIR` environment (wired in compose) →
`bolt://localhost:7687` local defaults.

Sizing: heap 2G handles small/medium logs; the paper's full BPIC17 (~1.2M events)
wants ~10G (`NEO4J_HEAP=10G`). Use the module's *minimum transition frequency* setting
(the paper used 1000 on BPIC17) to bound extraction on very diverse logs.

## Fidelity

The generic `Case` + `Resource` semantic header reproduces the reference
implementation exactly: on the BPIC17 21-case sample (validated end-to-end through
the platform), all 203 aggregated rows are present and **every count/percentage
matches the reference pipeline bit-for-bit** - the classification is identical.
Mean waiting times differ by < 1 s per transition: the reference result parser
truncates sub-second duration components (`hours*3600 + minutes*60 + seconds`),
while this module keeps exact epoch-millisecond diffs. Logs without a `lifecycle`
column get an empty-string lifecycle so the task-variant machinery stays intact.

## promg 1.0.10 compatibility notes (applied worker-local in `pipeline/run.py`)

- pinned `neo4j>=5.15,<6` (6.x removed `write_transaction`) and `pandas>=2.2.2,<3`
  (3.x string dtype breaks promg's dtype mapper) - hence `isolation: subprocess`;
- `clear_db(replace=False)` + community-safe indexes (its default path uses the
  Enterprise-only `CREATE OR REPLACE DATABASE` / `NODE KEY` constraint);
- the task-instance DF label (`DF_TI_{Type}`) is computed directly - upstream's
  queries call a `get_df_ti_label()` that promg 1.0.10 does not have.

## Tests

```bash
uv run pytest modules/actor_performance/tests            # pure units (platform venv)

# integration (needs the sidecar above + promg importable):
NEO4J_TEST_URI=bolt://localhost:7687 \
NEO4J_TEST_PASSWORD=mate-graph-dev \
NEO4J_TEST_IMPORT_DIR=$(pwd)/data/neo4j-import \
uv run --with promg==1.0.10 --with 'neo4j>=5.15,<6' --with 'pandas>=2.2.2,<3' \
  pytest modules/actor_performance/tests/test_integration_neo4j.py -v
```

## License

`GPL-3.0` for this module folder: it links against [promg](https://github.com/PromG-dev/promg-core)
(GPL-3.0). The reference implementation repository carries no license file; its Cypher
was rewritten here (with fixes) and is credited in `pipeline/queries.py` and the
manifest's `authors`/`papers`.
