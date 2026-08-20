"""Subprocess worker entry point for `isolation: subprocess` modules (§5.4).

Spawned by the host (`SubprocessHost`) with::

    python -m mate.api.modules.subprocess_worker <socket_path> <module_folder>

Connects to the host's listening Unix socket, imports the module from
``<module_folder>/module.py``, walks the resulting `Module` instance for
decorated handlers and sends a `ready` message describing them. The host
then sends `call` requests for each invocation; the worker executes the
handler in-process using a `ProxyContext` that translates every `ctx.*`
attribute access into an RPC back to the host over the same socket.

Wire protocol: line-delimited JSON, one message per line. Each message is
``{"id": int, "method": str, "params": dict}`` for requests, or
``{"id": int, "result": ...}`` / ``{"id": int, "error": {...}}`` for
responses. Both sides initiate requests; ids are local to the initiator.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import traceback
from pathlib import Path
from typing import Any

# RPC messages are newline-framed JSON read via StreamReader.readline(), whose
# asyncio default buffer limit is 64 KiB. Handler return values, ctx.cache.set
# payloads and duckdb_fetch rows routinely exceed that (a single >64 KiB line
# raises LimitOverrunError and tears the connection down, killing the worker), so
# raise it well beyond. DataFrames still cross via a Parquet file, not the
# socket, so this only bounds JSON metadata.
RPC_STREAM_LIMIT = 256 * 1024 * 1024  # 256 MiB

# Must match `subprocess_host.CANCEL_RPC_MSG`. The host raises an RPC error
# carrying this string for every ctx call made by a soft-cancelled job; the
# worker turns it into `Cancelled` (below) so the handler unwinds cleanly.
_CANCEL_RPC_MSG = "__ff_job_cancelled__"


def _resolve_cancelled() -> type[BaseException]:
    """The `Cancelled` type to raise for a soft cancel.

    Prefer the SDK's `mate.sdk.Cancelled` (new workers). Fall back to a local
    BaseException subclass so an *older* worker SDK - which predates `Cancelled`
    - still unwinds under a broad `except Exception` (BaseException isn't caught
    by it). Version-skew-proof either way.
    """
    try:
        from mate.sdk import Cancelled as _Cancelled  # type: ignore[attr-defined]

        return _Cancelled
    except Exception:  # pragma: no cover - exercised only against an old SDK

        class Cancelled(BaseException):
            pass

        return Cancelled


Cancelled = _resolve_cancelled()


def _import_module(folder: Path):
    """Import ``<folder>/module.py`` as a package so relative imports work."""
    py_path = folder / "module.py"
    if not py_path.exists():
        raise FileNotFoundError(f"Missing module.py at {py_path}")
    ns = f"_ff_subprocess_mod_{folder.name}"
    spec = importlib.util.spec_from_file_location(
        ns, py_path, submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {py_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ns] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_module_class(mod):
    from mate.sdk import Module

    for value in mod.__dict__.values():
        if (
            inspect.isclass(value)
            and issubclass(value, Module)
            and value is not Module
            and value.__module__ == mod.__name__
        ):
            return value
    raise RuntimeError(f"No Module subclass in {mod.__name__}")


def _collect_handlers(instance) -> list[dict[str, Any]]:
    """Walk the instance for route/event/job-decorated methods."""
    from mate.sdk.decorators import get_event_sub, get_job_spec, get_route_spec

    out: list[dict[str, Any]] = []
    for attr_name in dir(instance):
        unbound = getattr(type(instance), attr_name, None)
        route_spec = get_route_spec(unbound)
        event_sub = get_event_sub(unbound)
        job_spec = get_job_spec(unbound)
        if not (route_spec or event_sub or job_spec):
            continue
        entry: dict[str, Any] = {"attr": attr_name}
        if route_spec is not None:
            entry["route"] = {
                "method": route_spec.method,
                "path": route_spec.path,
                "name": route_spec.name,
            }
        if event_sub is not None:
            entry["on_event"] = {"topic": event_sub.topic}
        if job_spec is not None:
            # Serialize the static JobSpec fields so the host can rebuild it.
            # Callable title/subtitle can't cross the socket - leave them null
            # so the host falls back to a static label (loader's
            # `_resolve_dynamic`).
            entry["job"] = {
                "progress": job_spec.progress,
                "priority": job_spec.priority,
                "cancellable": job_spec.cancellable,
                "result_url": job_spec.result_url,
                "title": job_spec.title if isinstance(job_spec.title, str) else None,
                "subtitle": job_spec.subtitle if isinstance(job_spec.subtitle, str) else None,
            }
        out.append(entry)
    return out


class _ProxyContext:
    """ctx.* surface forwarded to the host via JSON-RPC.

    DataFrame views (`pandas`/`polars`/`pm4py`) are materialised by the host to
    a Parquet file under the shared workdir and loaded here, so heavy
    process-mining jobs work the same as in_process.
    """

    def __init__(self, conn: WireConnection, token: str, ctx_meta: dict[str, Any]):
        self._conn = conn
        self._token = token
        self.log_id: str = ctx_meta.get("log_id", "")
        self.module_id: str = ctx_meta.get("module_id", "")
        self.workdir = Path(ctx_meta.get("workdir", "/tmp"))
        self.event_log = _EventLogProxy(conn, token)
        self.bus = _BusProxy(conn, token)
        self.registry = _RegistryProxy(conn, token, ctx_meta.get("capabilities"))
        self.cache = _CacheProxy(conn, token)
        self.config = _ConfigProxy(ctx_meta.get("config", {}))
        self.progress = _ProgressProxy(conn, token)
        self.logger = _LoggerProxy(conn, token)
        self.cancellation = _CancellationProxy(conn, token)

    def is_cancelled(self) -> bool:
        return self.cancellation.is_cancelled()

    async def check_cancelled(self) -> None:
        await self.cancellation.check_cancelled()


class _EventLogProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def duckdb_fetch(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        result = await self._conn.send_request(
            "ctx.event_log.duckdb_fetch",
            {"ctx_token": self._token, "sql": sql, "params": list(params or [])},
        )
        return [tuple(row) for row in result]

    async def _materialize(self) -> str:
        """Ask the host to write the (filter-applied) log to a Parquet under the
        shared workdir; returns the path. Host and worker share the filesystem."""
        return await self._conn.send_request(
            "ctx.event_log.materialize", {"ctx_token": self._token}
        )

    async def pandas(self):
        import pandas as pd

        path = await self._materialize()
        return await asyncio.to_thread(pd.read_parquet, path)

    async def polars(self):
        import polars as pl

        path = await self._materialize()
        return await asyncio.to_thread(pl.read_parquet, path)

    async def pm4py(self):
        import pandas as pd

        path = await self._materialize()

        def _convert():
            import pm4py.utils as pmu

            df = pd.read_parquet(path).rename(
                columns={
                    "case_id": "case:concept:name",
                    "activity": "concept:name",
                    "timestamp": "time:timestamp",
                }
            )
            return pmu.format_dataframe(df)

        return await asyncio.to_thread(_convert)


class _BusProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def emit(self, topic: str, payload: Any) -> None:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif not isinstance(payload, dict):
            payload = {"value": payload}
        await self._conn.send_request(
            "ctx.bus.emit", {"ctx_token": self._token, "topic": topic, "payload": payload}
        )


class _RegistryProxy:
    def __init__(self, conn: WireConnection, token: str, capabilities: list[str] | None = None):
        self._conn = conn
        self._token = token
        # The host snapshots the available capability names into ctx_meta at
        # call time so `has()` (sync per the SDK Protocol) answers locally
        # without a round-trip.
        self._caps = frozenset(capabilities or ())

    def has(self, cap: str) -> bool:
        return cap in self._caps

    async def call(self, capability: str, **kwargs: Any) -> Any:
        return await self._conn.send_request(
            "ctx.registry.call",
            {"ctx_token": self._token, "capability": capability, "kwargs": kwargs},
        )


class _CacheProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def get(self, key: str) -> Any:
        return await self._conn.send_request(
            "ctx.cache.get", {"ctx_token": self._token, "key": key}
        )

    async def set(self, key: str, value: Any) -> None:
        await self._conn.send_request(
            "ctx.cache.set", {"ctx_token": self._token, "key": key, "value": value}
        )

    async def exists(self, key: str) -> bool:
        return bool(
            await self._conn.send_request(
                "ctx.cache.exists", {"ctx_token": self._token, "key": key}
            )
        )

    async def delete(self, key: str) -> None:
        await self._conn.send_request("ctx.cache.delete", {"ctx_token": self._token, "key": key})


class _ConfigProxy:
    def __init__(self, value: dict[str, Any]):
        self._value = dict(value)

    @property
    def value(self) -> dict[str, Any]:
        return dict(self._value)

    def get(self, key: str, default: Any = None) -> Any:
        return self._value.get(key, default)


class _ProgressProxy:
    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    async def update(
        self,
        current: float,
        message: str | None = None,
        *,
        total: float | None = None,
        stage: str | None = None,
    ) -> None:
        await self._conn.send_request(
            "ctx.progress.update",
            {
                "ctx_token": self._token,
                "current": current,
                "total": total,
                "stage": stage,
                "message": message,
            },
        )


class _CancellationProxy:
    """ctx.cancellation surface for a subprocess handler (new-SDK workers).

    `check_cancelled()` makes a dedicated `ctx.cancel.check` RPC; the host's
    cancel guard turns it into the cancel sentinel when the job is flagged, which
    `WireConnection.run` reconstructs as `Cancelled` - so the call raises. When
    not cancelled the RPC just returns False. `is_cancelled()` is best-effort
    sync: it can't round-trip, so it reports False and authors should prefer the
    async `check_cancelled()` (or simply report progress, which also polls).
    """

    def __init__(self, conn: WireConnection, token: str):
        self._conn = conn
        self._token = token

    def is_cancelled(self) -> bool:
        return False

    async def check_cancelled(self) -> None:
        await self._conn.send_request("ctx.cancel.check", {"ctx_token": self._token})


class _LoggerProxy:
    """Minimal structlog-compatible logger that forwards to the host."""

    def __init__(self, conn: WireConnection, token: str, bound: dict[str, Any] | None = None):
        self._conn = conn
        self._token = token
        self._bound = dict(bound or {})

    def bind(self, **kwargs: Any) -> _LoggerProxy:
        merged = {**self._bound, **kwargs}
        return _LoggerProxy(self._conn, self._token, merged)

    def _log(self, level: str, event: str, **kwargs: Any) -> None:
        payload = {**self._bound, **kwargs, "event": event}
        asyncio.create_task(
            self._conn.send_request(
                "ctx.logger.log",
                {"ctx_token": self._token, "level": level, "payload": payload},
            )
        )

    def info(self, event: str, **kw: Any) -> None:
        self._log("info", event, **kw)

    def debug(self, event: str, **kw: Any) -> None:
        self._log("debug", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._log("warning", event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._log("error", event, **kw)

    def exception(self, event: str, **kw: Any) -> None:
        self._log("error", event, exc_info=True, **kw)


class WireConnection:
    """Bidirectional line-delimited JSON-RPC over a stream pair."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._dispatcher: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def register(self, method: str, fn) -> None:
        self._dispatcher[method] = fn

    def fail_all_pending(self, exc: BaseException) -> None:
        """Reject every in-flight outbound request with `exc`.

        Called when the peer process dies (e.g. the host SIGKILLs the worker
        to cancel a job): without this, the futures returned by `send_request`
        never resolve and their awaiting tasks hang forever. Idempotent.
        """
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def send_request(self, method: str, params: dict[str, Any]) -> Any:
        async with self._lock:
            rid = self._next_id
            self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"id": rid, "method": method, "params": params})
        return await fut

    async def _write(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg).encode("utf-8") + b"\n"
        self._writer.write(line)
        await self._writer.drain()

    async def run(self) -> None:
        while not self._reader.at_eof():
            line = await self._reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if "method" in msg:
                asyncio.create_task(self._dispatch(msg))
            else:
                rid = msg.get("id")
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    if "error" in msg:
                        message = msg["error"].get("message", "remote error")
                        # The host signals a soft cancel via an RPC error carrying
                        # the cancel sentinel - turn it into `Cancelled` (a
                        # BaseException) so the awaiting handler unwinds even under
                        # a broad `except Exception`. Other errors stay RuntimeError.
                        if _CANCEL_RPC_MSG in message:
                            fut.set_exception(Cancelled())
                        else:
                            fut.set_exception(RuntimeError(message))
                    else:
                        fut.set_result(msg.get("result"))

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        method = msg["method"]
        params = msg.get("params", {})
        fn = self._dispatcher.get(method)
        if fn is None:
            await self._write({"id": rid, "error": {"message": f"unknown method {method!r}"}})
            return
        try:
            result = fn(params)
            if inspect.isawaitable(result):
                result = await result
            await self._write({"id": rid, "result": result})
        except Cancelled:
            # A handler unwound on a soft cancel (it raised Cancelled, or a ctx
            # RPC reconstructed one). Report it back carrying the cancel sentinel
            # so the host re-raises Cancelled too - the job records `cancelled`,
            # not `failed`. (Cancelled is a BaseException, so the generic
            # `except Exception` below would otherwise miss it and the host's
            # `call` future would hang.)
            await self._write({"id": rid, "error": {"message": _CANCEL_RPC_MSG}})
        except Exception as exc:
            await self._write(
                {
                    "id": rid,
                    "error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                }
            )


async def _amain(socket_path: str, module_folder: str) -> int:
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=RPC_STREAM_LIMIT)
    conn = WireConnection(reader, writer)

    mod = _import_module(Path(module_folder))
    module_class = _find_module_class(mod)
    instance = module_class()
    handlers_meta = _collect_handlers(instance)

    async def handle_call(params: dict[str, Any]) -> Any:
        attr = params["handler"]
        ctx_token = params["ctx_token"]
        ctx_meta = params.get("ctx", {})
        args = params.get("args", []) or []
        kwargs = params.get("kwargs", {}) or {}
        bound = getattr(instance, attr)
        ctx = _ProxyContext(conn, ctx_token, ctx_meta)
        if inspect.iscoroutinefunction(bound):
            return await bound(ctx, *args, **kwargs)
        return await asyncio.to_thread(bound, ctx, *args, **kwargs)

    conn.register("call", handle_call)
    conn.register("shutdown", lambda _params: True)

    await conn._write({"id": None, "method": "ready", "params": {"handlers": handlers_meta}})
    await conn.run()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return 0


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: subprocess_worker.py <socket_path> <module_folder>", file=sys.stderr)
        sys.exit(2)
    socket_path = sys.argv[1]
    module_folder = sys.argv[2]
    # Make the module folder importable for relative `from .x import y`.
    sys.path.insert(0, module_folder)
    raise SystemExit(asyncio.run(_amain(socket_path, module_folder)))


if __name__ == "__main__":  # pragma: no cover - executed by spawned subprocess
    main()
