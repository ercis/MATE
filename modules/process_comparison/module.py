"""Process Comparison - diff two or more *filtered views* of event logs.

Every route takes a single ``sides`` parameter: base64-encoded JSON holding an
ordered list of ``{"log": <log_id>, "filter": [<entry>, ...]}``. Side 0 is the
baseline (the "A" side), the rest are compared against it. Each side is opened
through ``ctx.open_event_log`` - the sanctioned, ownership-checked cross-log
accessor - so a user can only ever diff their own logs, and each side carries
its **own** filter, which replaces that log's committed Events-tab filter for
that read.

Because the filter belongs to the side rather than the request, the *same* log
is a legal pair of sides as long as the two filters differ: "region = North vs
region = South", "Q1 vs Q2" - a cohort comparison inside one log, not just
log-vs-log. Only a side pair that is identical in *both* log and filter is
refused (its diff is empty by construction).

Results are cached under a key hashing every side's (log id, parquet mtime,
filter), so a re-import of any log - or any edit to any side's filter -
invalidates exactly the affected entries.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from mate.sdk import Module, ModuleContext, route

from . import compute as comp
from .serializers import (
    build_activity_diff,
    serialize_activity_deltas,
    serialize_bpmn,
    serialize_dfg_diff,
    serialize_summary_delta,
    serialize_variant_diff,
)

# Filter ops the platform's Events-tab editor emits. Mirrored here rather than
# imported: a module never imports platform internals. The backend re-validates
# fields against the log's real columns when it bakes the predicate, so this is
# a shape gate, not the authority.
_FILTER_OPS: frozenset[str] = frozenset(
    {"contains", "equals", "gte", "lte", "is_null", "is_not_null", "in"}
)

# Upper bound on sides per request. Similarity is O(n^2) pairwise EMD and the
# panel's colour ramp holds six; beyond that the matrix stops being readable.
_MAX_SIDES = 6


def _side_label(index: int) -> str:
    """A / B / C … - how the panel names the sides in its own UI."""
    return chr(ord("A") + index) if index < 26 else f"#{index + 1}"


def _clean_filters(raw: Any) -> list[dict[str, Any]] | None:
    """Validate one side's filter list.

    ``None`` (absent) means "use the log's committed Events-tab filter"; a list
    replaces it (``[]`` = the raw, unfiltered log). Malformed entries raise 422
    rather than being dropped - a silently ignored filter would show the user a
    comparison of the wrong rows.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="A side's `filter` must be a list.")
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail="Filter entries must be objects.")
        field = entry.get("field")
        op = entry.get("op")
        if not isinstance(field, str) or not field:
            raise HTTPException(status_code=422, detail="filter.field must be a string.")
        if op not in _FILTER_OPS:
            raise HTTPException(status_code=422, detail=f"Unsupported filter op {op!r}.")
        cleaned.append({"field": field, "op": op, "value": entry.get("value")})
    return cleaned


def _side_key(side: dict[str, Any]) -> str:
    """Identity of a side = its log **and** its filter (order-insensitive).

    ``None`` (inherit the committed filter) keys apart from ``[]`` (raw log):
    they only coincide when the log happens to carry no committed filter, which
    this module can't see - collapsing them would let a "raw vs committed" pair
    share one cache entry.
    """
    filters = side.get("filter")
    if filters is None:
        return f"{side['log']}|~committed"
    entries = sorted(json.dumps(f, sort_keys=True, default=str) for f in filters)
    return f"{side['log']}|{'|'.join(entries)}"


