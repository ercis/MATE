# Modules with sidecar services (extra containers)

How to ship a module that needs a **whole server next to the platform** — a graph
database, a search engine, a solver, anything that is its own long-running process and
speaks TCP. The reference implementation of everything below is
[`modules/actor_performance`](./actor_performance) (Neo4j sidecar); copy from it.

The short version: **the module never starts or manages containers.** The service is
declared as an *optional compose profile*, the operator starts it, and the module just
connects to a configured address — with a health probe and a good setup screen for when
it isn't there. No Docker-in-Docker, no docker.sock in the api container, ever.

---

## 1. Do you actually need a sidecar?

Walk this list top to bottom; stop at the first match.

| Your dependency is… | Use instead |
|---|---|
| A Python library | `dependencies.python.packages` in the manifest — no container |
| A Python lib with conflicting pins / other Python version | `isolation: subprocess` (own venv + interpreter) |
| JVM code (a jar) | `runtime: {kind: jvm}` — see [`modules/PROTOCOL.md`](./PROTOCOL.md), reference `modules/performance_java` |
| A separate **server process** with its own storage/protocol (Neo4j, OpenSearch, a CPLEX server, …) | **This pattern** |

Why never Docker-in-Docker / mounting docker.sock: both are root-equivalent on the
host, need runtime image pulls on the prod VM, and add container lifecycle management
(GC, resource caps, orphans) to the platform for no gain — a module's service need is
*static and known*, which is exactly what compose is for. It also breaks host-mode
`make dev`, where there is no Docker at all unless the operator starts one container by
hand.

## 2. Architecture — how the connection works

```
host-mode dev                          docker / prod
─────────────                          ─────────────
api (make dev, host process)          api container ──────────┐ internal compose network
   │  tcp → localhost:<port>             │  tcp → <service>:<port>  (docker DNS name)
   ▼                                     ▼
sidecar container                      sidecar container (profile-gated)
(docker run …, port published          (NO published ports in prod;
 on 127.0.0.1 only)                     127.0.0.1-only in the base file)
```

- **One service instance is shared by all users.** Tenant isolation is the module's
  job (see §4) — the platform does not multiplex sidecars per user.
