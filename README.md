# Mate

A locally-hosted, modular process mining platform. Two services (`api` + `web`)
plus a bundled Keycloak (`keycloak` + `keycloak-db`) for OIDC login,
embedded application data stores (SQLite + DuckDB + Parquet), no broker, no cloud.
Each user gets a fully isolated workspace – their event logs, jobs, AI keys,
and module config never bleed across accounts.

All documentation is indexed in [`docs/README.md`](./docs/README.md). For the
full design rationale, read [`docs/INSTRUCTIONS.md`](./docs/INSTRUCTIONS.md). For
the module authoring contract, read [`modules/README.md`](./modules/README.md).

## Quick start

```bash
git clone <repo-url> mate
cd mate
cp .env.example .env   # then rotate AUTH_SECRET + KEYCLOAK_CLIENT_SECRET
make up
```

Then open <http://localhost:3000>. On first login use
`admin@flows-funds.local` / `flowsfunds` – Keycloak forces a password reset.

To add additional users, sign in to the Keycloak admin console at
<http://localhost:8080/admin> with `admin` / `admin`, switch to the
`flows-funds` realm, and create users there.

## Running modes

**`make up` is not the dev mode, and `docker compose` is not a separate "prod" tool** – `make up` *is* `docker compose up -d --build`. What actually changes between dev and prod is which **compose overlay** stacks on top of the base [`docker-compose.yml`](./docker-compose.yml):

| Command | What you get |
| --- | --- |
| `make dev` | No Docker – runs the API + web dev servers directly with hot reload (needs `uv` + `pnpm` on the host). Fastest inner loop. |
| `make up` | Base `docker-compose.yml` only: **prod-style built images**, detached, no reload. The default quick-start – closest to prod, minus TLS/proxy. |
| `make up-dev` | Base **+ `compose.dev.yml`**: hot reload in Docker (`uvicorn --reload` + `next dev`, source-mounted). The in-container dev mode. |
| *(prod deploy)* | Base **+ `docker-compose.prod.yml`**: adds Caddy/TLS, collapses everything onto one same-origin, and stops publishing the app ports. Run manually – `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`; there is no `make` target. See [`docs/DEPLOY.md`](./docs/DEPLOY.md). |

In short: **develop** with `make up-dev` (or `make dev` without Docker), **preview the prod build** locally with `make up`, and **deploy** by adding the `docker-compose.prod.yml` overlay.

## Advanced setup

### Prerequisites

- **Docker Desktop** (macOS / Windows) or **Docker Engine + Compose v2** (Linux). No Python, Node, `uv`, or `pnpm` needed on the host.
- Free ports `3000` (web), `8000` (api), and `8080` (Keycloak).
- ~3 GB free disk for the built images. First boot pulls each module's Python deps (cv4cdd alone pulls TensorFlow, ~500 MB) plus Keycloak (~500 MB) and Postgres 16 alpine. Subsequent boots reuse the cached wheels under `data/uv-python/`.

### Step-by-step

1. **Clone the repo.**

   ```bash
   git clone <repo-url> mate
   cd mate
   ```

2. **Build and start the stack.** `make up` is a thin wrapper around `docker compose up -d --build` – it builds both images (`api`, `web`) and starts them detached. Either command works:

   ```bash
   make up
   # or
   docker compose up -d --build
   ```

   First boot takes several minutes because each module resolves its Python deps into its own `.venv/`. The api container's healthcheck has a 10-minute grace period to cover the worst case (cv4cdd pulling TensorFlow). Subsequent boots reuse the cached wheels and start in seconds.

3. **Open the app.** Visit <http://localhost:3000>. The first run lands on `/processes` with the empty state – drop a XES, XES.gz, or CSV file to start mining.

4. **Hot-reload mode (optional, for development).** Use the dev overlay to run `uvicorn --reload` + `next dev` with the source tree mounted:

   ```bash
   make up-dev
   ```

5. **Stop the stack.**

   ```bash
   make down
   ```

## Bundled modules

The modules under [`modules/`](./modules/) are discovered on startup and
mounted automatically:

| Module | What it does |
| --- | --- |
| `discovery` | Process discovery – DFG, Petri nets (Alpha / Inductive), Process Tree, Heuristics Net, BPMN |
| `performance` | Throughput, lead / cycle / sojourn time, P90, bottlenecks, performance DFG |
| `complexity` | EPA-based complexity measures – variant/sequence entropy, Lempel-Ziv, affinity, structure, Pentland's task/process complexity |
| `cv4cdd` | Computer-vision concept-drift detection (sudden, gradual, incremental, recurring) |
| `concept_drift_explainer` | LLM-backed explanations for drifts, grounded in user-uploaded enterprise documents |
| `agent_simulator` | Agent-based simulation that learns from a log and generates synthetic traces |
| `demo` | Minimal reference module – useful when authoring your own |

Enable / disable / configure each one under **Settings → Modules**.

## Mate AI

The right-side chat panel ("Mate AI") is wired to your own LLM provider –
keys, model, and system prompt live in `data/metadata.db` (under the
`ai.config` user-setting) and never leave the box. Configure under
**Settings → AI**. The assistant can reference the active process log,
modules, and recent jobs when answering.

## Privacy & usage

