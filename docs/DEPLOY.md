# Deploying Mate to the uni VM

Production deployment to `pm-mate.uni-muenster.de`, fronted by the FB4 reverse
proxy. For the local-`localhost` setup, see [`README.md`](../README.md) – this
doc only covers the server. **All VM access needs the FB4-DEV-VPN.**

## Quick reference (cheat sheet)

The daily-driver commands. The numbered walkthrough below is the full,
first-time setup. The prod overlay is always **both** compose files:

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
```

```bash
# Connect (compose stack lives in ~/mate)
ssh -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"
ssh-copy-id -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"   # once, to skip the password

# Deploy
make deploy                      # from laptop: push branch + redeploy + health-check
./scripts/deploy.sh --no-push    # redeploy origin's current state, no push
cd ~/mate && git pull && $DC up -d --build   # manual, on the VM

# Run / stop / status
$DC up -d --build        # bring up (first boot ~10 min: module deps)
$DC ps                   # status
$DC restart api          # restart one service
$DC restart proxy        # after a Caddyfile-only change
$DC down                 # stop (keeps volumes/data)
$DC down -v              # stop + WIPE Keycloak users (re-imports realm next boot)

# Logs
$DC logs -f api                 # follow API
$DC logs --tail=80 api web      # last 80 lines
$DC logs -f proxy keycloak
```

- `NEXT_PUBLIC_*` change → needs `--build` (inlined into the client bundle).
- Caddyfile-only change → `$DC restart proxy`.

## How the two proxies fit together

```
        Internet
           │  https://pm-mate.uni-muenster.de  (public cert on the uni proxy)
           ▼
   ┌──────────────────┐
   │  uni edge proxy  │   forwards :80 and :443  ──►  the VM's :443
   └──────────────────┘
           │  HTTPS (one pipe, one port)
           ▼
   ┌────────────────────────────────────────────────────────┐
   │  VM :443  →  proxy container (Caddy)                     │
   │  terminates TLS with the VM's own Let's Encrypt cert   │
   │  and routes by path:                                     │
   │    /api/v1/*  + /health   →  api:8000                    │
   │    /auth/*                →  keycloak:8080               │
   │    everything else        →  web:3000  (incl. /api/auth) │
   └────────────────────────────────────────────────────────┘
```

The uni edge proxy only knows "this hostname → that one port"; it can't split
your three services. The **on-VM Caddy container does that split** and
terminates TLS using the cert the uni dropped at
the VM's Let's Encrypt live dir (`$TLS_CERT_DIR`, set in `.env`). The public cert
(`pm-mate`) lives on the uni proxy; the VM cert just secures the
proxy↔VM hop, which is why Caddy binds by port and ignores the hostname
mismatch.

Result: the browser talks to a **single same-origin** `https://pm-mate.uni-muenster.de`,
so the per-port CORS and `/etc/hosts` Keycloak hacks from the local setup are gone.

## Prerequisites

- Access to the department **dev VPN** (Cisco Secure Client; URL, username and OTP are in the team's internal deployment notes).
- SSH to the VM: `ssh -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"`.
  The real host, port and login are **not in this public repo** - put them in
  `scripts/deploy.env` (copy `scripts/deploy.env.example`); `scripts/deploy.sh`
  sources that file and refuses to run without it.
- Docker Engine + Compose v2 (≥ 2.24 for the `!reset`/`!override` merge tags) on the VM.
- The cert files present at `$TLS_CERT_DIR/{fullchain,privkey}.pem` and readable by the Docker daemon (root) – they are, by default. Certbot auto-renews them; Caddy re-reads on restart.

> **Image size note:** the api image bakes a Temurin 21 JRE (~150 MB layer) for
> JVM modules (`runtime: {kind: jvm}`, see `modules/PROTOCOL.md`). First pull
> after upgrading grows by that layer once; nothing else changes. JVM module
> jars (e.g. `modules/performance_java/dist/`) ship in the repo – no build
> tooling needed on the VM.

## Files this deployment adds

| File | Purpose |
| --- | --- |
| [`docker-compose.prod.yml`](../docker-compose.prod.yml) | Prod overlay – adds the proxy, repoints URLs, drops public ports + the macOS cv4cdd mount. |
| [`infra/caddy/Caddyfile`](../infra/caddy/Caddyfile) | TLS termination on `:443` + path routing. |
| `.env` (you create it on the VM – see §4) | Rotated secrets + Keycloak admin creds. |

## 0. Smoke-test the `:443` pipe first

Before deploying the app, confirm the uni forwarding + cert chain work
end-to-end. On the VM, serve a one-line page on `:443` with the real cert
(this exercises the same `tls <cert> <key>` mechanism production uses):

```bash
cat > /tmp/hello.Caddyfile <<'EOF'
{
	auto_https off
}
:443 {
	tls {$TLS_CERT_DIR}/fullchain.pem {$TLS_CERT_DIR}/privkey.pem
	respond "hello from pm-mate"
}
EOF

docker run --rm -p 443:443 \
  -v /etc/letsencrypt:/etc/letsencrypt:ro \
  -v /tmp/hello.Caddyfile:/etc/caddy/Caddyfile:ro \
  caddy:2-alpine
```

From your laptop (outside the uni net): `curl https://pm-mate.uni-muenster.de`.
Seeing `hello from pm-mate` means the pipe is good. `Ctrl-C` to stop, then
proceed.

## 1. Clone on the VM

```bash
ssh -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"
git clone <repo-url> ~/mate && cd ~/mate
```

`docker-compose.prod.yml` and `infra/caddy/Caddyfile` come with the repo.
(`~/mate` is the path `scripts/deploy.sh` expects – override with `DEPLOY_DIR`
if you clone elsewhere.)

## 2. Secrets + realm – one command

`infra/bootstrap-vm.sh` does the two fiddly bits in one go (covers §4 too):

```bash
sudo apt install -y jq        # the script needs jq
./infra/bootstrap-vm.sh
```

It (1) generates `.env` with fresh secrets, (2) patches the realm so the prod
domain is allowed and the client secret matches `.env`, and (3) starts the
stack (`docker compose … up -d --build`) after a free-port check – so it
covers §4 and §5 too. Pass `--no-start` to only write config. It's idempotent
and prints the Keycloak admin login at the end. Then jump to §6 (verify).

### Why this is needed / what it touches

Two things must line up or login fails:

- **Client secret** – the web app authenticates to Keycloak with a shared
  secret. It lives in **both** `.env` (`KEYCLOAK_CLIENT_SECRET`) and the realm
  JSON (`secret`); they must be **identical**. The script generates one value
  and writes it to both.
- **Redirect URI / web origin** – Keycloak only redirects back to URLs that are
  pre-approved. The default is `localhost:3000`; the script adds
  `https://pm-mate.uni-muenster.de` (keeping localhost, so local dev still
  works). Without it: "Invalid redirect_uri".

The realm imports from `infra/keycloak/realm-export/flows-funds-realm.json`
**only on the first boot** (empty Keycloak DB). On a re-deploy the import is
skipped – change realm settings in the admin console at
`https://pm-mate.uni-muenster.de/auth/admin` (login = `KEYCLOAK_ADMIN` /
`KEYCLOAK_ADMIN_PASSWORD`) instead.

<details>
<summary>Manual alternative (no script)</summary>

In the `flows-funds-web` client of the realm JSON, before first boot:

- `redirectUris`: add `"https://pm-mate.uni-muenster.de/api/auth/callback/keycloak"`
- `webOrigins`: add `"https://pm-mate.uni-muenster.de"`
- `attributes."post.logout.redirect.uris"`: append `##https://pm-mate.uni-muenster.de/login##https://pm-mate.uni-muenster.de/`
- `rootUrl` / `baseUrl`: set to `https://pm-mate.uni-muenster.de`
- `secret`: replace with a fresh value and use the **same** one for `KEYCLOAK_CLIENT_SECRET` in `.env` (§4).
</details>

### University login (skip the Keycloak form)

To send every login straight to the university's OIDC IdP and never render
Keycloak's own login page:

1. Register an OIDC client with the university. The redirect URI is Keycloak's
   broker callback, whose last path segment is the IdP **alias** (not the
   protocol):
   `https://pm-mate.uni-muenster.de/auth/realms/flows-funds/broker/keycloak-oidc/endpoint`
   Keep the Keycloak IdP alias equal to that segment (`keycloak-oidc`) so this
   URI never has to be re-registered.
2. Configure the realm (IdP + redirect + silent first login) on the **running**
   Keycloak – the realm JSON only imports into an empty DB:
   ```bash
   UNIV_CLIENT_ID=... UNIV_CLIENT_SECRET=... \
   UNIV_DISCOVERY_URL=https://idp.uni-muenster.de/.../.well-known/openid-configuration \
   KC_SERVER=http://localhost:8080/auth \
     ./infra/keycloak/configure-university-idp.sh   # IDP_ALIAS defaults to keycloak-oidc
   ```
3. Set `KEYCLOAK_IDP_HINT=keycloak-oidc` in `.env` (already defaulted in
   `docker-compose.prod.yml`) and restart the `web` service.

Break-glass: the local `admin@flows-funds.local` user still works via the
Keycloak admin console (`master` realm), which the redirect does not touch.

## 3. cv4cdd model (optional)

The base compose mounts a macOS-only path for the cv4cdd model; the prod
overlay drops it. If you want cv4cdd on the VM, copy the model onto the host
and add a mount under `api.volumes` in `docker-compose.prod.yml`:

```yaml
    volumes: !override
      - ./data:/app/data
      - ./modules:/app/modules
      - /srv/flows-funds/cv4cdd_model:/srv/flows-funds/cv4cdd_model:ro
```

Then set its `model_path` under **Settings → Modules → Temporal Dynamics** to
that absolute path. Otherwise just disable the cv4cdd module in the UI.

## 4. Create `.env` (rotated secrets)

```dotenv
# Public origin / Auth.js
AUTH_SECRET=<openssl rand -base64 32>

# Keycloak (the web client's confidential secret – must equal the realm JSON)
KEYCLOAK_CLIENT_SECRET=<same fresh secret as in the realm JSON>

# Keycloak Postgres + admin console
KEYCLOAK_DB_PASSWORD=<fresh>
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=<fresh – this is your admin console login>
```

The URL-derived, non-secret settings (issuer, JWKS, CORS, `NEXT_PUBLIC_API_URL`,
`AUTH_URL`, Keycloak hostname/relative-path) are all set in
`docker-compose.prod.yml`, so they don't belong in `.env`.

## 4a. Resource limits & multi-tenant fairness (optional)

One VM is shared across all tenants. These knobs bound how much CPU/RAM one user's
heavy module mining can take and stop a runaway offload from starving others. All
optional with safe defaults – add to `.env` only to tune. Env names are the
uppercased field names (case-insensitive).

```dotenv
# CPU offload (ctx.run_in_process – pm4py/networkx mining runs in its own process,
# one short-lived process per call, hard-killable by the job timeout).
MODULE_PROCESS_POOL_SIZE=4    # max concurrent offload processes (default min(cores, 8))
MAX_OFFLOADS_PER_USER=2       # per-tenant offload cap (default 0 = pool size, i.e. no per-user limit)

# A job (and its offloads) past this many seconds is force-stopped – now a real
# SIGKILL of the offload process, not just a flipped DB row.
JOB_EXECUTION_TIMEOUT_SECONDS=1800   # default 1800 (30 min); lower if every job is known-fast

# Parallel asyncio job slots (admin can also change this live at Settings → Jobs).
WORKER_CONCURRENCY=2          # default 2

# DuckDB, per query – stop one big-log query or a many-card dashboard from grabbing
# every core / all RAM.
DUCKDB_THREADS=4              # default 0 = all cores
DUCKDB_MEMORY_LIMIT=4GB       # default empty = DuckDB's own default
```

On a shared box set `MAX_OFFLOADS_PER_USER` **below** `MODULE_PROCESS_POOL_SIZE` so no
single user can hold every offload slot, and cap `DUCKDB_THREADS` / `DUCKDB_MEMORY_LIMIT`
so one query can't monopolise cores or RAM. Leaving them unset keeps single-tenant
behaviour (no per-user throttling).

## 4b. Graph sidecar (Neo4j, optional)

The `actor_performance` module (Event-Knowledge-Graph performance decomposition) needs a
Cypher engine. It ships as an optional compose **profile** – the default stack is unchanged.

```dotenv
# .env – enable the profile and rotate the password
COMPOSE_PROFILES=graph
NEO4J_PASSWORD=<openssl rand -hex 16>
# Heap for the graph engine. 2G default handles small/medium logs; the BPIC17
# paper dataset (~1.2M events) wants ~10G – size against the VM's free RAM.
NEO4J_HEAP=4G
```

Facts worth knowing:

- Stock `neo4j:5.26-community` + APOC **core** (auto-installed via `NEO4J_PLUGINS`). No
  APOC Extended, no custom image – the module imports via plain `LOAD CSV`.
- bolt (7687) is **never public**: loopback-only in the base file (for host-mode
  `make dev`), `!reset` to nothing in prod; the api container uses `bolt://neo4j:7687`.
- Data handoff is the shared `./data/neo4j-import` directory (api writes prepared CSVs,
  the server reads them as its import dir).
- Community edition = one database. The module uses the graph as **transient per-run
  scratch** (wipe → build → analyze → wipe) and serializes runs behind a module-global
  lock, so tenant isolation holds: results land only in the per-user module cache.
- Module connection defaults come from `MATE_NEO4J_URI` / `MATE_NEO4J_PASSWORD` /
  `MATE_NEO4J_IMPORT_DIR` (already wired in the compose api service); per-user module
  settings can override them.
- Host-mode dev without the compose stack: see `modules/actor_performance/README.md`
  for the single `docker run` command.

## 4c. S3 storage backend (optional – keep the VM volume small)

By default every event log and module result lives on the `./data` bind-mount,
so the VM volume grows with total-data-ever. Pointing the platform at an
S3-compatible bucket (uni Ceph RGW, AWS S3, MinIO, …) makes the bucket the
authoritative store and turns local disk into a bounded working cache.

The backend is configured **only** via `.env` – there is no admin UI and no
DB-stored config. Full variable reference + operational flows (enable on an
existing VM, fresh-VM restore, switch back): `docs/S3_OFFLOAD.md`. Minimal
Ceph-RGW example:

```dotenv
STORAGE_MODE=s3
STORAGE_S3_ENDPOINT=https://rgw.uni-muenster.de
STORAGE_S3_BUCKET=pm-mate
STORAGE_S3_ACCESS_KEY=<key>
STORAGE_S3_SECRET_KEY=<secret>
# path-style + SSL-on are the defaults; set a prefix to share the bucket
STORAGE_S3_PREFIX=prod

# Actually reclaim disk: bound the local cache and (after a dry-run soak)
# enable deletes. Without a budget S3 mode only mirrors – the VM stays big.
LOCAL_CACHE_MAX_BYTES=53687091200   # 50 GiB working set
CACHE_EVICT_DRY_RUN=false
```

After flipping an existing deployment to `s3`, recreate the api (a `restart`
keeps the old env – it must be `up -d`), then push the already-imported data up
once:

```bash
$DC up -d api                                             # recreate with the new env
$DC exec api python -m mate.api.storage.migration check   # probe the bucket
$DC exec api python -m mate.api.storage.migration to_s3   # copy-only, re-runnable
```

## 5. Bring it up

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f api      # first boot installs module deps – up to ~10 min (cv4cdd pulls TensorFlow)
```

Subsequent boots reuse the cached wheels under `data/uv-python/` and start in
seconds.

## 5a. What does *not* work out of the box

`bootstrap-vm.sh` + `up -d --build` gets you the full platform: schema
migration, realm import, all bundled modules (venvs + esbuilt panels), and JVM
modules (the fat jar is committed, the Temurin JRE is baked into the api image
– no JDK on the VM). The list below is everything that still needs a hand on a
fresh VM. Nothing here is a bug; each is an opt-in or a per-deployment secret.

| # | Not live yet | What to do |
| --- | --- | --- |
| 1 | **Neo4j sidecar** – the `graph` profile is off | `COMPOSE_PROFILES=graph` + `NEO4J_PASSWORD` (+ `NEO4J_HEAP`) in `.env`, then `$DC up -d` (§4b). Without it `actor_performance` is installed and visible but fails on run (setup screen). It declares `consumes: []`, so no precompute → **no red jobs on import**. |
| 2 | **cv4cdd has no model** – `default_enabled: true` + `consumes: log.imported` | Every log import starts a detect job that **hard-fails without a model** (`modules/cv4cdd/module.py`, `_do_detect`). Upload a `.tar.zst` model, pin one admin-globally (Admin → Controls), or disable the module/card. |
| 3 | **No platform admin** – the seeded `admin@flows-funds.local` carries realm roles `[user, offline_access, uma_authorization]`, not `admin` | `/admin/*` stays closed until the `admin` realm role is assigned in the Keycloak console. |
| 4 | **University login points at placeholders** – the realm JSON ships IdP `keycloak-oidc` with `https://idp.uni-muenster.de/REPLACE/...`, and the prod overlay defaults `KEYCLOAK_IDP_HINT: keycloak-oidc` | The first login redirects into nothing. Either set `KEYCLOAK_IDP_HINT=` (empty) in `.env` to keep Keycloak's own form, or run `configure-university-idp.sh` with real credentials (§2 – **delete the imported IdP first**, Keycloak ignores `providerId` on update and the imported type is the wrong one). |
| 5 | **MCP server** – opt-in | `MCP_ENABLED=1`, `API_BASE_URL`, `MCP_OAUTH_CLIENT_ID=mate-mcp` in `.env`, then `$DC up -d api` – `restart` keeps the old env (see the MCP section). |
| 6 | **Keycloak admin client** – user deletion doesn't remove the Keycloak account | `configure-admin-client.sh` + the four `KEYCLOAK_ADMIN_*` env vars, else that step no-ops (all Mate-side data is still purged). |

## 6. Verify (in order – each step rules out one layer)

```bash
# 1. Next.js reachable through the proxy
curl -I https://pm-mate.uni-muenster.de

# 2. API reachable through the proxy (unauthenticated liveness)
curl https://pm-mate.uni-muenster.de/health

# 3. Keycloak issuer is the PUBLIC URL (not `keycloak`/localhost) – if this is
#    wrong, the OIDC login loop breaks
curl https://pm-mate.uni-muenster.de/auth/realms/flows-funds/.well-known/openid-configuration | grep '"issuer"'
#    expect: "issuer":"https://pm-mate.uni-muenster.de/auth/realms/flows-funds"
```

Then in a browser:

4. Log in with `admin@flows-funds.local` / `flowsfunds` (Keycloak forces a
   password reset) – exercises the full OIDC redirect chain.
5. **Upload a log, run a job, and open Mate AI.** This confirms WebSockets
   (live job/event updates) and SSE (AI streaming) survive *both* proxies. If
   live updates stall but everything else works, the uni proxy likely needs
   WebSocket/streaming passthrough enabled for this alias – that's the first
   thing to raise with FB4 IT.
6. **Confirm the realm-hardening one-shot ran.** The `keycloak-config` service
   patches `sslRequired` + session lifetimes once per deploy, then exits:
   `$DC logs keycloak-config` → expect
   `realm 'flows-funds' hardening applied: sslRequired=EXTERNAL …`. A non-zero
   exit leaves Keycloak + app running but the realm unpatched — fix
   `KEYCLOAK_ADMIN_PASSWORD` and re-run `$DC up -d`.
7. **Log in once in Safari, not only Chrome.** Safari enforces cookie
   size/eviction more strictly. The server-side session store (`SESSION_STORE_DIR`
   on the web service, §5) keeps the auth cookie ~64 B so the full Keycloak token
   never chunks — without it Safari drops a chunk and loops between the app and
   `/login` (Chrome tolerates the same chunked cookie). See Troubleshooting.

> **Staged rollout — internal OIDC back-channel (removes the TLS bypass).** The
> web container sets `NODE_TLS_REJECT_UNAUTHORIZED=0` to tolerate the cert-name
> mismatch on the server-side Keycloak hop. To remove it, in order: (1) uncomment
> `KEYCLOAK_INTERNAL_URL: http://keycloak:8080/auth/realms/flows-funds` on the web
> service, `$DC up -d`; (2) verify a full login **and** a token refresh (leave a
> tab idle past the access-token expiry, then click — must not bounce to `/login`)
> both succeed; (3) only then delete `NODE_TLS_REJECT_UNAUTHORIZED`. A wrong
> internal URL breaks login, and dropping the bypass first removes the fallback —
> never combine the steps.

## Updating a running deployment

### One-command deploy from your laptop (recommended)

With the VM clone in place and `.env` set, push-to-deploy from your machine
**while on the FB4-DEV-VPN**:

```bash
make deploy          # or: ./scripts/deploy.sh
```

This pushes the current branch, then over SSH does `git reset --hard origin/<branch>`,
rebuilds, restarts, and health-checks `https://pm-mate.uni-muenster.de/health`.
A cloud GitHub Action can't do this – GitHub's runners aren't on the VPN, so
they can't reach the VM's SSH port. The deploy clone mirrors git; your secrets
stay safe in the gitignored `.env` (untouched by the reset). Override the
target with `DEPLOY_HOST` / `DEPLOY_DIR` / `DEPLOY_BRANCH` env vars if needed,
and run `ssh-copy-id -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST"` once to skip
the password prompt.

### Or manually on the VM

```bash
cd mate && git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- Changing `NEXT_PUBLIC_API_URL` (or any `NEXT_PUBLIC_*`) requires `--build` – it's inlined into the client bundle.
- After editing only `infra/caddy/Caddyfile`: `docker compose -f docker-compose.yml -f docker-compose.prod.yml restart proxy`.
- The realm JSON is **not** re-imported once the Keycloak DB exists – change realm settings in the admin console, or `docker compose down -v` to wipe the Keycloak volume and re-import (this also drops all Keycloak users).

## MCP server (external AI tools on the platform)

This section is the **operator** view (enable, Keycloak, proxy). The
**consumer** reference – full tool catalog, scopes, client connection snippets,
conventions, troubleshooting – is [`MCP.md`](./MCP.md).

Opt-in. When enabled, the API mounts a [Model Context Protocol](https://modelcontextprotocol.io)
server at `/mcp` (streamable HTTP) for external MCP clients – Claude Code,
Claude Desktop / claude.ai, Codex, a customer's own agent. Since v2 this is the
**full platform, not a read-only viewer**: curated read tools plus scope-gated
write/control tools (import/rename/filter/delete processes, dashboards, job
control, watched folders, module config, account) and an OAuth-only `admin`
toolset. Raw event rows are never exposed by construction – tools run against
restricted module contexts that serve precomputed results and aggregates only
(the same wall as Mate AI).

- **Toolsets** (`MCP_TOOLSETS`, csv of
  `meta,processes,analysis,dashboards,jobs,watched,account,admin`): empty = all
  except `admin`; `all` = everything. Registration is boot-time – changing it
  needs an api recreate. `admin` is additionally request-gated (OAuth token
  with `admin` scope **and** `admin` realm role; a PAT can never reach it).
- **Scopes** (source of truth: `apps/api/src/mate/api/mcp/scopes.py`): one
  read/write pair per area – `processes:read|write`,
  `modules:read|write|manage`, `dashboards:read|write`, `jobs:read|control`,
  `watched:read|write`, `account:read|write` – plus `admin`. Every tool
  declares the scope it requires.
- **Read-only mode:** `MCP_READ_ONLY=1` (boot) doesn't register write tools at
  all; an admin can also flip read-only **live** at Settings → API & MCP –
  then write tools stay listed but refuse. Destructive tools are confirm-gated
  regardless: the first call returns a dry-run preview, execution needs a
  re-call with `confirm=true`.
- **Egress consent:** with `MCP_REQUIRE_EGRESS_CONSENT=1` (default) each user
  must opt in (Settings → API & MCP) before any tool returns their data – a
  local-first acknowledgement that data leaves the box.
- **Limits** (single-instance, in-process): per-user token bucket
  `MCP_RATE_LIMIT_PER_MINUTE`/`MCP_RATE_LIMIT_BURST` (120/40, 0 disables) plus
  a separate, tighter **write bucket**
  `MCP_WRITE_RATE_LIMIT_PER_MINUTE`/`MCP_WRITE_RATE_LIMIT_BURST` (30/10);
  `MCP_MAX_CONCURRENCY_PER_USER`/`MCP_MAX_CONCURRENCY_GLOBAL` (4/32);
  `MCP_TOOL_TIMEOUT_SECONDS` (30). **Scaling out to >1 API replica needs a
  shared store** for these and stateful sessions – not built; call it out
  before HA.
- **Audit:** every tool call emits a structlog `mcp.tool_call` line (always,
  for SIEM) + a best-effort analytics mirror surfaced in the admin usage view.
  Prometheus at `GET /api/v1/system/mcp-metrics` (admin) –
  `mate_mcp_tool_calls_total`, `mate_mcp_tool_latency_seconds`,
  `mate_mcp_rate_limited_total`, `mate_mcp_active_calls`.

### Enable (prod)

In the VM `.env`:

```dotenv
MCP_ENABLED=1
API_BASE_URL=https://pm-mate.uni-muenster.de
MCP_OAUTH_CLIENT_ID=mate-mcp     # after the OAuth step below; empty = PAT-only
# optional: MCP_TOOLSETS=…  MCP_READ_ONLY=1  MCP_REQUIRE_EGRESS_CONSENT=0
```

then **recreate** the api container: `$DC up -d api` – `restart` keeps the old
env. The api image does not bake `.env`: an MCP knob only reaches the container
through the `environment:` block in `docker-compose.yml`. That block now
interpolates every `MCP_*` var (it originally didn't – "set it in `.env`"
silently did nothing); if you add a new knob, add it there too.

Caddy already routes `/mcp` and `/.well-known/oauth-protected-resource` to the
api – both live at the API root, outside `/api/v1`, so without those matchers
they fall through to Next.js and 404. After editing the Caddyfile run
`$DC up -d --force-recreate proxy`: the file is bind-mounted, so a plain
`up -d` sees no config change and won't recreate the container.

Streamable HTTP is plain POST + SSE-style streaming – the same passthrough as
`/events` (§6 step 5); no WebSocket upgrade involved.

> **OAuth needs `API_BASE_URL`.** The discovery document and the
> `WWW-Authenticate` challenge are built **only** from `API_BASE_URL` (never the
> request `Host` header, to avoid pointing a client's login at a forged host).
> Without it, OAuth discovery is simply not advertised (PATs still work).

### Auth 1: personal access tokens (PATs)

Each user mints `mate_pat_…` at **Settings → API & MCP**, choosing scopes at
mint time (empty grant = all read scopes), and sends it as
`Authorization: Bearer mate_pat_…`. A PAT acts as its owner on that user's
data only (same tenant isolation as the rest of the platform) and is
**role-less by construction** – the admin toolset is unreachable with a PAT.
Admin governance at Settings → API & MCP (admin only; backed by
`GET/PUT /api/v1/system/mcp` and `/api/v1/admin/api-tokens`): live
enable/disable, live read-only, the mint policy
(`all_users` / `admin_only` / `disabled`), and an org-wide token list + revoke.

### Auth 2: OAuth 2.1 (SSO through Keycloak)

The MCP server is an OAuth **resource server** advertising Keycloak via
RFC 9728 (`/.well-known/oauth-protected-resource`, and
`WWW-Authenticate: …resource_metadata=…` on 401). A compliant client hits
`/mcp`, discovers Keycloak from the 401, runs auth-code + PKCE (interactive
login bounces through the WWU IdP exactly like the web app), and retries with
the resulting JWT – validated by the existing JWKS path (audience
`flows-funds-api`). Dynamic client registration is deliberately **off**: one
pre-registered public client, no self-service registration.

One-time setup, on the VM (the realm JSON is import-once – kcadm against the
running Keycloak is the real path):

```bash
KC_SERVER=http://localhost:8080/auth \
PUBLIC_BASE_URL=https://pm-mate.uni-muenster.de \
  ./infra/keycloak/configure-mcp-client.sh
```

Idempotent. It creates/updates the public `mate-mcp` client (PKCE S256,
consent screen on, loopback + claude.ai redirect URIs), adds the
`flows-funds-api` audience mapper (without it every token fails `aud`
validation), creates one Keycloak client scope per MCP scope – the `*:read`
scopes attached as **defaults**, write/manage/control + `admin` as
**optional** – and maps the `admin` realm role onto the `admin` client scope
(the client runs `fullScopeAllowed=false`, so the role only rides along when
that scope is requested). Then set `MCP_OAUTH_CLIENT_ID=mate-mcp` in `.env`
and `$DC up -d api`; the client id + metadata URL surface in Settings →
API & MCP. Fresh installs (empty Keycloak DB) get the same client + scopes
from the realm JSON import.

Scope mapping at request time: the JWT `scope` claim is intersected with the
MCP taxonomy; a token carrying **none** of our scopes falls back to the read
scopes. `admin` needs the scope **and** the `admin` realm role on the same
token; PATs never qualify.

### Client setup

```bash
# Claude Code – PAT
claude mcp add --transport http mate https://pm-mate.uni-muenster.de/mcp \
  --header "Authorization: Bearer mate_pat_…"

# Claude Code – OAuth: omit the header; Claude Code discovers Keycloak via the
# 401 resource metadata and opens the browser login (/mcp to authenticate).
claude mcp add --transport http mate https://pm-mate.uni-muenster.de/mcp

# Codex CLI (a build with HTTP-server support)
codex mcp add mate --url https://pm-mate.uni-muenster.de/mcp --bearer-token mate_pat_…
# Codex without HTTP support – stdio bridge:
codex mcp add mate -- npx mcp-remote https://pm-mate.uni-muenster.de/mcp \
  --header "Authorization: Bearer mate_pat_…"
```

- **Claude Desktop / claude.ai (custom connector):** add `https://…/mcp` as a
  custom connector (OAuth; needs the public HTTPS origin), or paste the
  `mcpServers` JSON with a PAT – **Settings → API & MCP generates that snippet**
  with your URL filled in.
- **Smoke test:** `npx @modelcontextprotocol/inspector` → transport
  "Streamable HTTP", URL `https://…/mcp`, `Authorization: Bearer <PAT>` header
  → `tools/list` should answer.

### Security notes

- No anonymous access; the dev demo bypass is refused on `/mcp`, and running
  `DEMO_MODE=1` with MCP in production is logged loudly as a misconfiguration.
- PATs are role-less – platform administration over MCP is OAuth-only
  (scope + realm role), and minting is governed by the admin mint policy.
- Per-user egress consent gates all data egress (default on).
- Read-only twice over: boot env (write tools not registered) or live admin
  toggle (write tools refuse); destructive tools additionally require
  `confirm=true`.
- Browser-borne requests pass an **Origin allowlist** (CORS origins +
  `API_BASE_URL` + loopback) – the MCP-spec DNS-rebinding guard. Non-browser
  clients send no Origin and are unaffected.
- No raw event rows on this surface – restricted module contexts only.
- Audit trail: structlog `mcp.tool_call` (SIEM) + the admin usage view.

### Known limits

- Rate limits and MCP sessions are in-process – correct for exactly one api
  replica; add a shared store before scaling out.
- FastMCP's GET notification stream has no heartbeat, so the uni edge proxy
  may reap a long-idle GET stream. Tool calls travel over POST and are
  unaffected; verify after deploy and treat a reaped idle GET stream as
  cosmetic (clients reconnect).
- Boot-time knobs need `$DC up -d api`: `MCP_TOOLSETS`, `MCP_READ_ONLY` (as
  tool unregistration), rate/concurrency/timeout numbers. Live, no restart:
  enable/disable, read-only, mint policy (admin UI).

## Admin user deletion (removing the Keycloak account)

Admin → **Users** lists every account; opening one shows everything it owns and
a **Delete user** action. Delete purges the user's DB rows (FK cascade), on-disk
`data/users/{id}/`, the S3 subtree (S3 mode), and — when configured — the
Keycloak account.

The API has no Keycloak admin credentials by default, so the Keycloak step
**no-ops** (the delete report says `keycloak account not removed: not
configured`); all Mate-side data is still purged. To let a delete also remove
the Keycloak account, provision a confidential service-account client:

```bash
# On the VM, against the running Keycloak (serves under /auth in prod):
KC_SERVER=http://localhost:8080/auth ./infra/keycloak/configure-admin-client.sh
```

It creates `flows-funds-admin` (service account, `client_credentials`, no
browser flow, `fullScopeAllowed=false`), grants its service account **only**
`realm-management:manage-users`, and prints the four env vars. Add them to
`.env` (the secret is powerful — `.env` only, never git) and recreate the api
container (`restart` does **not** re-read `.env`):

```bash
KEYCLOAK_ADMIN_BASE_URL=http://keycloak:8080/auth
KEYCLOAK_REALM=flows-funds
KEYCLOAK_ADMIN_CLIENT_ID=flows-funds-admin
KEYCLOAK_ADMIN_CLIENT_SECRET=<printed secret>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
```

Notes: an admin cannot delete their own account (self-delete is refused, which
also guarantees the realm never loses its last admin here). For a **WWU-brokered**
user, deleting the Keycloak account is clean but not a permanent ban — a later
university login mints a **new** `sub` = a fresh empty account.

## Backup

`./data/` (SQLite metadata + Parquet logs + module results + cached runtimes)
is bind-mounted – back it up by copying the directory. Keycloak users live in
the `kc-data` Docker volume; include it if you need to preserve logins.

With the S3 backend active (§4c) the bucket already holds the authoritative
copy of all user data plus an hourly `_system/metadata.db` snapshot, so the
`data/` tarball matters mainly for module runtimes/venvs; a lost VM is
recovered with the `db_backup restore` pre-boot step in `docs/S3_OFFLOAD.md`.

```bash
tar czf mate-data-$(date +%F).tgz -C ~/mate data                                   # SQLite + Parquet + results
docker run --rm -v kc-data:/v -v "$PWD":/b alpine tar czf /b/kc-data.tgz -C /v .   # Keycloak users
```

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Every API call 401s after login | `KEYCLOAK_JWKS_URL` missing the `/auth` prefix, or `KEYCLOAK_ISSUER` ≠ the issuer Keycloak actually mints (check §6 step 3). |
| Redirect loop / "invalid redirect_uri" at login | Prod `redirectUris`/`webOrigins` not added to the realm (§2), or realm imported before the edit. |
| Safari (not Chrome) loops between the app and `/login` after a successful login | `SESSION_STORE_DIR` unset on the web service → the full Keycloak token rides in the session cookie, chunks past 4KB, and Safari drops/evicts a chunk so `auth()` can't decode it. Set `SESSION_STORE_DIR: /app/sessions` + the `./data/web-sessions` volume (§5) so the cookie holds only an opaque id. |
| Login page styled wrong / 404 on `/auth/...` assets | `KC_HTTP_RELATIVE_PATH` and the Caddy `/auth/*` route out of sync. |
| Live jobs/AI never update, page otherwise fine | WebSocket/SSE not passed through by the uni proxy (§6 step 5). |
| `tls` cert errors on proxy start | Mounted only `live/` instead of all of `/etc/letsencrypt` – the symlinks into `archive/` break. |
| MCP client gets 401 at `/mcp` | Token missing/revoked/expired. PAT: must be `mate_pat_…` (Settings → API & MCP). OAuth: JWT lacks the `flows-funds-api` audience – the `mate-mcp` client is missing its audience mapper (re-run `configure-mcp-client.sh`). |
| MCP endpoint 404s | `MCP_ENABLED` never reached the api container (edit `.env` then `$DC up -d api` – `restart` keeps old env), or the Caddy `/mcp` matchers are missing (`$DC up -d --force-recreate proxy`). |
| MCP 403 "Origin not allowed" | Browser-based MCP client on an origin outside CORS + `API_BASE_URL` (DNS-rebinding guard); non-browser clients are unaffected. |
| MCP write tool missing or refuses | Read-only mode (boot `MCP_READ_ONLY` unregisters write tools; the live admin toggle makes them refuse), toolset not in `MCP_TOOLSETS`, or the token lacks the write scope. |
