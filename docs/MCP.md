# Mate MCP Server

Mate exposes the whole platform to external AI clients (Claude Code, Claude Desktop, claude.ai connectors, Codex, custom agents) over the [Model Context Protocol](https://modelcontextprotocol.io): read everything the UI shows **except raw event rows**, and control everything the UI can control — imports, filters, dashboards, jobs, modules, admin.

The transport is **streamable HTTP** at `/mcp`. Every call acts as exactly one authenticated Mate account; `user_id` is never a tool argument. This document is the consumer reference — server enablement, Keycloak setup and VM deployment live in [`DEPLOY.md`](./DEPLOY.md) ("MCP server").

## Quick start

1. In the Mate web app: **Settings → API & MCP** → enable *External data access* (consent toggle) → *Create token*. Pick scopes (empty = read-only). Copy the `mate_pat_…` secret — it is shown once.
2. Connect (Claude Code shown; other clients below):

   ```bash
   claude mcp add --transport http mate https://pm-mate.uni-muenster.de/mcp \
     --header "Authorization: Bearer mate_pat_…"
   ```

3. First calls: `get_server_info` → `list_processes` → pick a `log_id` → `get_process_overview(log_id)`.

Local dev: `MCP_ENABLED=1` in `.env`, restart the API (`make dev-api`), endpoint `http://localhost:8000/mcp`.

## Endpoint & availability

| | |
|---|---|
| Endpoint | `https://<host>/mcp` (prod) · `http://localhost:8000/mcp` (dev) |
| Transport | MCP streamable HTTP (POST JSON-RPC; SSE-formatted responses). Not stdio, not WebSocket. |
| Enablement | Boot flag `MCP_ENABLED` **and** the admin's live toggle (Settings → API & MCP admin card). Disabled → `503`. |
| Discovery | `GET /.well-known/oauth-protected-resource` (RFC 9728) advertises Keycloak as the authorization server; the `401` `WWW-Authenticate` header points there. |
| Info API | `GET /api/v1/api-tokens/mcp-info` (browser-authed): endpoint URL, consent state, scopes, toolsets, read-only flag, OAuth client. |

## Authentication

Two credentials; both resolve to the same per-user principal:

- **PAT (personal access token)** — `mate_pat_…`, minted at Settings → API & MCP. Carries its own scope grant; **empty grant = all read scopes**. PATs are role-less by construction: they can never reach the admin toolset, regardless of scopes. Revocable per token (UI, `revoke_api_token`, or admin). Minting can be admin-restricted (mint policy).
- **OAuth 2.1 (Keycloak)** — auth-code + PKCE against the pre-registered public client (`mate-mcp`). Compliant clients discover the flow from the `401` resource metadata automatically. MCP scopes ride the JWT `scope` claim; a token carrying none of Mate's scopes falls back to read-only. **The `admin` scope additionally requires the `admin` realm role on the same token** — this is the only way to use admin tools.

**Egress consent**: independent of the credential, each user must opt in once (Settings → API & MCP → External data access) before any tool returns their data. Until then every data tool fails with `[consent_required]`.

## Scopes

| Scope | Grants |
|---|---|
| `processes:read` / `processes:write` | List/read log metadata + aggregates / import, rename, filter, remap, duplicate, delete, folders |
| `modules:read` / `modules:write` / `modules:manage` | Module outputs, results, datasets / per-module config / uninstall + restore defaults |
| `dashboards:read` / `dashboards:write` | Read boards (own + shared) / create, edit, share, delete |
| `jobs:read` / `jobs:control` | List/get/wait / cancel, retry, queue pause/resume |
| `watched:read` / `watched:write` | Read watched folders / CRUD + scan |
| `account:read` / `account:write` | Usage summary, token list / revoke own tokens |
| `admin` | Admin toolset. OAuth + `admin` realm role only; never grantable on a PAT. |

## Connecting from clients

**Claude Code**

```bash
# PAT
claude mcp add --transport http mate https://<host>/mcp \
  --header "Authorization: Bearer mate_pat_…"
# OAuth (browser flow via Keycloak; discovers the AS from the 401)
claude mcp add --transport http mate https://<host>/mcp
```

**Claude Desktop** — Settings → API & MCP shows a ready-made `mcpServers` JSON snippet (uses the `mcp-remote` stdio shim with your PAT). Equivalent by hand:

```json
{
  "mcpServers": {
    "mate": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<host>/mcp",
               "--header", "Authorization: Bearer mate_pat_…"]
    }
  }
}
```

**claude.ai custom connector** — add `https://<host>/mcp` as a remote MCP server; requires public HTTPS + the OAuth client (operator setup in DEPLOY.md). Login bounces through the university IdP; a consent screen lists the requested scopes.

**Codex CLI** — `codex mcp add mate --url https://<host>/mcp --bearer-token mate_pat_…` on builds with HTTP-server support; otherwise the same `npx mcp-remote …` shim as a stdio server.

**Smoke test** — `npx @modelcontextprotocol/inspector`, transport *Streamable HTTP*, URL + `Authorization: Bearer …` header.

## Conventions (read once, they apply everywhere)

- **Errors** carry a stable machine-readable prefix: `[not_found]` `[forbidden]` `[conflict]` `[invalid]` `[rate_limited]` `[timeout]` `[read_only]` `[consent_required]` `[scope_missing]` `[confirm_required]` `[internal]`. `404` semantics everywhere: a foreign user's resource is indistinguishable from a missing one.
- **Destructive tools** (marked **D** below) take `confirm: bool`. Without `confirm=true` they mutate **nothing** and return a dry-run preview (`{"confirmed": false, "preview": {...}}`) describing exactly what would happen — inspect, then re-call with `confirm=true`.
- **Long operations** (imports, remap, reimport, committed filters, retries) return `{"job_id": …}` immediately. Follow up with `get_job(job_id)` or block on `wait_for_job(job_id, timeout_seconds)` — hitting the timeout is not an error, you get the current state with `"timed_out": true`; call it again to keep waiting. Imported logs stay `processing` until every module's precompute finishes, then flip `ready`.
- **Pagination**: list tools take `cursor` + `limit` (max 200) and return `{"items": [...], "next_cursor": ..., "total": ...}`. Pass `next_cursor` back verbatim; it is opaque.
- **Output cap**: a single result is bounded at ~200 KB; larger payloads come back `{"truncated": true, "preview": ...}` — narrow the query (filters, `limit`, a specific variant/module) instead.
- **Read-only mode**: the operator can lock the server (`MCP_READ_ONLY` at boot hides write tools entirely; the live admin toggle makes them fail `[read_only]`).
- **Toolsets**: the registered tool list is `MCP_TOOLSETS`-configured at boot (default: everything except `admin`). `get_server_info` reports what's live.
- **Rate limits** (defaults): 120 req/min per user (burst 40), plus a tighter write bucket of 30/min (burst 10); 4 concurrent tool calls per user; 30 s per-call timeout. `429`/`[rate_limited]` includes a retry hint.
- **Timestamps** are ISO-8601 UTC strings.

## The data wall (what you can never read)

MCP serves **aggregates and curated module outputs only**. Structurally unavailable — no scope unlocks them:

- Raw event rows (the UI's Events table), distinct column *values*, cell-edit history
- OCEL object/event/relationship rows
- File downloads/exports of any kind (original uploads, XES/CSV exports, the metadata DB, analytics exports)
- Simulated event rows (agentsimulator) and other row-shaped module artifacts

Module outputs are computed under a restricted context that raises on any raw-log accessor — a module that hasn't precomputed yet fails with `[conflict] … run its analysis first`, never with data. Case-*level* metadata (case ids, durations, event counts in `get_variant_cases`) is deliberately available; event-level is not.

## Tool catalog

Legend: **W** = write (needs the write scope; blocked in read-only mode) · **D** = destructive (`confirm` dry-run pattern).

### meta (no scope required)
| Tool | |
|---|---|
| `get_server_info` | Server version, enabled toolsets, read-only state, your scopes |
| `whoami` | The account this token acts as |

### processes (`processes:read` / `processes:write`)
| Tool | |
|---|---|
| `list_processes` | Your event logs with aggregate stats (filter by `status`, `q`; paginated) |
| `get_process` | Full metadata for one log: counts, dates, column roles, schema, applied filter |
| `get_activities` | Unique activities + event counts, frequency-ordered |
| `get_activity` | One activity in depth: counts/shares, per-case occurrences, start/end role, containing variants |
| `get_variants` | Aggregate variants: sequence, case count/pct, durations (sort/filter/paginate) |
| `get_variant` | One variant in depth: durations incl. histogram, attribute breakdowns |
| `get_variant_cases` | Case-level rows for a variant (case_id, start/end, duration, event count) |
| `get_data_quality` | Per-column completeness (null/distinct counts) |
| `get_time_bounds` | Earliest/latest timestamp |
| `get_column_schema` | Column list with roles/types — never cell values |
| `get_ocel_overview` / `get_ocel_object_types` | OCEL aggregates (object-centric logs only) |
| `list_folders` | Folder tree |
| **W** `import_process_from_url` | Import from a public http(s) URL (XES/CSV/XML/JSON/OCEL) → job |
| **W** `update_process` | Rename / describe / move between folders |
| **W** `duplicate_process` | Clone a ready log |
| **WD** `set_committed_filter` | Commit the dataset filter — re-runs EVERY module's precompute → jobs |
| **WD** `remap_columns` | Force column-role mapping, re-import → job |
| **WD** `reimport_process` | Re-run import from the retained original → job |
| **WD** `delete_process` | Delete a log (jobs cancelled, data removed) |
| **W** `create_folder` / `update_folder` · **WD** `delete_folder` | Folder CRUD; folder delete cascades to contained logs — read the preview |

### analysis (`modules:read` / `modules:write` / `modules:manage`)
| Tool | |
|---|---|
| `list_modules` | Your installed modules + per-log availability + guidance capability |
| `get_module_output` | One module's curated output for a log (the primary analysis read) |
| `get_process_overview` | All guidance-capable modules' outputs for a log in one call |
| `get_module_results` | A module's cached JSON results, canonical view (keys + parsed entries) |
| `list_datasets` / `get_dataset` | Typed module datasets (table/graph/kpi/tree) → canonical envelope |
| `get_cached_guidance` | Stored AI guidance for a module or `"__platform__"` (never triggers an LLM call) |
| `get_bottlenecks` / `get_conformance` / `get_process_model` / `get_drifts` | Curated shortcuts (performance / conformance / discovery / drift modules) |
| **W** `set_module_config` | Per-user module config + enabled flag (admin-locked settings refuse) |
| **WD** `uninstall_module` · **W** `restore_default_modules` | Install lifecycle (`modules:manage`) |

### dashboards (`dashboards:read` / `dashboards:write`)
| Tool | |
|---|---|
| `list_dashboards` / `get_dashboard` | Your boards; `get` also works on boards shared with you (`is_owner: false`) |
| `list_shared_with_me` / `get_share_targets` / `list_dashboard_shares` | Sharing views |
| `get_dashboard_card_catalog` | Placeable cards (module widgets) with sizes + config schemas |
| `export_dashboard` | Portable id-free snapshot |
| **W** `create_dashboard` / `update_dashboard` / `import_dashboard` | Board CRUD (items = card placements, settings = filters/canvas) |
| **W** `share_dashboard` / `revoke_dashboard_share` | Read-only sharing with a teammate or team |
| **WD** `delete_dashboard` | Delete (cascades shares) |

### jobs (`jobs:read` / `jobs:control`)
| Tool | |
|---|---|
| `list_jobs` / `get_job` | Your background jobs (status/type filters; includes progress + payload) |
| `wait_for_job` | Block until terminal; timeout returns current state with `timed_out: true` |
| **W** `cancel_job` / `retry_job` / `pause_my_queue` / `resume_my_queue` | Job control |
| **WD** `cancel_all_jobs` | Cancel everything of yours |

### watched (`watched:read` / `watched:write`)
| Tool | |
|---|---|
| `list_watched_folders` / `get_watched_folder` | Auto-import sources + file ledger |
| **W** `create_watched_folder` / `update_watched_folder` / `scan_watched_folder` | CRUD + scan-now (imports → jobs) |
| **WD** `delete_watched_folder` | Stop watching (imported logs are kept) |

### account (`account:read` / `account:write`)
| Tool | |
|---|---|
| `get_usage_summary` | Your usage-analytics totals |
| `list_api_tokens` | Your PATs (prefix only — secrets are never retrievable) |
| **WD** `revoke_api_token` | Revoke one of your tokens. There is deliberately **no minting over MCP** |

### admin (`admin` — OAuth + admin realm role; toolset off unless `MCP_TOOLSETS` includes it)
Cross-user platform control: `admin_list_jobs`, `admin_get_job_logs`, `admin_cancel_job`, `admin_retry_job`, **WD** `admin_kill_job`, **WD** `admin_cancel_all`, `admin_pause_user_queue`/`admin_resume_user_queue`, `admin_list_users`, `admin_insights(section=overview|users|storage|jobs|usage)`, `admin_list_event_logs` (metadata only — no downloads), `admin_get_worker_pool`/**W** `admin_set_worker_pool`, `admin_get_mcp_config`/**W** `admin_set_mcp_config` (live enable/read-only/mint policy — note: `enabled=false` cuts your own connection within ~5 s), teams CRUD + membership (**WD** on delete/remove), `admin_list_dashboard_shares`/**WD** `admin_revoke_dashboard_share`, `admin_list_api_tokens`/**WD** `admin_revoke_api_token`. Storage configuration, migrations and all raw exports are excluded by policy — UI only.

### Resources
`mate://docs/usage` (in-band usage guide) · `mate://processes` (your process list) · `mate://process/{log_id}/module/{module_id}` (one module output). Everything resources serve is also available as tools — prefer tools; client resource support varies.

## Typical workflows

**Explore a process**: `list_processes` → `get_process` + `get_activities` + `get_variants` → `get_process_overview` (or targeted `get_bottlenecks` / `get_conformance` / `get_drifts`) → drill into `get_variant` / `get_variant_cases`.

**Import and analyse**: `import_process_from_url` → `wait_for_job(job_id)` → log flips `ready` once module precompute finishes → read outputs. Same pattern after `set_committed_filter` / `remap_columns` / `reimport_process`.

**Build a dashboard**: `get_dashboard_card_catalog` + `list_datasets` → `create_dashboard(event_log_id=…, items=[…])` → `share_dashboard(target_team_id=…)`.

**Recover a failed run**: `list_jobs(status="failed")` → `get_job` (error detail) → fix cause → `retry_job` → `wait_for_job`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `503` "MCP is disabled" | Boot flag off or admin kill switch — Settings → API & MCP (admin card) |
| `401` | Missing/expired/revoked token. The response's `WWW-Authenticate` carries the OAuth discovery URL |
| `[consent_required]` | Enable *External data access* in Settings → API & MCP |
| `[scope_missing]` | Token lacks the named scope — mint a new PAT with it (scopes are fixed at mint) |
| `[read_only]` | Server-wide write lock — admin toggle or `MCP_READ_ONLY` env |
| `[conflict]` "no precomputed results yet" | Module hasn't run for this log — wait for `ready` / check `list_jobs` |
| `[conflict]` "not ready" on aggregates | Log still `importing`/`processing` — `wait_for_job` on the import |
| `[confirm_required]` message in a preview | Expected: re-call the destructive tool with `confirm=true` |
| `429` / `[rate_limited]` | Per-user bucket (writes have a tighter one) — honor the retry hint |
| `403` "Origin not allowed" | Browser-origin request from a foreign origin — use a native/server client |
| Admin tool refuses a PAT | By design: admin needs OAuth + the `admin` realm role + the `admin` scope |

## Guarantees & limits

- **Tenant isolation**: a token reads and mutates only its owner's data; foreign ids 404. Dashboard sharing is the single read-only cross-account path.
- **Audit**: every tool call is logged (`mcp.tool_call` structlog + admin insights), including mutation flag and outcome.
- **Single instance**: rate limits and sessions are in-process (matches the one-VM deployment).
- Long-idle GET notification streams may be reaped by the upstream proxy; request/response tool calls are unaffected.