Anonymous usage capture is **on for every user by default**
(`USER_TRACKING_ONBOARDING=force`): the onboarding privacy step and the
**Settings → Privacy** tab are hidden and there's no opt-out. Set the var to
`on` (enabled by default, but opt-out) or `off` (disabled by default, opt-in)
to let users choose under **Settings → Privacy**. Either way events stay in the
local SQLite database; nothing ships off the host. See
[Configuration](#configuration).

## Common commands

| Command | What it does |
| --- | --- |
| `make up` | Start the prod-style stack (detached) |
| `make up-dev` | Start with hot reload – `uvicorn --reload` + `next dev`, source-mounted |
| `make down` | Stop the stack |
| `make build` | Rebuild both images |
| `make test` | Run the Python test suite (inside Docker) |
| `make typecheck` | Type-check the web app |
| `docker compose logs -f api` | Tail API logs |
| `docker compose logs -f web` | Tail web logs |
| `make clean` | Wipe `data/event_logs/`, `data/module_results/`, and `data/metadata.db` – irrevocable. Module folders are kept. |

## Data & persistence

- `./data/` is bind-mounted – SQLite metadata, Parquet event logs, module results, and the cached uv-managed Python runtimes all live here. Back up by copying the directory.
- `./modules/` is bind-mounted read/write – the bundled **default** modules live here, and any module folder you drop in is picked up on the next start. Each module's `.venv/` and `.dist/` (esbuilt frontend bundle) are auto-created and gitignored.
- User **uploads** (Settings → Modules → Import) land in `data/uploaded_modules/<id>/` instead, so they never overwrite or mix with the repo defaults. Module ownership is per-user (the `module_installs` table): every user is auto-seeded the default set on first login, and uninstall is reference-counted. A **Restore defaults** button re-adds any defaults a user removed.

## Configuration

The defaults in [`docker-compose.yml`](./docker-compose.yml) work out of the box for `localhost`. Override via `.env` (copy from `.env.example`) when running on a different host or before exposing the stack:

- `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) – the URL the **browser** uses to reach the API. This is inlined at build time, so changing it requires a rebuild (`make build` or `docker compose up -d --build`).
- `CORS_ORIGINS` on the api (default `["http://localhost:3000"]`) – extend this if the web origin changes.
- `USER_TRACKING_ONBOARDING` on the api (default `force`) – usage-tracking default & policy. `force` keeps tracking on for every user and hides both the onboarding privacy step and the **Settings → Privacy** tab (no opt-out); `on` enables tracking by default during onboarding (opt-out); `off` disables it by default (opt-in).
- `AUTH_SECRET` – encrypts the Auth.js session cookie. Rotate per environment (`openssl rand -base64 32`).
- `KEYCLOAK_CLIENT_SECRET` – confidential client secret for the `flows-funds-web` client. Rotate before any non-local deployment; the seeded dev value also lives in `infra/keycloak/realm-export/flows-funds-realm.json`.
- `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` – bootstrap credentials for the Keycloak admin console at `http://localhost:8080/admin`.

### Authentication

Login is mandatory. The web app gates everything behind a Keycloak OIDC flow
(Auth.js v5 on the Next.js side, PyJWT + JWKS validation on the FastAPI
side). Sessions are JWT-only – no extra DB tables on the Auth.js side, and
no Postgres for the app itself; only Keycloak uses Postgres.

### Admin data export

Visit **`/admin/export`** to download a consistent snapshot of the entire
metadata database (every user's accounts, usage analytics, process metadata,
and settings) as a single SQLite `.db` file. The download requires the Keycloak
realm role **`admin`** – assign it in the Keycloak admin console under *Realm
roles → admin → Users in role*. Users without the role see an explanatory
message instead of the button. The file contains all users' data, so treat the
download as sensitive.

User data isolation:

- Every event log / job / AI key is keyed by the Keycloak `sub` claim.
- On-disk parquet + module results live under `data/users/{user_id}/`.
- WebSocket envelopes are filtered by `user_id` server-side; a user only
  ever sees their own jobs and events.

## Tests

Run inside Docker so the host doesn't need the toolchain:

```bash
docker compose run --rm api uv run pytest apps/api/tests -v
docker compose run --rm web pnpm typecheck
```

## Layout

```
mate/
├── apps/
│   ├── api/         # FastAPI backend
│   └── web/         # Next.js 15 frontend
├── modules/         # Bundled + user-installed module packages
├── packages/
│   ├── module-sdk-py/   # Python SDK for module authors
│   ├── module-sdk-ts/   # TS SDK for module frontends
│   └── shared-types/    # Generated TS types from OpenAPI
├── data/            # Bind-mounted; SQLite + Parquet + cached runtimes
├── docs/            # Design spec, deploy runbook, MCP reference (see docs/README.md)
├── docker-compose.yml       # base stack
├── compose.dev.yml          # dev overlay – hot reload (make up-dev)
└── docker-compose.prod.yml  # prod overlay – Caddy/TLS (see docs/DEPLOY.md)
```

## Adding a module

```bash
mkdir modules/my_mod
$EDITOR modules/my_mod/manifest.yaml   # see modules/README.md §3
$EDITOR modules/my_mod/module.py        # subclass mate.sdk.Module
make up-dev                             # restart picks it up
```

Or upload a zip / clone a git URL via **Settings → Modules → Import**.