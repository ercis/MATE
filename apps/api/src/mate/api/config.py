"""Runtime configuration. Reads from env / .env via pydantic-settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("data"), description="Bind-mounted persistent data root.")
    modules_dir: Path = Field(
        default=Path("modules"),
        description="Filesystem-discovered module folder (§5.3).",
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///data/metadata.db",
        description="aiosqlite URL - async SQLAlchemy engine.",
    )

    log_level: Literal["debug", "info", "warning", "error"] = "info"

    env: Literal["dev", "prod"] = Field(
        default="prod",
        description="Set to 'dev' to enable watchdog hot-reload of modules/ (§5.3 #7).",
    )

    # Behaviour-tracking policy & onboarding default. Read by the frontend via
    # GET /api/v1/usage/config (surfaced as `onboarding_mode`):
    #   force - tracking is ON for every user; the onboarding privacy step and
    #           the Settings → Privacy tab are hidden, and users cannot opt out.
    #   on    - tracking is enabled by default during onboarding (opt-out flow).
    #   off   - tracking is disabled by default during onboarding (opt-in flow).
    user_tracking_onboarding: Literal["force", "on", "off"] = Field(
        default="force",
        description="USER_TRACKING_ONBOARDING - tracking default/policy (force|on|off).",
    )

    # Job queue. ``worker_concurrency`` is the boot default for the asyncio
    # worker pool; an admin can change it live at Settings → General → Jobs
    # (persisted in ``system_settings`` and re-applied over this default at boot).
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    progress_persist_every: int = Field(
        default=1000,
        description="Persist job progress to SQLite every N processed events.",
    )
    # Soft-cancel grace for subprocess-isolated module jobs (§7.9). On cancel the
    # worker is asked to wind down cooperatively (RPC-error on its next ctx call);
    # if it hasn't stopped after this many seconds the host hard-kills+respawns it
    # (the existing SIGKILL fallback). Keep small so a stuck native job dies
    # promptly, but non-zero so a cooperative worker gets a chance to unwind.
    subprocess_cancel_grace_seconds: float = Field(default=3.0, ge=0, le=60)

    # Wall-clock backstop for a single job execution. A handler still running
    # after this many seconds is force-stopped (cooperative token + the subprocess
    # two-phase kill) and recorded as a timeout failure, so a wedged job can never
    # hold its worker slot forever - the slot leak behind the cross-user starvation
    # we saw (one user's stuck precompute jobs draining the shared pool). A safety
    # net, not a tuning knob: keep it well above a legitimate large-log import.
    # `0` disables it. Tune down only if every job on the deployment is known-fast.
    job_execution_timeout_seconds: int = Field(default=1800, ge=0, le=86400)

    # CPU offload (§8.3). `ctx.run_in_process` ships the heaviest module compute
    # (pm4py/networkx mining) to a `ProcessPoolExecutor` so it runs on multiple
    # cores instead of contending on the GIL. Sized independently of
    # `worker_concurrency` (which only gates asyncio job slots) - default to the
    # box's cores, capped so a many-core host doesn't fork an absurd pool.
    module_process_pool_size: int = Field(
        default_factory=lambda: min((os.cpu_count() or 2), 8),
        ge=1,
        le=64,
        description="MODULE_PROCESS_POOL_SIZE - worker processes for ctx.run_in_process.",
    )

    # Per-user fairness cap on concurrent CPU offloads (§8.3). The offload pool is
    # shared across tenants; without a per-user bound one user's burst of heavy
    # mining can hold every `module_process_pool_size` slot and starve others
    # (the cross-user starvation the job timeout was meant to bound but couldn't,
    # because an offloaded process ignored cooperative cancel). `0` resolves to
    # `module_process_pool_size` (no bite on a single-tenant box); set a smaller
    # value on a multi-tenant deployment so no one user holds more than this many
    # offload processes at once.
    max_offloads_per_user: int = Field(
        default=0,
        ge=0,
        le=64,
        description="MAX_OFFLOADS_PER_USER - per-user concurrent ctx.run_in_process cap (0=pool size).",
    )

    # DuckDB tuning (§9). `duckdb_threads=0` leaves DuckDB's default (all cores -
    # right for single-query ingest); set a positive cap to stop a many-card
    # dashboard's concurrent widget queries from oversubscribing the box.
    # `duckdb_memory_limit` is a DuckDB size string (e.g. "4GB"); empty leaves
    # DuckDB's own default. Object-cache + unordered scans are always applied.
    duckdb_threads: int = Field(default=0, ge=0, le=256)
    duckdb_memory_limit: str = Field(default="", description="DUCKDB_MEMORY_LIMIT e.g. '4GB'.")

    # Shared, mtime-guarded Parquet read cache (§5.5). Many dashboard widgets on
    # one log each call `event_log.pandas()`, re-reading the same Parquet; this
    # caps how many materialised frames we keep so N widgets collapse to one read
    # within the freshness window. 0 disables; bounded by a per-frame size guard.
    event_log_cache_entries: int = Field(default=3, ge=0, le=64)

    # Live system-resource sampler (Admin → System). A background task samples
    # CPU/RAM every ``metrics_sample_interval_seconds`` into a ring buffer of the
    # last ``metrics_history_samples`` points (default 90 x 2s = 3 min of history).
    # Cheap enough to run always-on; not persisted.
    metrics_sample_interval_seconds: float = Field(default=2.0, ge=0.5, le=30)
    metrics_history_samples: int = Field(default=90, ge=10, le=600)

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Allowed origins for the Next.js dev server.",
    )

    # Keycloak / OIDC. Set in docker-compose; required for any authenticated
    # request to succeed. The `iss` claim on issued tokens must equal
    # ``keycloak_issuer`` (browser-facing URL). The JWKS endpoint can live on
    # a different host (e.g. the docker DNS name) - ``keycloak_jwks_url`` lets
    # the API fetch keys without going via the browser-facing hostname.
    keycloak_issuer: str = Field(
        default="http://localhost:8080/realms/flows-funds",
        description="OIDC issuer URL - must match the `iss` claim on tokens.",
    )
    keycloak_jwks_url: str = Field(
        default="http://localhost:8080/realms/flows-funds/protocol/openid-connect/certs",
        description="Where the API fetches signing keys (back-channel).",
    )
    keycloak_audience: str = Field(
        default="flows-funds-api",
        description="Expected `aud` claim on access tokens.",
    )
    keycloak_jwks_ttl_seconds: int = Field(default=3600, ge=60)

    # Demo/dev login bypass. When true, a request whose bearer token equals the
    # demo sentinel (see auth/dependencies.DEMO_ACCESS_TOKEN) is resolved to a
    # fixed, non-admin demo user WITHOUT any Keycloak/JWKS validation. Pairs with
    # DEMO_MODE on the web app, which auto-signs that demo session in.
    # NEVER enable in a multi-tenant or public production deployment - it lets
    # anyone in as the demo user with no credentials.
    demo_mode: bool = Field(
        default=False,
        description="DEMO_MODE - accept the demo sentinel token as a fixed demo user (no auth).",
    )

    # When DEMO_MODE is on, grant the fixed demo user the realm "admin" role so it
    # can reach admin-gated routes (/admin/*). No effect unless demo_mode is set.
    # Pairs with DEMO_ADMIN on the web app, which flags the demo session isAdmin.
    demo_admin: bool = Field(
        default=False,
        description="DEMO_ADMIN - give the demo user the admin role (only when DEMO_MODE is on).",
    )

    # Secret used to derive the Fernet key that encrypts the S3 secret-access-key
    # stored in the metadata DB (Admin → Storage). Must be set and STABLE in
    # production - rotating it makes the stored S3 secret undecryptable and the
    # admin has to re-enter it. Falls back to ``database_url`` when unset so dev
    # works out of the box (acceptable: the DB is local-only there).
    storage_encryption_key: str | None = Field(
        default=None,
        description="STORAGE_ENCRYPTION_KEY - encrypts the stored S3 secret key.",
    )

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    @property
    def uploaded_modules_dir(self) -> Path:
        """Persistent root for user-uploaded modules.

        Kept separate from ``modules_dir`` (the git-tracked repo defaults) so an
        upload can never overwrite or sit next to a default's code. Discovery
        scans both roots; a default and an upload sharing an id is a hard error.
        """
        return self.data_dir / "uploaded_modules"

    def event_logs_dir_for(self, user_id: str) -> Path:
        return self.users_dir / user_id / "event_logs"

    def module_results_dir_for(self, user_id: str) -> Path:
        return self.users_dir / user_id / "module_results"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.uploaded_modules_dir.mkdir(parents=True, exist_ok=True)

    def ensure_user_dirs(self, user_id: str) -> None:
        self.event_logs_dir_for(user_id).mkdir(parents=True, exist_ok=True)
        self.module_results_dir_for(user_id).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