def _decode_sides(raw: str) -> list[dict[str, Any]]:
    """Decode + validate the ``sides`` parameter (base64 of a JSON list)."""
    if not raw:
        raise HTTPException(status_code=422, detail="Select two views to compare.")
    try:
        payload = json.loads(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Malformed `sides` parameter.") from exc
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=422, detail="`sides` must be a list of {log, filter} entries."
        )
    if len(payload) > _MAX_SIDES:
        raise HTTPException(
            status_code=422, detail=f"At most {_MAX_SIDES} sides can be compared at once."
        )
    sides: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=422, detail="Each side must be an object.")
        log_id = entry.get("log")
        if not isinstance(log_id, str) or not log_id:
            raise HTTPException(status_code=422, detail="Each side needs a `log` id.")
        sides.append({"log": log_id, "filter": _clean_filters(entry.get("filter"))})
    keys = [_side_key(s) for s in sides]
    if len(set(keys)) != len(keys):
        raise HTTPException(
            status_code=422,
            detail=(
                "Two sides are the same log with the same filter - give one of them a "
                "different log or a different filter."
            ),
        )
    return sides


def _events_mtime(access: Any) -> float:
    path = getattr(access, "events_path", None)
    if not isinstance(path, Path):
        return 0.0
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cache_key(prefix: str, sides: list[dict[str, Any]], mtimes: list[float]) -> str:
    """Cache identity = every side's (log, filter) plus its parquet mtime.

    The filter is part of the key, so the same log compared under two different
    filters occupies two distinct entries instead of colliding on one.
    """
    sig = "|".join(f"{_side_key(s)}:{m:.0f}" for s, m in zip(sides, mtimes, strict=False))
    digest = hashlib.sha1(sig.encode()).hexdigest()[:16]
    return f"{prefix}__{digest}"


