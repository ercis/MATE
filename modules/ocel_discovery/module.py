"""Object-Centric Discovery - OC-DFG + object-type summary for OCEL logs.

Proves the object-centric module path end-to-end: every handler reads through
``ctx.object_log`` (the OCEL access layer), never ``ctx.event_log``. The module
is gated to ``log_model: object_centric`` in its manifest, so it is only ever
available on OCEL logs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mate.sdk import Module, ModuleContext, route

# Object-graph variants pm4py.discover_objects_graph supports. ``object_interaction``
# is computed in DuckDB (fast); the rest go through pm4py. Descendants / inheritance
# are directed; cobirth / codeath are undirected.
_GRAPH_TYPES = {
    "object_interaction": False,
    "object_descendants": True,
    "object_inheritance": True,
    "object_cobirth": False,
    "object_codeath": False,
}


def _count(members: object) -> int:
    """Length of a pm4py member collection, tolerant of missing keys."""
    try:
        return len(members)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def _median(xs: list[float]) -> float | None:
    """Median of an already-or-not sorted list of seconds (pm4py sorts them)."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _serialize_ocdfg(ocdfg: dict[str, Any]) -> dict[str, Any]:
    """Flatten pm4py's nested OC-DFG into a frontend-friendly shape.

    pm4py returns counts as ``{measure: {object_type: {key: [members...]}}}`` for
    three frequency measures (``event_couples`` / ``unique_objects`` /
    ``total_objects``) plus ``edges_performance`` (sorted seconds per edge). We
    collapse to per-object-type edge / start / end lists carrying all three
    counts and the edge's mean/median performance. ``count`` is kept as an alias
    of ``unique_objects`` for back-compat.
    """
    activities = sorted(str(a) for a in ocdfg.get("activities", []))
    object_types = sorted(str(t) for t in ocdfg.get("object_types", []))

    e = ocdfg.get("edges", {})
    edges_uo = e.get("unique_objects", {})
    edges_ev = e.get("event_couples", {})
    edges_to = e.get("total_objects", {})
    perf_ev = ocdfg.get("edges_performance", {}).get("event_couples", {})

    edges: list[dict[str, Any]] = []
    for ot, couples in edges_uo.items():
        for (src, tgt), members in couples.items():
            perf = perf_ev.get(ot, {}).get((src, tgt)) or []
            unique = len(members)
            edges.append(
                {
                    "object_type": str(ot),
                    "source": str(src),
                    "target": str(tgt),
                    "count": unique,
                    "unique_objects": unique,
                    "events": _count(edges_ev.get(ot, {}).get((src, tgt))),
                    "total_objects": _count(edges_to.get(ot, {}).get((src, tgt))),
                    "perf_mean": _mean(perf),
                    "perf_median": _median(perf),
                }
            )
    edges.sort(key=lambda e: (e["object_type"], -e["count"], e["source"], e["target"]))

    def _act_list(section: str) -> list[dict[str, Any]]:
        sec = ocdfg.get(section, {})
        uo = sec.get("unique_objects", {})
        ev = sec.get("events", {})
        to = sec.get("total_objects", {})
        out: list[dict[str, Any]] = []
        for ot, acts in uo.items():
            for act, members in acts.items():
                unique = len(members)
                out.append(
                    {
                        "object_type": str(ot),
                        "activity": str(act),
                        "count": unique,
                        "unique_objects": unique,
                        "events": _count(ev.get(ot, {}).get(act)),
                        "total_objects": _count(to.get(ot, {}).get(act)),
                    }
                )
        out.sort(key=lambda e: (e["object_type"], -e["count"]))
        return out

    return {
        "activities": activities,
        "object_types": object_types,
        "edges": edges,
        "start_activities": _act_list("start_activities"),
        "end_activities": _act_list("end_activities"),
    }


