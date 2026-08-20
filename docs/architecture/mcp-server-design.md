# Authenticated MCP Server for Mate (FandFPMIF)

**Status:** Architecture proposal (conceptual). No code.
**Author role:** Senior architect, future-of-platform planning.
**Scope:** A multi-tenant, remotely-hosted MCP server that exposes Mate's functionality to AI assistants — both the platform's *integrated* assistant and *external* user-chosen assistants (Claude Desktop / Claude.ai / web connectors). Auth bridges to the existing Keycloak realm. The integrated assistant is re-platformed onto the same MCP server, replacing today's intent-navigation-only approach.

All file paths are absolute under `/Users/t_zimm11/Code/mate/FandFPMIF`.

---

## 0. Executive summary & recommendation

**Recommendation:** Build a single Streamable-HTTP MCP server, hosted as a new FastAPI sub-app (or a sibling ASGI service) inside the existing `apps/api`, that acts as an **OAuth 2.1 Resource Server** validating audience-bound JWTs against the *existing* `flows-funds` Keycloak realm. It reuses Mate's auth chokepoint (`apps/api/src/mate/api/auth/dependencies.py`, `auth/jwks.py`, `auth/ownership.py`) verbatim for token validation and `sub`-based tenant scoping. The tool surface is **two-layered**:

1. A **static core** of ~30 tools mapping the platform inventory (logs, processes, folders, dashboards, jobs, modules-meta) onto MCP tools, derived from the existing `/api/v1` routers.
2. A **per-user dynamic layer** that reflects each authenticated user's *installed and enabled* modules and their `@route`/`@job` capabilities — computed exactly the way `ai_nav.build_user_destinations` (`apps/api/src/mate/api/ai_nav.py:228`) already computes per-user nav destinations, and refreshed via `notifications/tools/list_changed`.

The integrated in-app assistant consumes the **same** MCP server through an in-process MCP client, unifying the AI layer. Today's `POST /api/v1/ai/route` (intent → navigation) and `POST /api/v1/ai/chat` (grounded advice) become **two tools among many**, not the whole AI surface. The assistant gains the ability to *act* (import logs, run analyses, manage dashboards) instead of only *navigate + advise*.

**Why now:** The grounding confirms (`ai-auth-registry`) that today's assistant is "a router + advisor" with no general function-calling loop against platform APIs — "An MCP server is the missing layer that would let an external model actually invoke the operations." Mate already has the two prerequisites an MCP server needs: a single auth chokepoint and a per-user capability view (`_UserScopedRegistry`).

