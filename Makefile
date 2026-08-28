.DEFAULT_GOAL := help
.PHONY: help install preflight dev dev-api dev-web up up-dev down build test typecheck fmt clean codegen deploy

# Tab indentation is required for Make recipes.

# Dev API port. Override with `make dev PORT=8001`.
PORT ?= 8000

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nMate – common tasks\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Resolve all deps (uv + pnpm)
	uv sync --extra dev
	pnpm install

preflight: ## Free $(PORT) + reap stale API / orphaned offload workers from a prior run
	@pids=$$(lsof -nP -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pids" ]; then echo "preflight: freeing :$(PORT) (pids $$pids)"; kill -9 $$pids 2>/dev/null || true; fi
	@pkill -9 -f "uvicorn mate.api.main" 2>/dev/null || true
	@pkill -9 -f "$(CURDIR)/.venv/bin/python -c from multiprocessing" 2>/dev/null || true
	@true

dev: preflight ## Run the API and the web dev server together (no docker)
	uv run alembic -c apps/api/alembic.ini upgrade head
	@trap 'kill 0' INT; \
	(uv run uvicorn mate.api.main:app --reload --reload-dir apps/api/src --reload-dir packages/module-sdk-py/src --app-dir apps/api/src --host 127.0.0.1 --port $(PORT) --timeout-graceful-shutdown 3) & \
	(cd apps/web && pnpm dev) & \
	wait

dev-api: preflight ## Run only the API (with --reload)
	uv run alembic -c apps/api/alembic.ini upgrade head
	uv run uvicorn mate.api.main:app --reload --reload-dir apps/api/src --reload-dir packages/module-sdk-py/src --app-dir apps/api/src --host 127.0.0.1 --port $(PORT) --timeout-graceful-shutdown 3

dev-web: ## Run only the web dev server
	cd apps/web && pnpm dev

up: ## docker compose up (production-style images)
	docker compose up -d --build

up-dev: ## docker compose with the dev overlay (uvicorn --reload + next dev)
	docker compose -f docker-compose.yml -f compose.dev.yml up --build

down: ## Stop the compose stack
	docker compose down

build: ## Build both Docker images
	docker compose build

test: ## Run the Python test suite
	uv run --extra dev pytest apps/api/tests -v

sdk-jvm: ## Build the JVM module SDK + example + conformance jars (needs JDK 17+)
	cd packages/module-sdk-jvm && ./gradlew build shadowJar :examples:performance-metrics:copyExampleJar

typecheck: ## Type-check the web app
	cd apps/web && pnpm typecheck

fmt: ## Format Python with ruff
	uv run ruff check --fix .
	uv run ruff format .

codegen: ## Regenerate TS types from the running API's /openapi.json
	cd apps/web && pnpm codegen

deploy: ## Push + redeploy to the uni VM (run on the FB4-DEV-VPN)
	./scripts/deploy.sh

clean: ## Wipe local data + Keycloak Postgres volume – irrevocable
	rm -rf data/event_logs/* data/module_results/* data/users data/metadata.db data/metadata.db-wal data/metadata.db-shm data/.multi_user_migrated
	-docker volume rm flows-and-funds_kc-data 2>/dev/null || true
	@echo "data/ wiped and Keycloak volume removed. Module folders under modules/ are kept."
