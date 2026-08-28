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

    # Java launcher used by the JVM module runtime (modules/PROTOCOL.md). Bare
    # name resolves on PATH; the Docker image bakes a Temurin JRE onto PATH, dev
    # hosts need their own JRE only when actually running Java modules.
    java_bin: str = Field(default="java")

    # Crash-respawn policy for worker-bridged modules (modules/PROTOCOL.md §8).
    # A worker that exits unexpectedly is respawned with exponential backoff
    # (0.5s · 2^attempt, capped); after this many consecutive failed starts the
    # bridge enters a terminal failed state (calls error immediately) until the
    # module is fixed/reloaded. The counter resets once a worker stays up 60s,
    # so a rare OOM kill self-heals while a boot-crash loop can't burn CPU.
    subprocess_respawn_max_attempts: int = Field(default=5, ge=1, le=50)
    subprocess_respawn_backoff_cap_seconds: float = Field(default=30.0, ge=0.5, le=300)

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

    # --- Storage backend (see docs/S3_OFFLOAD.md) --------------------------
    # Where event logs + module outputs are durably stored. ``local`` (default)
    # keeps everything under ``data_dir``; ``s3`` makes an S3-compatible bucket
    # (AWS S3, Ceph RGW, MinIO, …) the authoritative store and local disk a
    # bounded working cache - the point is that the VM volume no longer has to
    # hold every byte ever imported. Configured HERE ONLY (.env / compose env);
    # there is no admin UI and no DB row - changing the backend is an operator
    # action: edit the env, restart, run the migration CLI
    # (``python -m mate.api.storage.migration``) to move existing data.
    storage_mode: Literal["local", "s3"] = Field(
        default="local",
        description="STORAGE_MODE - 'local' (single copy on disk) or 's3' (bucket authoritative).",
    )
    # Endpoint URL incl. scheme (e.g. https://rgw.example.org or
    # http://minio:9000). Leave EMPTY for AWS S3 proper - boto3 derives the
    # regional endpoint. Required for every non-AWS provider.
    storage_s3_endpoint: str = Field(default="", description="STORAGE_S3_ENDPOINT")
    storage_s3_bucket: str = Field(default="", description="STORAGE_S3_BUCKET")
    # Region: required by AWS (picks the endpoint + SigV4 scope); most
    # S3-compatibles ignore it (any value works; Cloudflare R2 wants 'auto').
    storage_s3_region: str = Field(default="", description="STORAGE_S3_REGION")
    storage_s3_access_key: str = Field(default="", description="STORAGE_S3_ACCESS_KEY")
    storage_s3_secret_key: str = Field(default="", description="STORAGE_S3_SECRET_KEY")
    # Path-style addressing (host/bucket/key). Required by most self-hosted
    # providers (Ceph RGW, MinIO without wildcard DNS); AWS prefers
    # virtual-host style - set false there.
    storage_s3_path_style: bool = Field(default=True, description="STORAGE_S3_PATH_STYLE")
    storage_s3_use_ssl: bool = Field(default=True, description="STORAGE_S3_USE_SSL")
    # TLS certificate verification: '' = verify against system CAs (default),
    # 'false' = disable verification, or a path to a CA bundle - for on-prem
    # endpoints with an internal CA.
    storage_s3_verify: str = Field(default="", description="STORAGE_S3_VERIFY")
    # Key prefix inside the bucket (share one bucket between deployments).
    storage_s3_prefix: str = Field(default="", description="STORAGE_S3_PREFIX")
    # Total-bucket ceiling in bytes enforced on new imports (507 when reached).
    # 0 = no quota.
    storage_s3_quota_bytes: int = Field(
        default=0, ge=0, description="STORAGE_S3_QUOTA_BYTES - 0 disables the quota."
    )

    # --- S3 local-cache eviction (see docs/S3_OFFLOAD.md) ------------------
    # In S3 mode the bucket is authoritative and local disk is a reclaimable
    # cache. When the cache exceeds ``local_cache_max_bytes`` a background reaper
    # deletes the least-recently-used log/output dirs locally (they survive on S3
    # and re-hydrate on the next read). ``0`` disables eviction - local disk keeps
    # every synced copy, the pre-eviction behaviour. No effect in local mode,
    # where the local copy is the only copy.
    local_cache_max_bytes: int = Field(
        default=0,
        ge=0,
        description="LOCAL_CACHE_MAX_BYTES - S3-mode local cache high-water mark (0=disabled).",
    )
    # Safety: the reaper logs candidates but deletes nothing while true. Soak in
    # dry-run, confirm the candidate set, then flip to false to enable deletes.
    cache_evict_dry_run: bool = Field(
        default=True,
        description="CACHE_EVICT_DRY_RUN - log eviction candidates without deleting.",
    )
    # Never evict a dir accessed within this window - guards against deleting a
    # tree whose S3 upload is still in flight just after a write.
    cache_evict_min_age_seconds: int = Field(default=600, ge=0)
    # How often the reaper wakes.
    cache_evict_interval_seconds: int = Field(default=60, ge=5, le=3600)

    # Jobs-table retention (see S3_OFFLOAD.md). Terminal jobs older than this many
    # days are pruned by the daily retention sweeper - bounds metadata.db growth on
    # a busy deployment. `0` keeps every job forever (the pre-retention behaviour).
    job_retention_days: int = Field(
        default=0,
        ge=0,
        description="JOB_RETENTION_DAYS - prune terminal jobs older than N days (0=keep forever).",
    )

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

    # Keycloak admin REST client (server-to-server). Used ONLY by the admin
    # "delete user" flow to remove the account from Keycloak after the local
    # data purge - distinct from the validation config above. Needs a
    # confidential client whose service account holds the realm-management role
    # ``manage-users``. When base_url/client_secret are unset (dev, demo, tests,
    # or a prod misconfig) the admin client is a logged no-op: the Mate-side
    # purge still succeeds and the caller is told KC was not touched, so a
    # misconfiguration fails SAFE instead of silently half-deleting. ``keycloak_realm``
    # is the realm segment of the admin REST path (the issuer/JWKS URLs above
    # embed it, but the admin API needs it explicitly).
    keycloak_admin_base_url: str | None = Field(
        default=None,
        description="KEYCLOAK_ADMIN_BASE_URL - internal Keycloak base (e.g. http://keycloak:8080/auth).",
    )
    keycloak_realm: str = Field(
        default="flows-funds",
        description="KEYCLOAK_REALM - realm segment for the admin REST path.",
    )
    keycloak_admin_client_id: str | None = Field(
        default=None,
        description="KEYCLOAK_ADMIN_CLIENT_ID - confidential client with a manage-users service account.",
    )
    keycloak_admin_client_secret: str | None = Field(
        default=None,
        description="KEYCLOAK_ADMIN_CLIENT_SECRET - secret for the admin client.",
    )

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

    # MCP server (Model Context Protocol). When enabled the API mounts a
    # read-only MCP endpoint at ``/mcp`` (streamable HTTP) so external MCP
    # clients (Claude Desktop, claude.ai, customer agents) can consume a user's
    # process-mining outputs. Authenticated by a per-user API token (PAT) minted
    # at Settings → API tokens. Default OFF - turning it on opens a network
    # surface that reaches outside the local box, so it must be a deliberate
    # opt-in (consistent with the local-first default).
    mcp_enabled: bool = Field(
        default=False,
        description="MCP_ENABLED - mount the read-only MCP server at /mcp.",
    )
    # Public base URL of this API (e.g. https://mate.example.com), used only to
    # advertise the MCP endpoint URL in the UI. Empty falls back to deriving it
    # from the incoming request.
    api_base_url: str | None = Field(
        default=None,
        description="API_BASE_URL - public base URL, advertised for the MCP endpoint.",
    )
    # MCP rate limiting + concurrency (single-instance, in-process). The per-user
    # token bucket admits ``per_minute`` requests with a ``burst`` ceiling;
    # ``0`` disables it. Concurrency caps bound simultaneous tool executions.
    mcp_rate_limit_per_minute: int = Field(default=120, ge=0, le=100_000)
    mcp_rate_limit_burst: int = Field(default=40, ge=0, le=100_000)
    mcp_max_concurrency_per_user: int = Field(default=4, ge=1, le=64)
    mcp_max_concurrency_global: int = Field(default=32, ge=1, le=512)
    # Wall-clock cap on a single MCP tool call (a tool runs inline in the request,
    # not via the job runtime, so it needs its own timeout).
    mcp_tool_timeout_seconds: float = Field(default=30.0, ge=1, le=600)
    # Pre-registered Keycloak public OAuth client id for MCP clients (DCR not
    # assumed). Surfaced via /api/v1/api-tokens/mcp-info; empty = PAT-only.
    mcp_oauth_client_id: str | None = Field(
        default=None,
        description="MCP_OAUTH_CLIENT_ID - pre-registered Keycloak client for MCP OAuth.",
    )
    # Require explicit per-user opt-in before MCP serves a user's data to an
    # external client (local-first egress gate). Off = minting a token suffices.
    mcp_require_egress_consent: bool = Field(
        default=True,
        description="MCP_REQUIRE_EGRESS_CONSENT - gate MCP data egress on per-user consent.",
    )
    # Server-wide write lock. At boot: write tools are not registered at all.
    # Live: an admin can also flip mcp.read_only (SystemSetting) without a
    # restart - registered write tools then refuse with a read_only error.
    mcp_read_only: bool = Field(
        default=False,
        description="MCP_READ_ONLY - expose only read tools over MCP.",
    )
    # Which toolsets to register, csv (see mcp/registry.py). Empty = every
    # toolset except `admin`; the literal "all" also enables `admin`.
    mcp_toolsets: str = Field(
        default="",
        description="MCP_TOOLSETS - csv of enabled toolsets; empty = all except admin.",
    )
    # Tighter bucket charged only by mutating tools (on top of the per-request
    # rate limit). 0 disables it.
    mcp_write_rate_limit_per_minute: int = Field(default=30, ge=0, le=100_000)
    mcp_write_rate_limit_burst: int = Field(default=10, ge=0, le=100_000)

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

    @property
    def staging_dir(self) -> Path:
        """Root for uploads staged by the import wizard but not yet confirmed.

        The wizard uploads once, probes the staged file for its columns, and
        only creates the log once the user confirms the mapping - so an
        abandoned wizard leaves bytes here. Never user-visible; swept by TTL
        (``_staging_sweep`` in ``main.py``).
        """
        return self.data_dir / "staging"

    def event_logs_dir_for(self, user_id: str) -> Path:
        return self.users_dir / user_id / "event_logs"

    def staging_dir_for(self, user_id: str) -> Path:
        return self.staging_dir / user_id

    def module_results_dir_for(self, user_id: str) -> Path:
        return self.users_dir / user_id / "module_results"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.uploaded_modules_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def ensure_user_dirs(self, user_id: str) -> None:
        self.event_logs_dir_for(user_id).mkdir(parents=True, exist_ok=True)
        self.module_results_dir_for(user_id).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