**Biggest design risk and its mitigation up front:** Keycloak (through ≥26.5) does **not** natively understand RFC 8707 Resource Indicators — it rates MCP `2025-06-18`/`2025-11-25` as "Partially Supported without Resource Indicators" ([Keycloak: MCP](https://www.keycloak.org/securing-apps/mcp-authz-server)). We therefore use the de-facto workaround: per-surface client scopes (`mcp:tools`, …) each carrying an **Audience protocol mapper** whose `Included Custom Audience` is the MCP server's canonical URL, and we validate `aud` on every request. This is the single load-bearing auth decision (§4).

---

## 1. Where this sits in Mate's architecture

```
                          ┌─────────────────────────────────────────────┐
   External assistant     │   Keycloak realm  flows-funds                │
   (Claude.ai / Desktop)  │   - OIDC discovery / JWKS (existing)         │
        │  OAuth 2.1       │   - DCR endpoint (clients-registrations)     │
        │  (PKCE, DCR)     │   - NEW client scopes: mcp:tools/resources   │
        ▼                  └─────────────────────────────────────────────┘
  ┌───────────────┐  Bearer (aud=mcp-url)          ▲ validate aud/iss/exp
  │  MCP server   │────────────────────────────────┘  (reuse auth/jwks.py)
  │  /mcp         │
  │  Streamable   │  in-process function calls (NOT HTTP loopback)
  │  HTTP         │────────────┐
  └───────────────┘            ▼
        ▲              ┌────────────────────────────────────────────┐
        │ in-proc MCP  │  Existing Mate service layer (apps/api)     │
        │ client       │  - get_current_user_from_token (dependencies)│
  ┌───────────────┐    │  - get_owned_* (ownership.py)               │
  │ Integrated    │    │  - ModuleLoader / _UserScopedRegistry       │
  │ in-app        │    │  - JobRuntime, EventBus, DuckDB pool         │
  │ assistant     │    │  - module @route/@job handlers              │
  └───────────────┘    └────────────────────────────────────────────┘
```

Key placement decisions:

- **One process, one auth model.** The MCP server lives beside the REST API and calls the *same service functions*, not its own copy of business logic, and not an HTTP loopback to `/api/v1/*`. This avoids token-passthrough (§7) and a second auth path. The MCP tool implementations import and call `get_owned_event_log`, `ModuleLoader`, `JobRuntime` etc. directly.
- **The MCP endpoint is a new mount**, e.g. `app.mount("/mcp", mcp_asgi_app)` in `apps/api/src/mate/api/main.py`, *outside* the `/api/v1` router tree so it gets its own auth and its own CORS/Origin rules. It does **not** depend on `CurrentUserDep` (which is a FastAPI dependency keyed to the REST request model); instead it runs MCP-spec-conformant 401/PRM handling and then calls `get_current_user_from_token(token, session)` (`dependencies.py:155`) — the function explicitly designed to be reused outside the dependency.

---

## 2. Transport

### Decision: Streamable HTTP, single `/mcp` endpoint.

Per the MCP spec (`2025-06-18`, carried to `2025-11-25`) there are two standard transports: **stdio** (local, single-user subprocess) and **Streamable HTTP** (multi-client, hosted) ([spec/transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)). For a remotely-hosted multi-tenant server, stdio is disqualified (it is "fundamentally single-user, single-process, local"). The legacy "HTTP+SSE" transport (`2024-11-05`) is deprecated; SSE survives only as an optional *response mode within* Streamable HTTP.

| Transport | Multi-tenant? | Remote? | Verdict |
|---|---|---|---|
| stdio | No (one subprocess/user) | No | Reject — not hostable |
| HTTP+SSE (legacy) | Yes | Yes | Reject — deprecated; only for old clients |
| **Streamable HTTP** | **Yes** | **Yes** | **Adopt** |

Concrete obligations the server must meet ([spec/transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)):

- A single MCP endpoint path supporting **POST and GET** (`https://<host>/mcp`). POST carries client→server JSON-RPC; a POST response MAY upgrade to `text/event-stream` for streaming/notifications, or return a single `application/json`. A standalone GET opens an SSE stream for server-initiated messages (e.g. `notifications/tools/list_changed`).
- **`Origin` header MUST be validated** on every connection (DNS-rebinding defense). Mate already centralises CORS in `main.py:355`; the MCP mount needs its own stricter allow-list (Anthropic's infra origins for external connectors; the platform's own origin for the in-app client).
- **`Mcp-Session-Id`**: server MAY issue at init; client MUST echo. Session IDs MUST be cryptographically secure and **MUST NOT be used for authentication** (§7).
- **`MCP-Protocol-Version`** header required on every post-negotiation request.

**Fit with Mate's SSE world:** This is a natural fit. Mate already standardises on SSE-not-WebSocket (`CLAUDE.md`: the prod proxy chain drops WS upgrades; `routes/events_sse.py`, `routes/jobs.py:147` stream `text/event-stream`). Streamable HTTP's SSE response mode rides the same proxy-friendly HTTP streaming. The async/job model maps cleanly: long operations are *submit-then-stream*, exactly as the existing job SSE already does.

---

## 3. Tool / Resource / Prompt surface

### 3.1 Primitive choice: tools-first.

The three MCP primitives differ by *who controls invocation*: **tools** are model-controlled (`tools/list` + `tools/call`), **resources** are application-controlled URI-addressed data, **prompts** are user-controlled templates ([spec/tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)). The decisive constraint: **Claude's hosted connectors and the Messages API MCP connector consume only tools** — "Of the feature set of the MCP specification, only tool calls are currently supported" ([Claude: MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)). Resources/prompts are usable only by a client *we* run (the in-app assistant).

**Recommendation:**
- Expose the full actionable surface as **tools** (works for everyone).
- Additionally expose **resources** and **prompts** for the in-app client only (it gets richer affordances: a log as a subscribable resource, a "diagnose this process" prompt template). These are pure upside for the path we control and ignored by external clients.

| Primitive | External clients | In-app client | Use for |
|---|---|---|---|
| Tools | ✅ | ✅ | All actions: list/import/run/fetch/manage |
| Resources | ❌ (ignored) | ✅ | Logs/processes/dashboards as URI data; `resources/subscribe` for live job state |
| Prompts | ❌ (ignored) | ✅ | Guided workflows ("conformance check this log") |

### 3.2 Static core tools (derived from the grounding inventory)

Mapped from `routes/__init__.py` and the per-router inventory in the `ai-auth-registry` grounding. Each tool re-asserts ownership server-side (via `get_owned_*`) and is gated by a least-privilege scope (§4.4). Naming convention: `mate.<resource>.<verb>`.

**Event logs / processes** (from `event_logs.py`, `event_log_data.py`, `ocel_data.py`)
- `mate.logs.list` — list the caller's event logs (read).
- `mate.logs.get` — detail for one log (read; `get_owned_event_log`).
- `mate.logs.import` — create/import from a referenced upload or URL → returns `job_id` (write; async). XES/XES.gz/CSV/JSON/XML.
- `mate.logs.probe` — `probe-xml`/`probe-json` pre-import inspection (read).
- `mate.logs.delete` / `mate.logs.patch` (write).
- `mate.logs.events` — paged events (read).
- `mate.logs.variants` / `mate.logs.variant_detail` / `mate.logs.activities` (read).
- `mate.logs.data_quality` (read).
- `mate.logs.filter.set` — active-filter PUT (write; affects what modules see).
- `mate.logs.cell.patch` / `mate.logs.bulk_fill` (write — event editing).
- `mate.ocel.overview` / `mate.ocel.object_types` / `mate.ocel.objects` / `mate.ocel.events` / `mate.ocel.relations` (read; OCEL logs).

**Folders** (`folders.py`)
- `mate.folders.list` / `create` / `patch` / `delete` / `reorder`.

**Dashboards & sharing** (`dashboards.py`, `sharing.py`)
- `mate.dashboards.list` / `get` / `create` / `patch` / `delete`.
- `mate.dashboards.export` / `import`.
- `mate.dashboards.share.list` / `create` / `delete`.
- `mate.sharing.shared_with_me` / `mate.sharing.targets` (read).

**Jobs** (`jobs.py`, `events_sse.py`)
- `mate.jobs.list` / `get` (read).
- `mate.jobs.cancel` / `retry` / `cancel_all` (write).
- `mate.jobs.wait` — *MCP-native helper*: submit-then-poll/stream wrapper that blocks (with timeout) on a `job_id` and returns the terminal state, so a model that just called an async tool gets one synchronous-feeling result. Backed by the existing `GET /api/v1/jobs/{id}/stream` SSE.

**Watched folders** (`watched_folders.py`)
- `mate.watched_folders.list` / `create` / `patch` / `delete` / `scan`.

**Modules — meta** (`routes/modules.py`)
- `mate.modules.list` — installed+available, per-log availability (read).
- `mate.modules.manifest` / `config.get` / `config.set` / `layout.get` / `layout.set`.
- `mate.modules.install` — from registry/git/upload → returns `job_id` (write; async).
- `mate.modules.uninstall` / `restore_defaults` (write).

**AI surface (re-homed; see §6)**
- `mate.ai.navigate` — wraps `ai_nav.route_intent` (`ai_nav.py:835`). Returns nav targets/hrefs; for the in-app client to render chips. *Model-callable but UI-coupled — annotate as such.*
- `mate.ai.guidance.module` / `mate.ai.guidance.process` / `mate.ai.guidance.import_mapping` — wrap `routes/ai_guidance.py` interpretation endpoints (read-only model-backed analysis).

**Preferences / onboarding / usage / system** — exposed sparingly:
- `mate.preferences.get` / `set` (per-user KV).
- `mate.system.storage_info` (read).
- *Deliberately excluded from default scope:* `usage.wipe`, `usage.export`, AI-provider config, privacy/consent — see §3.5.

**Admin tools** (gated by the `admin` realm role, mirroring `AdminUserDep` / `dependencies.py:194`) — a separate `mate.admin.*` family behind an `mcp:admin` scope, only surfaced when the token carries `realm_access.roles` ⊇ `{admin}`. Cross-tenant by design; never in the per-user default set.

### 3.3 Tool schema (manifest fields per tool)

Each tool definition carries:

```jsonc
{
  "name": "mate.logs.import",
  "title": "Import event log",
  "description": "Import an XES/CSV/OCEL event log. Returns a job_id; the import runs asynchronously.",
  "inputSchema": { /* JSON Schema — reuse the existing Pydantic request schema, dumped */ },
  "outputSchema": { /* e.g. {job_id: string} */ },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": false,
    "x-mate-scope": "mcp:logs:write",     // least-privilege scope (§4.4)
    "x-mate-async": true,                  // returns job_id; pair with mate.jobs.wait
    "x-mate-confidential-safe": true       // does not exfiltrate data off-box
  }
}
```

- `inputSchema`/`outputSchema` are generated from the **existing Pydantic request/response models** already used by the matching `/api/v1` route — single source of truth, kept in sync the way `make codegen` keeps `api-types.ts` in sync. No hand-written schemas.
- `annotations` are **untrusted to the client** per spec — they are hints, not security. The `x-mate-scope` annotation is informational for clients; the actual gate is server-side scope validation (§4.4).
- Tool descriptions are **static and tenant-neutral** (tool-poisoning defense, §7) — never interpolate user data, log names, or another tenant's strings into a tool description.

### 3.4 Resources (in-app client only)

- `mate://logs/{log_id}` — a process as a resource; `resources/subscribe` to get `notifications/resources/updated` when its status flips `processing → ready` (driven by the existing bus `job.*`/`<module>.completed` events).
- `mate://logs/{log_id}/variants`, `mate://dashboards/{id}` — addressable read data.
- All resource reads pass through `get_owned_*` / `user_can_read_log` (`loader.py:1401`) so sharing semantics hold.

### 3.5 What stays OUT of the surface (least privilege of the *catalog* itself)

The current assistant is deliberately incapable of touching anything outside `SETTING_WHITELIST` (`ai_nav.py:391`), with sensitive settings "deliberately absent and rejected" (`ai_nav.py:376`). The MCP server *will* newly enable many of those — so they must be gated, not free. Explicitly **not** in any default scope (require explicit step-up scope or are excluded entirely):

- AI-provider API keys, system prompt, model/provider selection, `allow_process_data`.
- Privacy/analytics consent, account email/password.
- Data wipe / full export (`usage.wipe`, `usage.export`, `/admin/export`).
- Module install/uninstall is **write-scoped and consent-flagged**, never in `mcp:tools-basic`.

---

## 4. Auth & security (the core of this design)

### 4.1 Role split (OAuth 2.1)

Per the MCP authorization spec, three roles ([spec/authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization)):
- **MCP server = OAuth 2.1 Resource Server** — validates tokens, never issues them.
- **MCP client = OAuth 2.1 client** — obtains tokens on the user's behalf.
- **Authorization Server = the existing Keycloak `flows-funds` realm** — "It may be hosted with the resource server or a separate entity." Keycloak is the separate entity.

Mate already *is* a resource server for `/api/v1` (RS256 Bearer + JWKS, `dependencies.py:103`). The MCP server is a second resource server in the same realm, with a *different audience* (the MCP URL, not `flows-funds-api`).

### 4.2 Discovery & token flow (what the MCP server must implement)

1. **Unauthenticated request → 401 + pointer.** Respond `401` with
   `WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource", scope="mcp:tools"`.
   Mate's existing 401 (`_UNAUTH`, `dependencies.py:41`) already emits `WWW-Authenticate: Bearer error="invalid_token"`; the MCP variant adds the `resource_metadata` and `scope` hints the spec requires.
2. **Protected Resource Metadata (RFC 9728) — MUST implement.** Serve at `/.well-known/oauth-protected-resource` (unauthenticated):
   ```jsonc
   {
     "resource": "https://<host>/mcp",
     "authorization_servers": ["https://<keycloak>/realms/flows-funds"],
     "scopes_supported": ["mcp:tools", "mcp:resources", "mcp:prompts"],
     "bearer_methods_supported": ["header"]
   }
   ```
   This is the only new well-known the *server* must host. AS discovery (RFC 8414 / OIDC) is served by Keycloak's existing realm well-known.
3. **Client → Keycloak.** Client reads PRM, finds the realm AS, runs OAuth 2.1 Authorization Code + **PKCE S256** (mandatory), validates `iss` (RFC 9207), and **includes the `resource` parameter** in both authorization and token requests (clients MUST send it even though Keycloak ignores it — §4.3).
4. **Token validation (every request).** Reuse `auth/jwks.py` + `dependencies._decode_token` *with a different audience*: verify RS256 signature against the realm JWKS, `iss == keycloak_issuer`, `exp`/`iat`, and **`aud` contains the MCP server URL** (RFC 8707 audience binding — the #1 security property). Wrong/expired → 401; insufficient scope → 403 with a step-up `WWW-Authenticate` challenge.

### 4.3 Bridging to Keycloak — the RFC 8707 gap and its workaround

**The caveat (load-bearing):** Keycloak through ≥26.5 does not natively honour the RFC 8707 `resource` parameter; its MCP doc rates the protocol "Partially Supported without Resource Indicators" ([Keycloak: MCP](https://www.keycloak.org/securing-apps/mcp-authz-server); [issue #14355](https://github.com/keycloak/keycloak/issues/14355); [discussion #35743](https://github.com/keycloak/keycloak/discussions/35743)). Native support is planned but not shipped. Until then, the de-facto standard workaround (in both Keycloak's own doc and the Go+Keycloak guide) is:

1. Create per-surface **client scopes** `mcp:tools`, `mcp:resources`, `mcp:prompts` (Optional type) in the `flows-funds` realm. (Mate's realm export lives at `infra/keycloak/realm-export/flows-funds-realm.json` — these scopes are added there.)
2. Add an **Audience protocol mapper** to each scope with `Included Custom Audience = https://<host>/mcp` (the same value the client sends as `resource`). This forces the correct `aud` claim into issued tokens.
3. Make the audience-bearing scope a **realm default scope** so DCR-registered clients inherit it automatically ([discussion #35743](https://github.com/keycloak/keycloak/discussions/35743)).
4. **DCR policy tuning:** Keycloak's default *Trusted Hosts* client-registration policy blocks arbitrary registrants. Relax/replace it and add `mcp:*` to *Allowed Client Scopes* — **but relaxing trusted-hosts has confused-deputy implications**, so pair it with per-client consent (§7).

> Status to track: when Keycloak ships native RFC 8707, the audience-mapper becomes redundant but harmless. Clients MUST keep sending `resource` regardless ([spec/authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization)).

**Registration alternatives — decision matrix:**

| Mechanism | Keycloak support today | Spec status | Use? |
|---|---|---|---|
| **Dynamic Client Registration (RFC 7591)** | ✅ at `/realms/flows-funds/clients-registrations/openid-connect` | Stable revs center on it; *draft* marks it deprecated-but-retained | **Adopt now** — broadest client compatibility |
| Client ID Metadata Documents (CIMD) | Experimental behind `--features=cimd` | Preferred forward path in *draft* | **Watch** — enable when stable; lower confused-deputy surface |
| Pre-registered static client | ✅ | Allowed | Use for the **in-app client only** (we control it) |

**Recommendation:** DCR now for external clients (with per-client consent), a single pre-registered confidential client for the in-app assistant, CIMD on the roadmap.

### 4.4 Least-privilege scopes per tool

The realm exposes a **minimal baseline** scope and steps up. Map tool families to scopes:

| Scope | Grants | Step-up? |
|---|---|---|
| `mcp:tools` | tool discovery + read-only tools (`*.list`, `*.get`, `*.events`, guidance) | baseline (in PRM) |
| `mcp:logs:write` | import/patch/delete logs, event editing, filters | step-up |
| `mcp:dashboards:write` | dashboard + share mutations | step-up |
| `mcp:modules:write` | install/uninstall, config/layout writes | step-up + consent |
| `mcp:jobs:write` | cancel/retry | step-up |
| `mcp:admin` | `mate.admin.*` cross-tenant (requires `admin` realm role too) | step-up + role |

Step-up mechanics: when a model first calls a privileged tool, the server returns `403` + `WWW-Authenticate: Bearer error="insufficient_scope", scope="mcp:logs:write"` ([spec/security-considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). Avoid wildcard/omnibus scopes (`*`, `full-access`); the server SHOULD accept down-scoped tokens. This bounds blast radius per tenant and per tool family, and pairs naturally with the per-module Keycloak scopes.

### 4.5 Per-tenant isolation (reuse Mate's `sub`-scoping verbatim)

This is the part Mate already does well; the MCP server must not weaken it.

- **Identity from the token, never the session.** `user_id = CurrentUser.id = sub` (`dependencies.py:165`), derived from the validated JWT on *every* request. The spec mandates this: "MCP Servers MUST NOT use sessions for authentication," and per-session data MUST be keyed `<user_id>:<session_id>` with `user_id` from the token, "not provided by the client" ([spec/security-considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). Mate's `request.state.user_id` pattern (`dependencies.py:181`) becomes a per-MCP-request resolved identity.
- **Row-level ownership.** Every tool that touches an owned resource calls `get_owned_event_log` / `get_owned_folder` / `get_owned_job` / `get_owned_watched_folder` (`auth/ownership.py`) — 404-on-mismatch (not 403) to avoid id enumeration. No new isolation code; the MCP tool is a thin caller of the existing service function.
- **Module RPC isolation.** Dynamic module tools (§5) resolve through `_UserScopedRegistry` (`loader.py:324`): a capability whose provider the user hasn't installed is treated as nonexistent (`LookupError` with a non-leaking message). This is the exact model the grounding recommends an MCP server "mirror when deciding which module tools a given `sub` may call."
- **Bus/SSE isolation invariant preserved.** Any tool that emits or streams (`mate.jobs.wait`, resource subscriptions) honours the `user_id`-stamped, server-side-filtered fan-out (`events_sse.py`, `jobs.py:40`). The MCP server never relaxes the "omitting `user_id` leaks to all users" invariant.

**Gap to close (named in the grounding):** module *routes* authenticate but skip the `user_owns_module` check — `_bind_route` (`loader.py:1024`) builds context and invokes the handler with only a *log* ownership gate (`_make_context`, `loader.py:1334`); the `user_owns_module` 404 gate exists only on **meta** routes (`modules.py:54`, `_assert_owns_module`). A non-owner who knows a module id and owns a log can reach `/api/v1/modules/{id}/{route}` in-process. **The MCP layer MUST re-assert module ownership before exposing any module tool** — concretely, the dynamic-tool resolver only emits tools for modules in `user_module_ids(session, user_id)` (`installs.py:32`) and re-checks `user_owns_module` at `tools/call` time. (Recommend also closing this at the loader level, but the MCP layer must not depend on that being done.)

### 4.6 Demo mode

`demo_mode` (`config.py:152`) accepts the literal `demo-access-token` as a fixed user with no JWKS (`dependencies.py:157`). The MCP server **MUST refuse to run with `demo_mode` enabled in any externally-reachable deployment** — it is "a non-production posture" (grounding). Concretely: the `/mcp` mount checks `settings.demo_mode` at startup and either refuses to bind or restricts to loopback. The in-app client may use it in local dev only.

---

## 5. Dynamic, per-user module tools

This is the feature that makes the MCP server reflect *each user's* Mate, not a static catalog.

### 5.1 Source of truth (already exists)

The per-user visible set is computed exactly as `ai_nav.build_user_destinations` (`ai_nav.py:228`) computes nav destinations:

1. `loader.manifests()` (`loader.py:764`) — every loaded module's `Manifest` (id, name, category, `provides`/`consumes`, `is_confidential_safe`, has-frontend).
2. Intersect with `user_module_ids(session, user_id)` (`installs.py:32`) — only modules this user installed.
3. Filter by `ModuleConfig.enabled` (default `m.default_enabled`) — only enabled ones.
4. For each surviving module, enumerate its `@route`/`@job` handlers.

The handler enumeration is **already neutral and serialized**: the subprocess `ready` message ships a pure-JSON handler descriptor `{attr, route:{method,path,name}, on_event:{topic}, job:{progress,priority,cancellable,result_url,title,subtitle}}` (`subprocess_worker.py:111-132`; reconstructed host-side at `subprocess_host.py:75-120`). For in-process modules the loader reads the same decorator metadata via `get_route_spec`/`get_job_spec` (`loader.py:1011-1022`). The MCP dynamic-tool generator reads this same metadata — no new enumeration mechanism needed.

### 5.2 Mapping a module `@route`/`@job` to an MCP tool

For module `performance` (provides `perf.kpis`, `perf.bottlenecks`, …), with `@route.get("/kpis")`:

- Tool name: `mate.module.performance.kpis`.
- `inputSchema`: synthesised from the handler's extra params (the loader already extracts these as typed query params — `_extra_handler_params`, `loader.py:466-505`; Pydantic models become object schemas). `log_id` becomes a required input where the route needs a log.
- A `@route` **stacked with `@job`** maps to an **async tool**: it returns `{job_id}` immediately (`loader.py:1062-1137`), annotated `x-mate-async: true`. The model is expected to follow with `mate.jobs.wait`. The `JobSpec.result_url` (`decorators.py:43`) becomes an `x-mate-result-uri` annotation pointing at the result resource.
- `x-mate-confidential-safe` = the module's `is_confidential_safe` (`manifest.py:227`) — lets a privacy-conscious client/user filter to confidential-safe tools only, mirroring the existing "Show only confidential modules" toggle.
- `x-mate-capability` = the `provides` capability id, so the tool description can say what it computes without leaking other tenants' installs.

**Two execution paths, one tool contract:** A module tool's implementation calls the module handler the same way the REST route does — through `loader._make_context` + `_invoke_handler` for in-process, or the subprocess bridge's `call_handler` (`subprocess_host.py:336`) for `isolation: subprocess`. DataFrames never cross MCP; results are already JSON-native (or the module wrote a cache entry the tool returns a reference to). The MCP layer adds nothing to the data plane — it reuses the Parquet-handoff / JSON boundary unchanged.

### 5.3 Keeping the per-user tool list live

Per spec, declare `"tools": { "listChanged": true }` and emit `notifications/tools/list_changed` when the set changes ([spec/tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)). Triggers in Mate:

- User installs/uninstalls a module (`record_install` / `remove_install`, `installs.py`).
- User enables/disables a module (`ModuleConfig.enabled` change).
- Loader hot-reload adds/removes a module (`hot_reload.py`, dev) or an install job completes (`install_jobs.py`).
- Admin policy changes module availability.

Mechanism: the loader/install paths already publish bus events; the MCP server subscribes (per `user_id`, reusing the isolation filter) and pushes `tools/list_changed` on that user's open SSE stream(s).

**Hard constraints from the research:**
- **Client support is uneven** — not all clients honour `list_changed` mid-session ([gemini-cli #13850](https://github.com/google-gemini/gemini-cli/issues/13850)). **Design so a fresh connection always yields the correct per-user set**; treat live `list_changed` as an enhancement, never a correctness dependency.
- **`list_changed` is an abuse vector under session hijacking** — "a client could end up with tools they were not aware were enabled" ([spec/security-considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). Therefore the per-user tool set is resolved **from the validated token's `sub` + scopes on every `tools/list`**, never from session state alone.
- Anthropic's connector tolerates dynamic tools by design (missing tool named in `configs` → backend warning, no error) and offers `defer_loading` + a tool-search tool for large/variable sets ([Claude: MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)) — useful because a power user's install set can produce many module tools.

---

## 6. The integrated assistant on the same MCP server (unifying the AI layer)

### 6.1 Today

Two parallel legs, fired per chat turn (grounding `ai-auth-registry`):
- **Intent → navigation:** `POST /api/v1/ai/route` → `ai_nav.route_intent` (`ai_nav.py:835`): destination registry → deterministic pre-filter → LLM classifier (`ROUTING_SCHEMA`) → resolve to hrefs. Output is *target ids to navigate to*, never a server action. The only mutation is a whitelist of client-side cosmetic settings (`SETTING_WHITELIST`, `ai_nav.py:391`).
- **Grounded chat:** `POST /api/v1/ai/chat` (`routes/ai.py:382`) — SSE advice, system prompt forbids claiming it navigated. Optional grounding from module `guidance_payload()` and (consented) process stats.

`routes/ai_guidance.py` uses provider tool-use, but only to *coerce output shape* (`emit_guidance`, `emit_column_mapping`) — "No model output is ever dispatched to a platform route." Net: **router + advisor, cannot act.**

### 6.2 Target: in-app assistant is an MCP client of our own server

Replace the bespoke two-leg pipeline with a single tool-calling loop where the in-app assistant is an **in-process MCP client** of the same `/mcp` server.

```
 user turn → in-app assistant (LLM, server-side, holds the user's Keycloak session)
            → MCP client → /mcp (same server)
                 - mate.ai.navigate   (wraps ai_nav.route_intent — still returns chips)
                 - mate.logs.*, mate.module.*, mate.dashboards.*  (NEW: it can ACT)
                 - resources/prompts   (in-app-only affordances)
```

- **Navigation is preserved, not discarded.** `ai_nav.route_intent` becomes the `mate.ai.navigate` tool. The deterministic pre-filter (`prefilter`, `ai_nav.py:580`) and cheap classifier still short-circuit pure-nav turns — wrapping it in a tool keeps the cost discipline. The in-app client renders the returned targets as the same additive chips. So the *navigation UX is unchanged*; it's just one tool now.
- **The whitelist-only mutation model is superseded by scoped tools.** `SETTING_WHITELIST` (cosmetic, client-side) was the safe boundary precisely because there were no real action tools. With MCP + least-privilege scopes + ownership re-assertion, the assistant can safely *do* things (import a log, run performance KPIs, create a dashboard) under the same auth as the REST API. The "deliberately absent and rejected" sensitive operations (`ai_nav.py:376`) stay rejected — now by *scope*, not by absence.
- **Grounding becomes resource reads.** `guidance_payload()` context and process stats (`routes/ai.py:267`, `:245`) are exposed as resources/prompts the in-app client pulls, instead of bespoke `_build_context_block` plumbing.

### 6.3 Auth for the in-app path

The in-app client already holds the user's Keycloak session (Auth.js v5, `apps/web/auth.ts`; rotated in the `jwt` callback). It can mint/forward a correctly-audience-bound MCP token *without a separate browser OAuth dance* — but the MCP server **MUST still validate `aud` exactly as for external clients** ([spec/authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization)). Two options:

| Option | How | Trust | Verdict |
|---|---|---|---|
| **Token exchange** | Backend exchanges the user's `flows-funds-api` token for one with `aud=mcp-url` via Keycloak token-exchange | Correct OAuth; one audience per RS | **Recommend** |
| Dual-audience token | Issue the session token with both `flows-funds-api` and the MCP URL in `aud` | Simpler but couples the two RS | Acceptable interim |

Either way, **do not trust the in-app client more than an external one for token validation** — same `aud`/`iss`/`exp`/signature checks.

### 6.4 Decision matrix: keep `ai_nav` vs full MCP re-platform

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Keep `ai_nav` two-leg, add MCP only for external clients | No churn to a working assistant | Two AI codepaths to maintain; in-app assistant stays action-less; grounding logic duplicated | Reject (long-term) |
| **Re-platform in-app assistant onto MCP; `ai_nav` becomes the `mate.ai.navigate` tool** | One AI layer; in-app assistant gains scoped actions; nav cost-discipline preserved; grounding via resources | Real migration effort; need an in-process MCP client; careful scope defaults | **Recommend (phased)** |
| Rip out `ai_nav` entirely, let the LLM navigate via generic tools | Maximal simplicity | Loses the deterministic pre-filter's cost/latency win; nav becomes an LLM round-trip | Reject — keep `ai_nav` *inside* the tool |

---

## 7. Security defenses (multi-tenant threat model)

All normative; each tied to a concrete Mate mechanism.

**1. Audience binding & no token passthrough (the #1 rule).** The server MUST accept only tokens with itself in `aud` and MUST reject others ([spec/security-considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). **Token passthrough is explicitly forbidden** — because the MCP server calls Mate's service layer *in-process* (not via HTTP to `/api/v1`), there is **no downstream token to pass through**: it resolves the user once and calls Python functions. If a future tool must call a *separate* upstream API, the server acts as its own OAuth client and obtains a distinct token (token exchange) — never forwarding the MCP client's token.

**2. Confused deputy.** Arises when a proxy uses a static client-ID to a third-party AS while letting clients dynamically register, and a leftover consent cookie lets an attacker skip consent and capture the code via a malicious `redirect_uri` ([spec/security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)). **This directly bites our DCR relaxation (§4.3).** Mandatory mitigations: obtain user consent for *each* dynamically-registered client before forwarding; per-client-ID consent storage checked before the flow; **exact `redirect_uri` string matching**; `__Host-` signed consent cookies (`Secure`/`HttpOnly`/`SameSite=Lax`); `X-Frame-Options: DENY` / CSP `frame-ancestors`; set the `state` cookie only *after* consent. Prefer letting **Keycloak own the consent screen** (it already does for OIDC clients) rather than building a proxy consent layer.

**3. Tool poisoning / prompt injection / rug pulls.** Tool descriptions and annotations are untrusted model input; clients MUST treat annotations as untrusted unless from a trusted server ([spec/tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)). Concrete rules for a multi-tenant server *we* operate:
   - **Tool descriptions are static and tenant-neutral** — never interpolate user data, log names, or another tenant's content into tool metadata (§3.3). A module's `name`/`description` from its manifest is the *author's* text, shown as the tool title; it is not another *tenant's* data, but it is still third-party (uploaded modules) — sanitise/escape and never let it carry instructions that the host treats specially.
   - **Tool results are strictly tenant-scoped** — a result can contain user content (e.g. an event log row), so the client must treat tool *output* as untrusted data, not instructions. Document this for the in-app client; it is out of our control for external clients but is their responsibility per spec.
   - **`list_changed` must not silently swap tool semantics** — tool identity (name → behaviour) is stable; an install only *adds/removes* tools, never repurposes a name.
   - Server MUST validate all tool inputs (reuse the Pydantic schemas), enforce access controls (scopes + ownership), rate-limit, and sanitise outputs.

**4. Session security.** Sessions are not auth (§4.5). Session IDs are secure-random UUIDs; session data keyed `<user_id>:<session_id>` with `user_id` from the token. A guessed session ID cannot impersonate another tenant.

**5. Transport/AS hygiene.** Validate `Origin` (DNS rebinding); all redirect URIs HTTPS (loopback exempt); PKCE S256 mandatory and refuse if AS metadata omits `code_challenge_methods_supported`; validate `iss` (RFC 9207) and exact `redirect_uri`. If the server ever fetches client-supplied URLs (CIMD/PRM), guard SSRF (block private/link-local incl. `169.254.169.254`, enforce HTTPS, egress proxy).

**6. Rate limiting & resource fairness.** The MCP layer must inherit Mate's existing per-user fairness mechanisms — `max_offloads_per_user` (`config.py:94`), `job_execution_timeout_seconds` (`config.py:72`), worker concurrency — so a model that fires many async tools cannot starve other tenants. Add a per-`sub` MCP request rate limit on top.

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Keycloak lacks native RFC 8707 (≥26.5) | High | Audience-mapper workaround (§4.3); validate `aud` server-side; track native support |
| DCR relaxation → confused deputy | High | Per-client consent, exact `redirect_uri`, `__Host-` cookies; prefer Keycloak-owned consent (§7.2) |
| Module routes skip `user_owns_module` (`loader.py:1024`) | High | MCP re-asserts ownership at list + call time (§4.5); recommend loader-level fix too |
| Clients ignore `tools/list_changed` | Medium | Correct set on every fresh connect; treat live update as enhancement (§5.3) |
| External clients only consume tools | Medium | Tools-first surface; resources/prompts in-app only (§3.1) |
| Tool/result injection into the model | Medium | Static tenant-neutral descriptions; treat output as data; scope + validate (§7.3) |
| `demo_mode` reachable externally | High | Refuse `/mcp` bind under `demo_mode` except loopback (§4.6) |
| Many module tools per power user bloat `tools/list` | Low | `defer_loading` + tool-search; scope filtering; confidential-safe filter (§5.3) |
| In-app re-platform regresses nav UX/cost | Medium | Wrap `ai_nav.route_intent` as a tool, keep deterministic pre-filter (§6.2) |
| Resource starvation via async tool spam | Medium | Inherit per-user offload/job caps + per-`sub` rate limit (§7.6) |
| Migration churn in `routes/ai.py`, `ai_nav.py` | Medium | Phase it; keep REST `/ai/*` until in-app MCP path proven (§9) |

---

## 9. Phased implementation outline (phases only — no code)

**Phase 0 — Keycloak realm prep (no Mate code).**
Add `mcp:tools`/`mcp:resources`/`mcp:prompts`/`mcp:logs:write`/… client scopes + Audience protocol mappers (`aud = mcp-url`) to `infra/keycloak/realm-export/flows-funds-realm.json`. Set the baseline scope as realm-default. Configure DCR policy + Allowed Client Scopes. Validate a hand-issued token carries the right `aud`.

**Phase 1 — Resource Server skeleton (read-only).**
Mount `/mcp` (Streamable HTTP) in `main.py`. Implement PRM (`/.well-known/oauth-protected-resource`), the 401+`WWW-Authenticate` challenge, `Origin` validation, and token validation reusing `auth/jwks.py` + a `keycloak_mcp_audience` setting. Ship a handful of read-only static tools (`mate.logs.list/get`, `mate.jobs.list/get`) calling existing service functions through `get_owned_*`. Verify with Claude Desktop/web and the Messages API connector.

**Phase 2 — Full static core + async tools.**
Add the write/async static tools (§3.2), driven by the existing Pydantic schemas. Implement `mate.jobs.wait` over the job SSE. Wire least-privilege scopes + step-up 403 challenges. Add per-`sub` rate limiting and inherit job/offload fairness caps.

**Phase 3 — Dynamic per-user module tools.**
Generate tools from `loader.manifests()` ∩ `user_module_ids` ∩ enabled, mapping `@route`/`@job` → tools (§5.2) and re-asserting `user_owns_module`. Resolve via `_UserScopedRegistry`. Wire `tools/list_changed` to install/enable/hot-reload bus events. Add `defer_loading`/tool-search affordances. Close the `loader.py:1024` ownership gap at the loader level in parallel.

**Phase 4 — Integrated assistant re-platform.**
Stand up the in-process MCP client. Re-home `ai_nav.route_intent` as `mate.ai.navigate` (keep the deterministic pre-filter). Move grounding to resources/prompts. Run the in-app assistant through the tool-calling loop with conservative default scopes. Keep REST `/ai/chat`/`/ai/route` live behind a flag until parity is proven, then deprecate.

**Phase 5 — Hardening & ops.**
Confused-deputy consent flow (or delegate to Keycloak consent), SSRF guards if CIMD is enabled, audit logging keyed by `sub` + tool + scope, admin observability of MCP sessions. Track Keycloak native RFC 8707 and CIMD; migrate off the audience-mapper / DCR when they land.

---

## 10. File reference index (exact Mate touchpoints)

- **Auth (reuse verbatim):** `apps/api/src/mate/api/auth/dependencies.py` (`get_current_user_from_token:155`, `_decode_token:86`, `require_admin:194`), `auth/jwks.py` (`get_signing_key`), `auth/ownership.py` (`get_owned_*`).
- **Config:** `apps/api/src/mate/api/config.py` (`keycloak_issuer:132`, `keycloak_jwks_url:136`, `keycloak_audience:140`, `demo_mode:152`) — add `keycloak_mcp_audience`.
- **App mount:** `apps/api/src/mate/api/main.py` (CORS `:355`; add `/mcp` mount + OpenAPI invalidation already at `loader.py:619`).
- **AI layer (re-home):** `apps/api/src/mate/api/ai_nav.py` (`route_intent:835`, `build_user_destinations:228`, `prefilter:580`, `classify_intent:702`, `resolve_targets:738`, `SETTING_WHITELIST:391`), `apps/api/src/mate/api/routes/ai.py` (`route:351`, `chat:382`), `apps/api/src/mate/api/routes/ai_guidance.py`.
- **Loader / registry / ownership (dynamic tools + isolation):** `apps/api/src/mate/api/modules/loader.py` (`manifests:764`, `_UserScopedRegistry:324`, `_bind_route:1024` [ownership gap], `_make_context:1334`, `user_can_read_log` use at `:1401`), `modules/registry.py` (`CapabilityRegistry`), `modules/installs.py` (`user_module_ids:32`, `user_owns_module:39`), `routes/modules.py` (`_assert_owns_module:54`).
- **SDK surface (tool schema source):** `packages/module-sdk-py/src/mate/sdk/manifest.py` (`provides:211`, `consumes:212`, `is_confidential_safe:227`), `decorators.py` (`RouteSpec:24`, `JobSpec:37`), `context.py` (`ModuleContext`).
- **Subprocess handler descriptor (neutral JSON contract):** `apps/api/src/mate/api/modules/subprocess_worker.py:111-132`, `subprocess_host.py:75-120`, `:336` (`call_handler`).
- **Async/streaming (job tools, resource subscriptions):** `apps/api/src/mate/api/routes/events_sse.py`, `routes/jobs.py:147` (`stream`), `jobs/runtime.py`, `events/bus.py`.
- **Static inventory source:** `apps/api/src/mate/api/routes/__init__.py` (router list), `/openapi.json` (kept fresh on module load/unload, `loader.py:619`).
- **Keycloak realm:** `infra/keycloak/realm-export/flows-funds-realm.json` (realm `flows-funds`, web client `flows-funds-web`, audience `flows-funds-api`).

---

## Appendix A — Cited external research (URLs preserved)

MCP spec & security:
- Transports — https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- Authorization — https://modelcontextprotocol.io/specification/draft/basic/authorization
- Security considerations — https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations
- Security best practices — https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- Tools — https://modelcontextprotocol.io/specification/2025-06-18/server/tools

RFCs:
- Protected Resource Metadata (RFC 9728) — https://datatracker.ietf.org/doc/html/rfc9728

Keycloak + MCP:
- Keycloak: Integrating with MCP — https://www.keycloak.org/securing-apps/mcp-authz-server
- Keycloak client registration — https://www.keycloak.org/securing-apps/client-registration
- Keycloak 26.5 release notes — https://www.keycloak.org/2026/01/keycloak-2650-released
- RFC 8707 gap (issue) — https://github.com/keycloak/keycloak/issues/14355
- Audience-mapper / default-scope (discussion) — https://github.com/keycloak/keycloak/discussions/35743
- Go+Keycloak OAuth 2.1 practical guide — https://medium.com/@wadahiro/protecting-mcp-server-with-oauth-2-1-a-practical-guide-using-go-and-keycloak-7544eb5379d3

Claude clients:
- MCP connector (tools-only; defer_loading; dynamic tools tolerated) — https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Custom connectors getting started — https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp

Client `list_changed` support gaps:
- gemini-cli tracking issue — https://github.com/google-gemini/gemini-cli/issues/13850
- IBM mcp-context-forge user-context tool lists — https://github.com/IBM/mcp-context-forge/issues/2171