def _cached_keys_newest_first(cache: Any, prefix: str) -> list[str]:
    """Cache keys matching ``{prefix}__*``, newest first.

    The SDK cache Protocol has no list API, but the platform's ``ResultCache``
    exposes its directory - the same duck-typed peek the performance module
    uses for its freshness check. Returns ``[]`` when the cache doesn't expose
    a directory (e.g. a stub or an unbound cache).
    """
    root = getattr(cache, "dir", None)
    if root is None:
        return []
    try:
        candidates = sorted(
            Path(root).glob(f"{prefix}__*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    return [p.stem for p in candidates]


class ProcessComparisonModule(Module):
    id = "process_comparison"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting a comparison between "
        "two or more views of event logs (a baseline 'A' side versus comparison "
        "sides). A side is a log plus an optional row filter, so two sides may "
        "be two cohorts of the SAME log - always name a side by its letter and "
        "its filter, never by the log alone. Cite the specific similarity "
        "metrics (Jaccard overlaps, footprint similarity, EMD distance), KPI "
        "deltas and per-activity frequency-share shifts. Distinguish structural "
        "differences (new or removed activities and edges) from load shifts "
        "(same structure, different frequencies)."
    )
    guidance_user_prefix = "Interpret this event-log comparison:"

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        """Compact summary of the most recent cached comparison results.

        Result keys are ``{view}__{digest}`` (the digest hashes every side's log
        id, filter and parquet mtime), so the latest entry per view is resolved
        by peeking at the cache directory. Reads only ``ctx.cache`` - never the
        event log - so it works under the restricted AI/MCP context. Returns
        ``None`` until at least one comparison has been run.
        """

        async def _latest(prefix: str) -> dict[str, Any] | None:
            for key in _cached_keys_newest_first(ctx.cache, prefix)[:1]:
                cached = await ctx.cache.get(key)
                if isinstance(cached, dict):
                    return cached
            return None

        similarity = await _latest("similarity")
        summary = await _latest("summary")
        deltas = await _latest("activity-deltas")
        if similarity is None and summary is None and deltas is None:
            return None

        payload: dict[str, Any] = {}
        # `sides` names each side by log + filter, so the model can tell two
        # cohorts of one log apart; `log_ids` alone cannot.
        for source in (similarity, summary, deltas):
            if isinstance(source, dict) and source.get("sides"):
                payload["sides"] = source["sides"]
                break
        if similarity is not None:
            payload["similarity"] = {
                "log_ids": similarity.get("log_ids"),
                "metrics": similarity.get("metrics"),
            }
        if summary is not None:
            payload["summary_delta"] = {
                "baseline_log_id": summary.get("baseline_log_id"),
                "other_log_id": summary.get("other_log_id"),
                "kpis": summary.get("kpis"),
            }
        if deltas is not None:
            activities = [a for a in deltas.get("activities", []) if isinstance(a, dict)]
            payload["top_activity_deltas"] = {
                "log_ids": deltas.get("log_ids"),
                "activities": activities[:10],
            }
        return payload

    # -- shared loading ------------------------------------------------------

    async def _resolve(self, ctx: ModuleContext, sides: list[dict[str, Any]]) -> list[Any]:
        """Resolve every side to its own filtered EventLogAccess.

        Side 0 goes through ``open_event_log`` too (rather than ``ctx.event_log``)
        so all sides are built the same way and the baseline can carry its own
        filter - and can be a log other than the one the panel was opened on.
        Opening here enforces ownership on *every* request, even cache hits,
        since the cache key depends on the resolved mtimes.
        """
        accessors: list[Any] = []
        for side in sides:
            try:
                accessors.append(await ctx.open_event_log(side["log"], side["filter"]))
            except PermissionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return accessors

    async def _frames(self, accessors: list[Any]) -> list[Any]:
        """Load each side's rows, refusing a side its filter emptied.

        An empty side would sail through the primitives and render as "every
        activity removed", which reads as a real finding rather than the
        over-narrow filter it is.
        """
        frames: list[Any] = []
        for i, access in enumerate(accessors):
            async with access as log:
                df = await log.pandas()
            if len(df) == 0:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Side {_side_label(i)} matches no events - loosen or clear its filter."
                    ),
                )
            frames.append(df)
        return frames

    async def _cache_get(self, ctx: ModuleContext, key: str) -> dict[str, Any] | None:
        """Cache read that tolerates an *unscoped* context.

        Each side names its own log, but the route is still scoped by the
        platform's ``log_id`` query param, and that is what binds ``ctx.cache``
        to a directory. A caller that omits it (a hand-rolled API/MCP request)
        gets an unbound cache whose ``get``/``set`` raise ``RuntimeError``. That
        is a memoisation concern, not a reason to fail the comparison - so an
        unscoped call simply recomputes every time.
        """
        if not ctx.log_id:
            return None
        cached = await ctx.cache.get(key)
        return cached if isinstance(cached, dict) else None

    async def _cache_set(self, ctx: ModuleContext, key: str, value: dict[str, Any]) -> None:
        if not ctx.log_id:
            return
        await ctx.cache.set(key, value)

    async def _compute_over_set(
        self, ctx: ModuleContext, prefix: str, sides_raw: str, run: Any
    ) -> dict[str, Any]:
        """Decode the sides, cache on (log + filter + mtime), and run ``run``.

        ``run(ordered_ids, frames)`` is a pure, thread-offloaded function - all
        the per-route logic lives there; everything around it (ownership checks,
        caching, frame loading) is shared. Used by the N-side views (similarity,
        variants, activity deltas).
        """
        sides = _decode_sides(sides_raw)
        if len(sides) < 2:
            raise HTTPException(status_code=422, detail="Select at least two views to compare.")
        accessors = await self._resolve(ctx, sides)
        key = _cache_key(prefix, sides, [_events_mtime(a) for a in accessors])
        cached = await self._cache_get(ctx, key)
        if cached is not None:
            return cached
        ordered_ids = [s["log"] for s in sides]
        frames = await self._frames(accessors)
        result = await asyncio.to_thread(run, ordered_ids, frames)
        result["sides"] = sides
        await self._cache_set(ctx, key, result)
        return result

    async def _compute_pairwise(
        self, ctx: ModuleContext, prefix: str, sides_raw: str, run: Any
    ) -> dict[str, Any]:
        """Exactly-two-sides variant: cache on (logs + filters + mtimes), run ``run(frames)``.

        ``run(frames)`` is a pure, thread-offloaded function returning the payload
        dict; this helper attaches ``baseline_log_id``/``other_log_id`` (and the
        full ``sides``, which is what actually identifies each view once the two
        can share a log). Every pairwise view (dfg overlay, summary, bpmn) goes
        through here - a delta is inherently one-vs-one.
        """
        sides = _decode_sides(sides_raw)
        if len(sides) != 2:
            raise HTTPException(status_code=422, detail="This view compares exactly two views.")
        accessors = await self._resolve(ctx, sides)
        key = _cache_key(prefix, sides, [_events_mtime(a) for a in accessors])
        cached = await self._cache_get(ctx, key)
        if cached is not None:
            return cached

        frames = await self._frames(accessors)

        def _wrapped() -> dict[str, Any]:
            payload = run(frames)
            payload["baseline_log_id"] = sides[0]["log"]
            payload["other_log_id"] = sides[1]["log"]
            payload["sides"] = sides
            return payload

        result = await asyncio.to_thread(_wrapped)
        await self._cache_set(ctx, key, result)
        return result

    # -- routes --------------------------------------------------------------

    @route.get("/similarity")
    async def similarity(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            variant_counts = [comp.variant_counts(df) for df in frames]
            metrics = comp.pairwise_similarity(
                activities=[set(comp.activity_frequencies(df)) for df in frames],
                edges=[set(comp.discover_dfg(df)[0]) for df in frames],
                variants=[set(vc) for vc in variant_counts],
                footprints=[comp.footprint_relations(df) for df in frames],
                variant_counts_list=variant_counts,
            )
            return {"kind": "similarity", "log_ids": ordered_ids, "metrics": metrics}

        return await self._compute_over_set(ctx, "similarity", sides, _run)

    @route.get("/dfg-overlay")
    async def dfg_overlay(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        def _run(frames: list[Any]) -> dict[str, Any]:
            dfg_a, sa, ea = comp.discover_dfg(frames[0])
            dfg_b, sb, eb = comp.discover_dfg(frames[1])
            return serialize_dfg_diff(dfg_a, sa, ea, dfg_b, sb, eb)

        return await self._compute_pairwise(ctx, "dfg_overlay", sides, _run)

    @route.get("/summary")
    async def summary(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        """Headline KPI deltas (cases / events / activities / variants / throughput)."""

        def _run(frames: list[Any]) -> dict[str, Any]:
            return serialize_summary_delta(
                comp.summary_kpis(frames[0]), comp.summary_kpis(frames[1])
            )

        return await self._compute_pairwise(ctx, "summary", sides, _run)

    @route.get("/bpmn")
    async def bpmn(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        """Inductive-miner BPMN for each side + the per-activity diff the overlay
        colours by. BPMN mining is the heaviest route - thread-offloaded and cached.
        """

        def _run(frames: list[Any]) -> dict[str, Any]:
            dfg_a, sa, ea = comp.discover_dfg(frames[0])
            dfg_b, sb, eb = comp.discover_dfg(frames[1])
            return {
                "kind": "bpmn_diff",
                "xml_a": serialize_bpmn(comp.discover_bpmn(frames[0]))["xml"],
                "xml_b": serialize_bpmn(comp.discover_bpmn(frames[1]))["xml"],
                "activities": build_activity_diff(dfg_a, sa, ea, dfg_b, sb, eb),
            }

        return await self._compute_pairwise(ctx, "bpmn", sides, _run)

    @route.get("/variants")
    async def variants(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            return serialize_variant_diff(ordered_ids, [comp.variant_counts(df) for df in frames])

        return await self._compute_over_set(ctx, "variants", sides, _run)

    @route.get("/activity-deltas")
    async def activity_deltas(self, ctx: ModuleContext, sides: str = "") -> dict[str, Any]:
        def _run(ordered_ids: list[str], frames: list[Any]) -> dict[str, Any]:
            freqs = [comp.activity_frequencies(df) for df in frames]
            sojourns = [comp.activity_mean_sojourn(df) for df in frames]
            return serialize_activity_deltas(ordered_ids, freqs, sojourns)

        return await self._compute_over_set(ctx, "activity-deltas", sides, _run)