- **Address resolution is a three-layer chain**, resolved per run/request:
  1. explicit module config (`config_schema` fields, per-user, set on the module
     settings page or via `PUT /api/v1/modules/{id}/config`),
  2. platform environment `MATE_<SERVICE>_*` (wired into the api service in
     docker-compose; subprocess workers inherit the api's environment),
  3. hardcoded local-dev default (`localhost:<port>`).
  Reference: `modules/actor_performance/pipeline/connection.py::resolve_settings`.
- **Data handoff**, two options:
  - *Pure protocol* (preferred): everything over the service's TCP protocol.
  - *Shared directory*: when the service insists on reading files from its own
    filesystem (Neo4j's import dir), bind-mount **one host directory into both
    containers** — e.g. `./data/<service>-handoff` is `/app/data/<service>-handoff`
    for the api and `/whatever/import` for the service. The module writes, the
    service reads. Files written by the api must be world-readable (default umask is).
- The api service must **never `depends_on` a profile-gated service** — with the
  profile off, compose would refuse to start the stack.

## 3. Integration steps (the actual checklist)

### 3.1 Module side

1. Build the module per [`modules/README.md`](./README.md). The service's **client
   library** goes into `dependencies.python.packages` like any other dep.
2. Declare connection fields in `config_schema` with **empty-string defaults** and
   describe the env fallback in the field description:

   ```yaml
   config_schema:
     properties:
       service_uri:
         type: string
         title: MyService URI
         description: 'Leave empty for automatic: MATE_MYSERVICE_URI (docker: tcp://myservice:9999), else tcp://localhost:9999.'
         default: ""
       password:
         type: string
         default: ""
         ui: { widget: password }
   ```

3. Write a `resolve_settings(config, env)` helper (copy from actor_performance):
   config value → `MATE_MYSERVICE_*` env → localhost default.
4. Add a **`GET /health` route**: short-timeout connect + auth check, returns
   `{status: ok|auth-failed|unreachable, uri, hint}`. Cheap and side-effect-free.
5. In the run job, **pre-flight before any work**: ping the service and raise a
   `RuntimeError` with an *actionable* message ("start the sidecar: COMPOSE_PROFILES=…,
   see …") — that string becomes the failure toast.
6. Panel gets a **setup card** for `status != ok`: what's missing, the `.env` line for
   the docker stack, and the copy-paste `docker run` one-liner for host-mode dev.
   Poll the health route (`refetchInterval` while unhealthy) so the card flips to
   ready by itself once the container is up.
7. Tests: keep pipeline logic importable without the client lib where possible; gate
   the integration test on `MYSERVICE_TEST_URI` env (skip otherwise) so CI without the
   service stays green.

### 3.2 Infra side (compose — the only platform files you touch)

1. `docker-compose.yml` — add the service under a **profile**:

   ```yaml
   myservice:
     image: vendor/myservice:1.2.3        # always pin the tag
     container_name: mate-myservice
     profiles: ["myservice"]              # ← not started unless enabled
     environment:
       MYSERVICE_PASSWORD: ${MYSERVICE_PASSWORD:-mate-myservice-dev}
     ports:
       - "127.0.0.1:9999:9999"            # loopback ONLY (host-mode dev needs it)
     volumes:
       - myservice-data:/data
       # only if file handoff is needed:
       - ./data/myservice-handoff:/import
     healthcheck:
       test: ["CMD-SHELL", "<cheap readiness probe>"]
       interval: 15s
       timeout: 10s
       retries: 10
       start_period: 120s
     restart: unless-stopped
   ```

   …and the named volume under `volumes:`.
2. Same file, api service `environment:` — the env-fallback wiring:

   ```yaml
   MATE_MYSERVICE_URI: ${MATE_MYSERVICE_URI:-tcp://myservice:9999}
   MATE_MYSERVICE_PASSWORD: ${MYSERVICE_PASSWORD:-mate-myservice-dev}
   ```

3. `docker-compose.prod.yml` — never expose the port publicly:

   ```yaml
   myservice:
     profiles: ["myservice"]
     ports: !reset []
   ```

4. `DEPLOY.md` — short section: enable via `.env`, secrets, RAM sizing. Module
   `README.md` — the host-mode `docker run` one-liner.

### 3.3 Enabling it

```dotenv
# .env — profiles are a comma-separated list, so sidecars compose:
COMPOSE_PROFILES=graph,myservice
MYSERVICE_PASSWORD=<rotate me on the VM>
```

`docker compose up -d` then creates **only the new container**; the running ones are
untouched. Host-mode dev instead runs the module README's `docker run` one-liner once
and uses `docker start/stop mate-myservice` afterwards.

## 4. Multi-tenancy rules (non-negotiable)

One shared service instance, many users. The platform invariant — nothing crosses
accounts — is enforced by *how the module uses the sidecar*:

- **No user data at rest in the sidecar.** Treat it as transient compute scratch:
  wipe/namespace at run start, compute, extract, **wipe again in a `finally`** (also on
  cancel/failure). Results live only in the per-user `ctx.cache`.
- **Serialize runs** with a module-global `asyncio.Lock` when the service has one
  shared namespace (Neo4j Community = one database). Show "Waiting for engine…"
  progress while queued. If the service supports real namespaces (one index/database
  per user), you may parallelize — but then you own cleanup on log/user deletion.
- **Never expose the service port publicly** (loopback in base compose, `!reset []` in
  prod). The service's own auth is a second fence, not the primary one.
- Module routes must never proxy raw service queries from the frontend — the panel
  talks to *your* routes, your routes talk to the service.

## 5. Do I have to restart anything? (matrix)

| You changed… | Restart needed |
|---|---|
| Started/stopped the **sidecar container** | **No.** The api never depends on it; the module's health route notices and the panel flips by itself. Running jobs against a stopping service fail with the pre-flight/actionable error. |
| Added a **new module folder** (host dev, `ENV=dev`) | Usually **no** — the modules/ watchdog discovers new manifests and builds the venv live. If it doesn't show up, restart `make dev-api` (seconds). |
| Added a **new module folder** (docker/prod) | **Yes, api container once** (`$DC restart api`) — discovery + venv build happen at startup. First boot with new deps takes as long as the deps. |
| Edited module **code** (dev) | No — hot reload. |
| Edited module **manifest deps** (dev) | No — watchdog re-runs `uv sync` for that module. |
| Edited **module config** (URI, password) | No — resolved per run/request. |
| Added the **compose service** to docker-compose.yml | No platform restart — `docker compose up -d` creates just the new container. |
| Changed `MATE_<SERVICE>_*` env defaults in compose | Api container recreate (`up -d` does it) — env is read at container start. |

## 6. When this pattern stops scaling

Each sidecar costs RAM on the shared VM and one more `.env` knob. If a **third**
service-backed module shows up, formalize instead of copy-pasting: a manifest
`services:` declaration + an admin "service connections" registry (admin UI, one
resolution path, per-service health on the module card). Until then, this document is
the contract.
