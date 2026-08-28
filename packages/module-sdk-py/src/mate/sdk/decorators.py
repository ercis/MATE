"""`@route`, `@on_event`, `@job` - metadata decorators (§5.6).

These do **not** run any platform machinery. They tag the wrapped function
with a small marker the loader picks up at mount time. The auto-wrap in §5.5
happens inside the loader.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

# Attribute names used to attach metadata to handler functions. Authors don't
# touch these - the loader reads them.
_ATTR_ROUTE = "__ff_route__"
_ATTR_ON_EVENT = "__ff_on_event__"
_ATTR_JOB = "__ff_job__"

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True)
class RouteSpec:
    method: HttpMethod
    path: str
    name: str | None = None
    response_model: Any | None = None


@dataclass(frozen=True)
class EventSubscription:
    topic: str


@dataclass(frozen=True)
class JobSpec:
    progress: bool = False
    title: str | Callable[..., str] | None = None
    subtitle: str | Callable[..., str] | None = None
    priority: int = 0
    cancellable: bool = True
    result_url: str | None = None


def _route_factory(method: HttpMethod) -> Callable[..., Callable[[Callable], Callable]]:
    def deco_factory(
        path: str,
        *,
        name: str | None = None,
        response_model: Any | None = None,
    ) -> Callable[[Callable], Callable]:
        spec = RouteSpec(method=method, path=path, name=name, response_model=response_model)

        def deco(fn: Callable) -> Callable:
            existing = getattr(fn, _ATTR_ROUTE, None)
            if existing is not None:
                raise RuntimeError(f"@route already applied to {fn.__qualname__}: {existing!r}")
            setattr(fn, _ATTR_ROUTE, spec)
            return fn

        return deco

    return deco_factory


@dataclass
class _RouteNamespace:
    """Namespaced API: `@route.get("/x")`, `@route.post("/y")`, ..."""

    get: Callable[..., Callable[[Callable], Callable]] = field(
        default_factory=lambda: _route_factory("GET")
    )
    post: Callable[..., Callable[[Callable], Callable]] = field(
        default_factory=lambda: _route_factory("POST")
    )
    put: Callable[..., Callable[[Callable], Callable]] = field(
        default_factory=lambda: _route_factory("PUT")
    )
    patch: Callable[..., Callable[[Callable], Callable]] = field(
        default_factory=lambda: _route_factory("PATCH")
    )
    delete: Callable[..., Callable[[Callable], Callable]] = field(
        default_factory=lambda: _route_factory("DELETE")
    )


route = _RouteNamespace()


def on_event(topic: str) -> Callable[[Callable], Callable]:
    """Subscribe to a bus topic. The loader registers the handler against the
    platform `EventBus` and applies the §5.5 sync→async auto-wrap.
    """
    sub = EventSubscription(topic=topic)

    def deco(fn: Callable) -> Callable:
        existing = getattr(fn, _ATTR_ON_EVENT, None)
        if existing is not None:
            raise RuntimeError(f"@on_event already applied to {fn.__qualname__}")
        setattr(fn, _ATTR_ON_EVENT, sub)
        return fn

    return deco


def job(
    *,
    progress: bool = False,
    title: str | Callable[..., str] | None = None,
    subtitle: str | Callable[..., str] | None = None,
    priority: int = 0,
    cancellable: bool = True,
    result_url: str | None = None,
) -> Callable[[Callable], Callable]:
    """Mark a handler as a long-running job (§5.6).

    When stacked under `@route.*` the route returns ``{"job_id": "..."}``
    immediately and the actual work runs on the platform job queue.
    """
    spec = JobSpec(
        progress=progress,
        title=title,
        subtitle=subtitle,
        priority=priority,
        cancellable=cancellable,
        result_url=result_url,
    )

    def deco(fn: Callable) -> Callable:
        setattr(fn, _ATTR_JOB, spec)
        return fn

    return deco


def get_route_spec(fn: object) -> RouteSpec | None:
    return getattr(fn, _ATTR_ROUTE, None)


def get_event_sub(fn: object) -> EventSubscription | None:
    return getattr(fn, _ATTR_ON_EVENT, None)


def get_job_spec(fn: object) -> JobSpec | None:
    return getattr(fn, _ATTR_JOB, None)
