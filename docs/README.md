# Documentation index

Every markdown doc in the repo, what it covers, and when to read it.

## Root (stay there by convention)

| File | What it is |
| --- | --- |
| [`/README.md`](../README.md) | Entry point: quick start, running modes (`make dev` / `up` / `up-dev` / prod overlay), bundled modules, configuration, data layout. Read first. |
| [`/CLAUDE.md`](../CLAUDE.md) | Orientation for Claude Code (claude.ai/code): commands, architecture summary, conventions. Kept in root because the tool loads it from there. |
| [`/PRODUCT.md`](../PRODUCT.md) | Product definition for design tooling (users, brand personality, design principles, accessibility targets). Read by the `impeccable` design skill's context script — must stay in root. |

`tasks.md` (root) is a gitignored personal scratchpad, not documentation.

## `docs/`

| File | What it is |
| --- | --- |
| [`INSTRUCTIONS.md`](./INSTRUCTIONS.md) | The design spec: tech-stack rationale, storage strategy, repo layout, the full module system (§5), API surface, frontend structure, job/progress architecture. The "why" behind everything; code docstrings cite its section numbers. |
| [`DEPLOY.md`](./DEPLOY.md) | Prod deploy runbook for the uni VM: proxy chain, secrets + realm setup, resource limits, Neo4j sidecar, verify checklist, update procedure, MCP server enablement. |
| [`MCP.md`](./MCP.md) | MCP server **consumer** reference: endpoint, auth (PAT/OAuth), scopes, tool catalog, data wall, client setup. Operator-side setup lives in `DEPLOY.md` ("MCP server"). |
| [`S3_OFFLOAD.md`](./S3_OFFLOAD.md) | S3 storage design doc (all phases implemented): local cache + eviction, bypass-hole closure, migration + quota, hydration performance, and the still-open multi-node seams. Storage code docstrings cite it. |

## `modules/`

| File | What it is |
| --- | --- |
| [`modules/README.md`](../modules/README.md) | The module authoring contract: manifest schema, SDK surface, events/capabilities, panels, checklists. Read this to build a module. |
| [`modules/PROTOCOL.md`](../modules/PROTOCOL.md) | Wire protocol between the platform host and non-Python module workers (JVM today; Node/R reserved). |
| [`modules/SIDECAR_SERVICES.md`](../modules/SIDECAR_SERVICES.md) | How a module ships with its own long-running container (compose profiles, e.g. the Neo4j graph sidecar). |

Some modules also carry their own `README.md` (e.g. `modules/actor_performance/`).

## Conventions

- Code comments cite docs by bare filename + section (`INSTRUCTIONS.md §5.1`, `DEPLOY.md §4b`, `S3_OFFLOAD.md`) — those files live here in `docs/`, except the `modules/` ones.
- New platform-level docs go in `docs/`; module-authoring docs go in `modules/`; root stays limited to the three convention-pinned files above.
