"""ModuleContext - the typed shape every entry point receives (§5.5).

The SDK only declares the *shape*. Concrete implementations live in
`mate.api.modules.*` and are injected by the loader. Module code
should depend on these Protocols, not the implementations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog


@runtime_checkable
class EventBusProtocol(Protocol):
    async def emit(self, topic: str, payload: Any) -> None: ...
    async def subscribe(self, *patterns: str) -> AsyncIterator[Any]: ...  # type: ignore[empty-body]


@runtime_checkable
class ModuleRegistryProtocol(Protocol):
    def has(self, capability_or_module_id: str) -> bool: ...
    async def call(self, capability: str, **kwargs: Any) -> Any: ...
    def installed_modules(self) -> list[str]: ...


@runtime_checkable
class ResultCacheProtocol(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...


@runtime_checkable
class ProgressReporterProtocol(Protocol):
    async def update(
        self,
        current: float,
        message: str | None = None,
        *,
        total: float | None = None,
        stage: str | None = None,
    ) -> None: ...


@runtime_checkable
class CancellationProtocol(Protocol):
    """Cooperative-cancel surface for a running handler (§7.9).

    A job is *soft-killed*: the platform flags it and the handler is expected to
    notice at its next poll point and wind down cleanly. Any handler that reports
    progress already polls for free (the progress reporter checks this internally),
    but long stretches without a progress tick should call ``check_cancelled()``::

        async with ctx.event_log as log:
            for chunk in chunks:
                await ctx.check_cancelled()   # raises Cancelled if asked to stop
                ...

    Both methods are cheap and side-effect-free when the job is not cancelled.
    """

    def is_cancelled(self) -> bool: ...
    async def check_cancelled(self) -> None: ...


class _NoopCancellation:
    """Default cancellation surface: never cancelled.

    Used when a context is built outside a cancellable job (e.g. a plain route
    handler) or by an older platform that doesn't wire one in - so module code
    can always call ``ctx.is_cancelled()`` / ``ctx.check_cancelled()`` safely.
    """

    def is_cancelled(self) -> bool:
        return False

    async def check_cancelled(self) -> None:
        return None


@runtime_checkable
class RunInProcessProtocol(Protocol):
    """Offload a pure function to the platform's ProcessPoolExecutor (§8.3).

    Use for CPU-bound work where the GIL is the bottleneck (e.g. pm4py
    inductive mining on a million-event log). `fn` must be importable by
    qualified name; args + return must be picklable. For I/O-bound or
    GIL-releasing work (numpy / pandas / duckdb), `asyncio.to_thread` is
    cheaper.
    """

    async def __call__(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class ModuleConfigProtocol(Protocol):
    @property
    def value(self) -> dict[str, Any]: ...
    def get(self, key: str, default: Any = None) -> Any: ...


@runtime_checkable
class EventLogAccessProtocol(Protocol):
    """Lazy view of the log under a given `log_id`. The async-context-manager
    pattern from §5.5 is what module authors actually use::

        async with ctx.event_log as log:
            df = await log.pandas()
            rows = await log.duckdb_fetch("SELECT activity, count(*) FROM events GROUP BY 1")
    """

    async def __aenter__(self) -> EventLogAccessProtocol: ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def pandas(self) -> Any: ...
    async def polars(self) -> Any: ...
    async def pm4py(self) -> Any: ...
    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]: ...

    # Hand the current view to a `ctx.run_in_process` worker without pickling a
    # multi-million-row DataFrame: `materialize_parquet()` returns a Parquet path
    # the worker reads with a plain `pandas.read_parquet` (§8.3). `events_path` /
    # `active_filter` expose the raw path + applied filter for advanced uses.
    async def materialize_parquet(self) -> tuple[str, bool]: ...
    @property
    def events_path(self) -> Path: ...
    @property
    def active_filter(self) -> list[dict[str, Any]] | None: ...


@runtime_checkable
class OpenEventLogProtocol(Protocol):
    """Open a *second* case-centric log owned by the same user (§5.5).

    `ctx.event_log` is bound to the one log the invocation is scoped to. A few
    modules - log comparison, benchmarking - need to read another log too. This
    factory returns an `EventLogAccessProtocol` for any other case-centric log
    **owned by `ctx.user_id`**, applying that log's committed Events-tab filter
    just like the primary view. It is the only sanctioned cross-log accessor:
    it enforces the tenant-isolation invariant, raising if the log is missing,
    belongs to another user, or is object-centric. Use it as a context manager::

        async with await ctx.open_event_log(other_id) as other:
            df = await other.pandas()

    `filters` overrides which rows that view serves, in the same
    ``{field, op, value}`` shape as `EventLogAccessProtocol.active_filter`:

    * ``None`` (default) - the log's committed Events-tab filter.
    * a list - **replaces** the committed filter for this view only (same
      precedence a dashboard's ephemeral filter has); ``[]`` reads the raw log.

    That is what lets one module read two *differently filtered* views - the
    same log twice under two filters is a legal A/B, e.g. two cohorts of one
    log::

        async with await ctx.open_event_log(ctx.log_id, filters=north) as a:
            ...
    """

    async def __call__(
        self, log_id: str, filters: list[dict[str, Any]] | None = None
    ) -> EventLogAccessProtocol: ...


@runtime_checkable
class ObjectCentricLogAccessProtocol(Protocol):
    """Lazy view of an object-centric (OCEL) log. The object-centric
    counterpart to `EventLogAccessProtocol` - bound on `ctx.object_log` only for
    logs whose `log_model` is ``object_centric``. Exposes the four OCEL tables
    plus a reconstructed pm4py OCEL object::

        async with ctx.object_log as ol:
            ocel = await ol.ocel()
            ocdfg = pm4py.discover_ocdfg(ocel)
    """

    async def __aenter__(self) -> ObjectCentricLogAccessProtocol: ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def events_pandas(self) -> Any: ...
    async def objects_pandas(self) -> Any: ...
    async def relations_pandas(self) -> Any: ...
    async def o2o_pandas(self) -> Any: ...
    async def ocel(self) -> Any: ...
    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]: ...


@dataclass
class ModuleContext:
    """The dependency-injected context every handler receives.

    Built by the loader per (log_id, module_id, invocation). For event
    handlers and route handlers without `log_id` (e.g. global routes), the
    `log_id` may be empty - module authors should treat it as optional.
    """

    log_id: str
    module_id: str
    # Owner of this invocation (Keycloak `sub`). Exposed so modules that reach
    # across to another module's per-user result cache can scope it correctly.
    user_id: str
    event_log: EventLogAccessProtocol
    bus: EventBusProtocol
    registry: ModuleRegistryProtocol
    cache: ResultCacheProtocol
    config: ModuleConfigProtocol
    progress: ProgressReporterProtocol
    logger: structlog.BoundLogger
    workdir: Path
    run_in_process: RunInProcessProtocol
    # Open another case-centric log owned by the same user (ownership-checked).
    # The sanctioned way for a module to read a *second* log - e.g. comparison.
    open_event_log: OpenEventLogProtocol
    # Bound only for object-centric (OCEL) logs; None for case-centric logs.
    # A module only ever runs against the model it declares (availability
    # gating), so it reads exactly one of `event_log` / `object_log`.
    object_log: ObjectCentricLogAccessProtocol | None = None
    # Cooperative-cancel surface for the running job (§7.9). Defaulted to a
    # never-cancelled no-op so contexts built outside a cancellable job - or by
    # an older platform that doesn't wire one in - keep working unchanged.
    cancellation: CancellationProtocol = field(default_factory=_NoopCancellation)

    def is_cancelled(self) -> bool:
        """Whether the running job has been asked to stop (non-blocking)."""
        return self.cancellation.is_cancelled()

    async def check_cancelled(self) -> None:
        """Raise :class:`Cancelled` if the running job has been asked to stop.

        Call this at poll points in long-running work that doesn't report
        progress; the platform turns the raised ``Cancelled`` into a clean
        ``job.cancelled`` outcome.
        """
        await self.cancellation.check_cancelled()
