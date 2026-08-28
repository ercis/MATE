# Plan: Docker-only install/start README

## Context

The current [README.md](README.md) opens with a no-docker `make install` / `make dev` quickstart and treats Docker as a secondary path. You want install/start to be **Docker-only** — one obvious way to run the app, no `uv` / `pnpm` / Python / Node prerequisites on the host.

The Docker setup already exists and works:
- [docker-compose.yml](docker-compose.yml) — production-style two-service stack (`api` on :8000, `web` on :3000), bind-mounts `./data` and `./modules`, has a healthcheck on the api.
- [compose.dev.yml](compose.dev.yml) — overlay for hot-reload (uvicorn `--reload` + `next dev`, source-mounted), runs `alembic upgrade head` on api startup.
- [Makefile](Makefile) — `make up`, `make up-dev`, `make down`, `make build` already wrap the docker commands.

So the change is **purely documentation**: rewrite [README.md](README.md) so the install/start path is Docker, drop the host-toolchain quickstart, and keep the rest of the orienting content (what the project is, layout, what's not in v1, adding a module).

## Approach

Rewrite [README.md](README.md) in place. Single file changed.

### New structure

1. **Title + one-line description** — keep as-is.
2. **Prerequisites** — Docker Desktop (Mac/Windows) or Docker Engine + Compose v2 (Linux). Nothing else. Note: ~2 GB free disk for the images, ports `3000` and `8000` free.
3. **Install & start** — Docker only:
   ```bash
   git clone <repo> && cd mate
   make up           # builds images, starts api + web in the background
   ```
   Open <http://localhost:3000>. First run lands on `/processes` empty state. Drop a XES / XES.gz / CSV to start mining.
   - Plain-docker fallback for users without `make`: `docker compose up -d --build`.
4. **Common commands** — short table:
   | Command | What it does |
   | --- | --- |
   | `make up` | Start prod-style stack (detached) |
   | `make up-dev` | Start with hot-reload (foreground, source-mounted) |
   | `make down` | Stop the stack |
   | `make build` | Rebuild both images |
   | `docker compose logs -f api` | Tail API logs |
   | `docker compose logs -f web` | Tail web logs |
5. **Data & persistence** — `./data/` is bind-mounted (SQLite + Parquet); `./modules/` is bind-mounted (installed modules land here). Back up by copying `./data/`. `make clean` wipes the local data dir.
6. **Configuration** — call out the env knobs that already exist in [docker-compose.yml](docker-compose.yml):
   - `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) — must be the URL the **browser** uses to reach the API. Override when running on a non-localhost host.
   - `CORS_ORIGINS` on the api (default `["http://localhost:3000"]`) — extend if you change the web origin.
7. **Layout** — keep as-is (it's still accurate).
8. **What's not in v1** — keep as-is.
9. **Adding a module** — keep the `mkdir modules/my_mod ...` flow but adapt the last line: `make up-dev` (instead of `make dev`) so the dev overlay picks it up; or upload via Settings → Modules → Import.
10. **Tests** — keep but route through Docker so the host doesn't need `uv` / `pnpm`:
    ```bash
    docker compose run --rm api uv run pytest apps/api/tests -v
    docker compose run --rm web pnpm typecheck
    ```
    (Note: `make test` / `make typecheck` still work if the user has the host toolchain — mention as optional.)

### What gets dropped

- The "no docker" quickstart (`make install` + `make dev`). It still works via the Makefile, but it's no longer in the README — that's the explicit user ask.
- The `make install` line, since users following the README will never need it.

## Critical files

- [README.md](README.md) — rewrite (only file touched).

## Reused content

- [docker-compose.yml](docker-compose.yml) — port + env values come from here verbatim, do not invent new ones.
- [Makefile](Makefile) — every command in the README maps to an existing Make target.

## Verification

1. Read the rewritten [README.md](README.md) end-to-end — every command should be copy-pasteable.
2. From a clean checkout (`make down && docker compose down -v` to reset), run the exact sequence in the README:
   - `make up` → wait for healthcheck → `curl http://localhost:8000/health` returns OK → open <http://localhost:3000> and confirm the empty `/processes` state.
   - `make down` cleanly stops both containers.
3. Run `make up-dev`, edit a file under [apps/api/src/](apps/api/src/) and confirm uvicorn `--reload` picks it up; edit a file under [apps/web/](apps/web/) and confirm `next dev` HMRs.
4. Run the test commands from the README inside Docker (`docker compose run --rm api uv run pytest apps/api/tests -v`) and confirm they pass without any host toolchain installed.