def _aggregate_object_pairs(
    pairs: Any,
    type_of: dict[str, str],
    *,
    directed: bool,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Aggregate a pm4py object-graph (a set of ``(oid, oid)`` tuples) to the
    object-type level. Returns ``(cross_type_edges, intra_type_counts)`` where
    edges are keyed by ``(source_type, target_type)`` - normalised to a sorted
    pair for undirected graphs - and counted by distinct object pairs."""
    seen: set[tuple[str, str]] = set()
    edges: dict[tuple[str, str], int] = {}
    intra: dict[str, int] = {}
    for o1, o2 in pairs:
        a, b = str(o1), str(o2)
        key_obj = (a, b) if directed else (min(a, b), max(a, b))
        if key_obj in seen:
            continue
        seen.add(key_obj)
        t1, t2 = type_of.get(a), type_of.get(b)
        if t1 is None or t2 is None:
            continue
        if t1 == t2:
            intra[t1] = intra.get(t1, 0) + 1
            continue
        key_t = (t1, t2) if directed else (min(t1, t2), max(t1, t2))
        edges[key_t] = edges.get(key_t, 0) + 1
    return edges, intra


def _pid(object_type: str, name: object) -> str:
    """Globally-unique node id. pm4py place / transition ``.name`` strings are
    only unique within a per-object-type net, so prefix with the object type."""
    return f"{object_type}::{name}"


def _serialize_ocpn(ocpn: Any) -> dict[str, Any]:
    """Flatten pm4py's object-centric Petri net into per-object-type place /
    transition / arc lists for the frontend.

    pm4py projects the OCPN into one classic Petri net per object type:
    ``ocpn["petri_nets"][ot] == (net, initial_marking, final_marking)``. A
    transition with ``label is None`` is a silent (tau) transition. The
    ``double_arcs_on_activity[ot][activity]`` flag marks variable arcs - drawn
    thicker, mirroring pm4py's own OCPN visualiser.
    """
    petri_nets = ocpn["petri_nets"]
    double_arcs = ocpn["double_arcs_on_activity"]

    activities = sorted(str(a) for a in ocpn["activities"])
    object_types = sorted(str(ot) for ot in petri_nets)

    nets: list[dict[str, Any]] = []
    for ot in object_types:
        try:
            net, im, fm = petri_nets[ot]
            initial = {p.name for p in im}
            final = {p.name for p in fm}

            places = sorted(
                (
                    {
                        "id": _pid(ot, p.name),
                        "label": str(p.name),
                        "is_initial": p.name in initial,
                        "is_final": p.name in final,
                    }
                    for p in net.places
                ),
                key=lambda d: d["id"],
            )

            trans_label = {t.name: t.label for t in net.transitions}
            transitions = sorted(
                (
                    {
                        "id": _pid(ot, t.name),
                        "label": "" if t.label is None else str(t.label),
                        "silent": t.label is None,
                    }
                    for t in net.transitions
                ),
                key=lambda d: d["id"],
            )

            ot_double = double_arcs.get(ot, {})
            arcs: list[dict[str, Any]] = []
            for i, arc in enumerate(net.arcs):
                src, tgt = arc.source.name, arc.target.name
                # The variable flag is keyed by the connected transition's label
                # (exactly one endpoint of a Petri-net arc is a transition).
                label = trans_label.get(src, trans_label.get(tgt))
                variable = bool(ot_double.get(label, False)) if label is not None else False
                arcs.append(
                    {
                        "id": _pid(ot, f"arc{i}"),
                        "source": _pid(ot, src),
                        "target": _pid(ot, tgt),
                        "variable": variable,
                    }
                )
            arcs.sort(key=lambda d: (d["source"], d["target"]))

            nets.append(
                {"object_type": ot, "places": places, "transitions": transitions, "arcs": arcs}
            )
        except Exception:
            # A degenerate / empty flattened log for this object type can make
            # the inductive miner produce a trivial net; emit an empty net so the
            # rest of the response still succeeds.
            nets.append({"object_type": ot, "places": [], "transitions": [], "arcs": []})

    return {"object_types": object_types, "activities": activities, "nets": nets}


class OcelDiscoveryModule(Module):
    id = "ocel_discovery"

    @route.get("/summary")
    async def summary(self, ctx: ModuleContext) -> dict[str, Any]:
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        async with ctx.object_log as ol:
            type_rows = await ol.duckdb_fetch(
                'SELECT "ocel:type" AS t, COUNT(*) AS n FROM ocel_objects '
                'GROUP BY "ocel:type" ORDER BY n DESC'
            )
            (events_count,) = (await ol.duckdb_fetch("SELECT COUNT(*) FROM ocel_events"))[0]
            (activities_count,) = (
                await ol.duckdb_fetch('SELECT COUNT(DISTINCT "ocel:activity") FROM ocel_events')
            )[0]
        return {
            "object_types": [{"type": str(t), "count": int(n)} for t, n in type_rows],
            "objects_count": int(sum(int(n) for _, n in type_rows)),
            "events_count": int(events_count),
            "activities_count": int(activities_count),
        }

    @route.get("/ocdfg")
    async def ocdfg(self, ctx: ModuleContext) -> dict[str, Any]:
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        async with ctx.object_log as ol:
            ocel = await ol.ocel()

        def _run() -> dict[str, Any]:
            import pm4py

            return _serialize_ocdfg(pm4py.discover_ocdfg(ocel))

        return await asyncio.to_thread(_run)

    @route.get("/ocpn")
    async def ocpn(self, ctx: ModuleContext) -> dict[str, Any]:
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        async with ctx.object_log as ol:
            ocel = await ol.ocel()

        def _run() -> dict[str, Any]:
            import pm4py

            return _serialize_ocpn(pm4py.discover_oc_petri_net(ocel))

        return await asyncio.to_thread(_run)

    @route.get("/objects-graph")
    async def objects_graph(
        self, ctx: ModuleContext, graph_type: str = "object_interaction"
    ) -> dict[str, Any]:
        """Object-to-object graph aggregated to the object-type level.

        ``object_interaction`` (the default) is computed directly in DuckDB -
        objects that share an event, counted by distinct object pairs - which
        avoids materialising every object pair. The other variants
        (descendants / inheritance / cobirth / codeath) come from
        ``pm4py.discover_objects_graph`` and are aggregated the same way.
        """
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        if graph_type not in _GRAPH_TYPES:
            raise ValueError(f"Unknown graph_type {graph_type!r}; one of {sorted(_GRAPH_TYPES)}.")
        directed = _GRAPH_TYPES[graph_type]

        edges: list[dict[str, Any]] = []
        intra: dict[str, int] = {}
        async with ctx.object_log as ol:
            type_rows = await ol.duckdb_fetch(
                'SELECT "ocel:type" AS t, COUNT(*) AS n FROM ocel_objects '
                'GROUP BY "ocel:type" ORDER BY n DESC'
            )
            if graph_type == "object_interaction":
                # Object type is functionally determined by oid, so DISTINCT over
                # (oa, ob, ta, tb) counts distinct co-occurring object pairs once,
                # regardless of how many events they share.
                edge_rows = await ol.duckdb_fetch(
                    "WITH rel AS ("
                    '  SELECT DISTINCT "ocel:eid" AS eid, "ocel:oid" AS oid, "ocel:type" AS t '
                    "  FROM ocel_relations"
                    "), pairs AS ("
                    "  SELECT DISTINCT a.oid AS oa, b.oid AS ob, a.t AS ta, b.t AS tb "
                    "  FROM rel a JOIN rel b ON a.eid = b.eid AND a.oid < b.oid"
                    ") SELECT LEAST(ta, tb) AS t1, GREATEST(ta, tb) AS t2, COUNT(*) AS cnt "
                    "FROM pairs GROUP BY 1, 2"
                )
                edges = [
                    {"source": str(t1), "target": str(t2), "count": int(c), "directed": False}
                    for t1, t2, c in edge_rows
                    if t1 != t2
                ]
                intra = {str(t1): int(c) for t1, t2, c in edge_rows if t1 == t2}
            else:
                obj_rows = await ol.duckdb_fetch('SELECT "ocel:oid", "ocel:type" FROM ocel_objects')
                ocel = await ol.ocel()
                type_of = {str(o): str(t) for o, t in obj_rows}

                def _run() -> tuple[dict[tuple[str, str], int], dict[str, int]]:
                    import pm4py

                    pairs = pm4py.discover_objects_graph(ocel, graph_type=graph_type)
                    return _aggregate_object_pairs(pairs, type_of, directed=directed)

                agg_edges, intra = await asyncio.to_thread(_run)
                edges = [
                    {"source": s, "target": t, "count": c, "directed": directed}
                    for (s, t), c in agg_edges.items()
                ]

        edges.sort(key=lambda e: (-e["count"], e["source"], e["target"]))
        object_types = [
            {"type": str(t), "count": int(n), "intra_count": int(intra.get(str(t), 0))}
            for t, n in type_rows
        ]
        return {
            "graph_type": graph_type,
            "directed": directed,
            "object_types": object_types,
            "edges": edges,
        }

    @route.get("/activity-object-types")
    async def activity_object_types(self, ctx: ModuleContext) -> dict[str, Any]:
        """Activity/object-type matrix: per (activity, type), the number of
        events that touch the type and the number of distinct objects involved.
        Pure DuckDB over the relations table (pm4py ``eve_to_obj_types``)."""
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        async with ctx.object_log as ol:
            rows = await ol.duckdb_fetch(
                'SELECT "ocel:activity" AS act, "ocel:type" AS t, '
                '  COUNT(DISTINCT "ocel:eid") AS events, '
                '  COUNT(DISTINCT "ocel:oid") AS objects '
                "FROM ocel_relations GROUP BY 1, 2"
            )
        cells = [
            {"activity": str(a), "object_type": str(t), "events": int(ev), "objects": int(ob)}
            for a, t, ev, ob in rows
        ]
        activities = sorted({c["activity"] for c in cells})
        object_types = sorted({c["object_type"] for c in cells})
        return {"activities": activities, "object_types": object_types, "cells": cells}

    @route.get("/objects-summary")
    async def objects_summary(self, ctx: ModuleContext) -> dict[str, Any]:
        """Object lifecycle summary (pm4py ``ocel_objects_summary`` fields,
        computed in DuckDB for scale): per-object-type aggregates plus the
        longest-lived objects. ``avg_interacting`` is derived from the O2O table
        when the log carries one (OCEL 2.0), else ``None``."""
        if ctx.object_log is None:
            raise RuntimeError("ocel_discovery requires an object-centric log.")
        async with ctx.object_log as ol:
            type_rows = await ol.duckdb_fetch(
                "WITH per_obj AS ("
                '  SELECT "ocel:oid" AS oid, "ocel:type" AS t, '
                '    epoch(MAX("ocel:timestamp")) - epoch(MIN("ocel:timestamp")) AS dur_s, '
                "    COUNT(*) AS n_events "
                "  FROM ocel_relations GROUP BY 1, 2"
                ") SELECT t, COUNT(*) AS objects, median(dur_s) AS median_dur, "
                "  avg(dur_s) AS avg_dur, avg(n_events) AS avg_events "
                "FROM per_obj GROUP BY t ORDER BY objects DESC"
            )
            top_rows = await ol.duckdb_fetch(
                'SELECT "ocel:oid" AS oid, "ocel:type" AS t, '
                '  epoch(MAX("ocel:timestamp")) - epoch(MIN("ocel:timestamp")) AS dur_s, '
                "  COUNT(*) AS n_events "
                "FROM ocel_relations GROUP BY 1, 2 ORDER BY dur_s DESC LIMIT 50"
            )
            interacting = await self._avg_interacting(ol)

        types = [
            {
                "type": str(t),
                "objects": int(objects),
                "median_duration_s": float(md) if md is not None else None,
                "avg_duration_s": float(ad) if ad is not None else None,
                "avg_events": float(ae) if ae is not None else None,
                "avg_interacting": interacting.get(str(t)),
            }
            for t, objects, md, ad, ae in type_rows
        ]
        top_objects = [
            {
                "oid": str(oid),
                "type": str(t),
                "duration_s": float(dur) if dur is not None else 0.0,
                "n_events": int(n),
            }
            for oid, t, dur, n in top_rows
        ]
        return {"types": types, "top_objects": top_objects, "has_interacting": bool(interacting)}

    @staticmethod
    async def _avg_interacting(ol: Any) -> dict[str, float]:
        """Average number of distinct related objects per object, by type, from
        the O2O table. Returns ``{}`` when the log has no usable O2O relations."""
        try:
            rows = await ol.duckdb_fetch(
                "WITH deg AS ("
                '  SELECT "ocel:oid" AS oid, COUNT(DISTINCT "ocel:oid_2") AS d '
                "  FROM ocel_o2o GROUP BY 1"
                "), obj AS ("
                '  SELECT "ocel:oid" AS oid, "ocel:type" AS t FROM ocel_objects'
                ") SELECT obj.t AS t, AVG(deg.d) AS avg_d "
                "FROM deg JOIN obj ON deg.oid = obj.oid GROUP BY 1"
            )
        except Exception:
            return {}
        return {str(t): float(a) for t, a in rows if a is not None}
