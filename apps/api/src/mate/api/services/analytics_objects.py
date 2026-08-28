"""Materialise OCEL objects + relations from behaviour-tracking events.

Implements the object side of the Abb & Rehse reference data model for
process-related UI logs (Information Systems 124 (2024) 102386). Every ingested
event is related to the objects it touches:

  - ``ui_element``   - the atomic target the action was executed on
  - ``ui_group``     - its enclosing semantic containers (innermost linked on
    the event, the full nesting chain kept as static ``part_of`` O2O rows)
  - ``application``  - ``app:mate-web`` (browser) / ``app:mate-api`` (backend)
  - ``system``       - browser/OS class the application runs on
  - ``user``         - pseudonymous actor (the rotating anon seed, never the
    account id, so exports stay pseudonymised)
  - ``task``         - auto-derived unit of work (route area)
  - platform resources (``job``/``module``/``log``/``dashboard``) for
    server-side events - the genuinely object-centric part of the backend log.

Object ids are stable digests so the same on-screen element upserts into the
same registry row across sessions; dynamic path segments (uuids, numbers) are
normalised out of the identity so ``/processes/<id>`` pages share elements.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import AnalyticsEventObject, AnalyticsObject, AnalyticsObjectRelation

APP_WEB_ID = "app:mate-web"
APP_API_ID = "app:mate-api"

# E2O qualifiers (paper: action target + context; OCEL: qualified relations).
Q_TARGET = "target"
Q_CONTEXT = "context"
Q_APPLICATION = "application"
Q_SYSTEM = "system"
Q_PERFORMED_BY = "performed_by"
Q_TASK = "task"
Q_RESOURCE = "resource"

# O2O qualifier for the composition hierarchy element ⊂ group ⊂ app ⊂ system.
REL_PART_OF = "part_of"

# Resource id keys recognised in server-event properties / path params.
_RESOURCE_KEYS: dict[str, str] = {
    "job_id": "job",
    "module_id": "module",
    "log_id": "log",
    "logId": "log",
    "dashboard_id": "dashboard",
    "dashboardId": "dashboard",
}

_DYNAMIC_SEGMENT = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|\d+)$"
)


@dataclass(frozen=True)
class ObjectRef:
    """One object an event relates to, with its E2O qualifier."""

    object_id: str
    object_type: str
    qualifier: str
    attrs: dict[str, Any] | None = field(default=None, compare=False)


def _digest(*parts: str) -> str:
    return hashlib.blake2s("\x1f".join(parts).encode("utf-8", "replace"), digest_size=8).hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())[:48] or "unknown"


def normalize_path(path: str | None) -> str:
    """Replace dynamic path segments (uuids, ids) with ``*`` for stable identity."""
    if not path:
        return "/"
    segments = [("*" if _DYNAMIC_SEGMENT.match(s) else s) for s in path.split("/") if s]
    return "/" + "/".join(segments)


def _task_area(norm_path: str, *, server: bool) -> str:
    segments = [s for s in norm_path.split("/") if s and s != "*"]
    if server:
        # API paths look like /api/v1/<area>/... - the area segment is the task.
        segments = segments[2:] if segments[:2] == ["api", "v1"] else segments
    return segments[0] if segments else "general"


def derive_client_objects(
    *,
    path: str | None,
    properties: dict[str, Any] | None,
    anon_user_id: str,
    ua_class: str | None,
) -> tuple[list[ObjectRef], set[tuple[str, str, str]]]:
    """Objects + static relations for one browser-emitted event.

    Returns ``(refs, relations)`` where ``relations`` are ``(src, tgt,
    qualifier)`` tuples. Every event links application/system/user/task; events
    with a DOM target additionally link the ui_element and its innermost
    ui_group, with the full group chain recorded as O2O ``part_of`` rows.
    """
    props = properties or {}
    norm = normalize_path(path)
    refs: list[ObjectRef] = []
    relations: set[tuple[str, str, str]] = set()

    system_id = f"system:{_slug(ua_class or 'unknown')}"
    refs.append(ObjectRef(system_id, "system", Q_SYSTEM, {"ua_class": ua_class or "unknown"}))
    refs.append(ObjectRef(APP_WEB_ID, "application", Q_APPLICATION, {"name": "mate-web"}))
    relations.add((APP_WEB_ID, system_id, REL_PART_OF))

    refs.append(ObjectRef(f"user:{anon_user_id}", "user", Q_PERFORMED_BY))

    area = _task_area(norm, server=False)
    refs.append(ObjectRef(f"task:{area}", "task", Q_TASK, {"area": area}))

    # UI hierarchy - present on DOM-targeted events (click/input/key/...).
    target = props.get("target")
    selector = props.get("selector")
    groups_raw = props.get("ui_groups")
    groups: list[dict[str, Any]] = (
        [g for g in groups_raw if isinstance(g, dict)] if (isinstance(groups_raw, list)) else []
    )

    group_ids: list[str] = []
    for i, g in enumerate(groups):
        kind = str(g.get("kind") or "group")
        ident = str(g.get("id") or g.get("label") or i)
        gid = f"group:{_digest(norm, kind, ident)}"
        group_ids.append(gid)
        attrs = {"kind": kind, "page": norm}
        if g.get("id"):
            attrs["dom_id"] = str(g["id"])[:128]
        if g.get("label"):
            attrs["label"] = str(g["label"])[:128]
        # Innermost group carries the event's `context` edge; outer groups are
        # reachable via the part_of chain.
        refs.append(ObjectRef(gid, "ui_group", Q_CONTEXT if i == 0 else "", attrs))

    if isinstance(target, dict) and isinstance(selector, str) and selector:
        elem_id = f"elem:{_digest(norm, selector)}"
        attrs = {
            "page": norm,
            "selector": selector[:400],
            "tag": str(target.get("tag") or "")[:32],
        }
        for key in ("id", "testid", "role", "label", "text", "type"):
            value = target.get(key)
            if value:
                attrs[key] = str(value)[:160]
        refs.append(ObjectRef(elem_id, "ui_element", Q_TARGET, attrs))
        relations.add((elem_id, group_ids[0] if group_ids else APP_WEB_ID, REL_PART_OF))

    for i, gid in enumerate(group_ids):
        parent = group_ids[i + 1] if i + 1 < len(group_ids) else APP_WEB_ID
        relations.add((gid, parent, REL_PART_OF))

    return refs, relations


def derive_server_objects(
    *,
    path: str | None,
    properties: dict[str, Any] | None,
    anon_user_id: str,
    extra: list[ObjectRef] | None = None,
) -> tuple[list[ObjectRef], set[tuple[str, str, str]]]:
    """Objects + relations for one backend-emitted event (request/job/mcp)."""
    props = properties or {}
    norm = normalize_path(path)
    refs: list[ObjectRef] = [
        ObjectRef(APP_API_ID, "application", Q_APPLICATION, {"name": "mate-api"}),
        ObjectRef(f"user:{anon_user_id}", "user", Q_PERFORMED_BY),
    ]
    area = _task_area(norm, server=True)
    refs.append(ObjectRef(f"task:{area}", "task", Q_TASK, {"area": area}))

    def _collect(mapping: dict[str, Any]) -> None:
        for key, kind in _RESOURCE_KEYS.items():
            value = mapping.get(key)
            if isinstance(value, (str, int)) and str(value):
                refs.append(ObjectRef(f"{kind}:{value}", kind, Q_RESOURCE))

    _collect(props)
    path_params = props.get("path_params")
    if isinstance(path_params, dict):
        _collect(path_params)
    if extra:
        refs.extend(extra)
    return refs, set()


async def persist_event_objects(
    session: AsyncSession,
    *,
    user_id: str,
    event_refs: list[tuple[int, list[ObjectRef]]],
    relations: set[tuple[str, str, str]],
) -> None:
    """Upsert the object registry + O2O rows and insert E2O rows for a batch.

    Callers must have ``flush()``ed the event rows first so ids exist. All
    statements are ON CONFLICT-safe, so retried batches stay idempotent.
    """
    now = datetime.now(UTC).replace(tzinfo=None)

    objects: dict[str, ObjectRef] = {}
    e2o_rows: set[tuple[int, str, str]] = set()
    for event_id, refs in event_refs:
        for ref in refs:
            existing = objects.get(ref.object_id)
            if existing is None or (ref.attrs and not existing.attrs):
                objects[ref.object_id] = ref
            if ref.qualifier:
                e2o_rows.add((event_id, ref.object_id, ref.qualifier))

    if objects:
        stmt = sqlite_insert(AnalyticsObject).values(
            [
                {
                    "user_id": user_id,
                    "object_id": ref.object_id,
                    "object_type": ref.object_type,
                    "attrs": ref.attrs,
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                for ref in objects.values()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "object_id"],
            set_={"attrs": stmt.excluded.attrs, "last_seen_at": stmt.excluded.last_seen_at},
        )
        await session.execute(stmt)

    if relations:
        rel_stmt = sqlite_insert(AnalyticsObjectRelation).values(
            [
                {"user_id": user_id, "src_object_id": src, "tgt_object_id": tgt, "qualifier": q}
                for src, tgt, q in relations
            ]
        )
        await session.execute(rel_stmt.on_conflict_do_nothing())

    if e2o_rows:
        e2o_stmt = sqlite_insert(AnalyticsEventObject).values(
            [
                {"event_id": eid, "object_id": oid, "qualifier": q, "user_id": user_id}
                for eid, oid, q in e2o_rows
            ]
        )
        await session.execute(e2o_stmt.on_conflict_do_nothing())
