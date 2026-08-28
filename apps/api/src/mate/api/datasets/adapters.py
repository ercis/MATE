"""Server-side ``kind -> DatasetEnvelope`` adapters + module-dataset resolution.

Python port of ``apps/web/lib/visualizations/adapters.ts``. Resolves a module
dataset by invoking its route handler through the loader (reusing the module's
result cache + the per-request ephemeral filter) and normalizes the response
into the canonical :class:`DatasetEnvelope`.
"""

from __future__ import annotations

import re
from typing import Any

from mate.api.datasets.envelope import (
    ColumnSpec,
    ColumnType,
    DatasetEnvelope,
    DatasetMeta,
    DatasetSchema,
    FieldRole,
    GraphData,
    GraphEdge,
    GraphNode,
    KpiData,
    KpiFormat,
    KpiMeasure,
    TreeData,
    TreeNode,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?")
_NUMERIC: frozenset[ColumnType] = frozenset({"number", "integer", "duration"})


def _prettify(key: str) -> str:
    return re.sub(r"[_-]+", " ", key).strip().title()


def _infer_type(value: Any) -> ColumnType:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        if _DATE_RE.match(value):
            return "datetime"
        try:
            f = float(value)
            return "integer" if f.is_integer() else "number"
        except (TypeError, ValueError):
            return "string"
    return "string"


def _role_for(col_type: ColumnType) -> FieldRole:
    if col_type in _NUMERIC:
        return "measure"
    if col_type == "datetime":
        return "time"
    return "dimension"


def table_from_rows(rows: list[dict[str, Any]]) -> tuple[list[ColumnSpec], list[dict[str, Any]]]:
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    columns: list[ColumnSpec] = []
    for k in keys:
        sample = next((r[k] for r in rows if r.get(k) is not None), None)
        col_type = _infer_type(sample)
        columns.append(
            ColumnSpec(id=k, label=_prettify(k), type=col_type, role=_role_for(col_type))
        )
    return columns, rows


def _kpi_format(key: str, value: float) -> KpiFormat | None:
    k = key.lower()
    if re.search(r"(fitness|precision|perc|percent|share|ratio|rate)", k) and 0 <= value <= 1:
        return "percent"
    if re.search(r"(^n[_.]|count|total|num)", k) and float(value).is_integer():
        return "integer"
    return "integer" if float(value).is_integer() else "number"


def _measures_from(obj: dict[str, Any], prefix: str = "") -> list[KpiMeasure]:
    out: list[KpiMeasure] = []
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(
                KpiMeasure(key=key, label=_prettify(k), value=float(v), format=_kpi_format(k, v))
            )
        elif isinstance(v, dict):
            out.extend(_measures_from(v, key))
    return out


def _graph_from_dfg(raw: dict[str, Any]) -> GraphData:
    activities = raw.get("activities") or []
    edges = raw.get("edges") or []
    nodes = [
        GraphNode(
            id=str(a.get("id")),
            label=str(a.get("label", a.get("id"))),
            value=a.get("frequency") if isinstance(a.get("frequency"), (int, float)) else None,
            kind="activity",
        )
        for a in activities
    ]
    g_edges = [
        GraphEdge(
            id=str(e.get("id", f"e{i}")),
            source=str(e.get("source")),
            target=str(e.get("target")),
            value=e.get("frequency") if isinstance(e.get("frequency"), (int, float)) else None,
            performanceSeconds=e.get("performance_seconds")
            if isinstance(e.get("performance_seconds"), (int, float))
            else None,
        )
        for i, e in enumerate(edges)
    ]
    return GraphData(
        directed=True,
        nodes=nodes,
        edges=g_edges,
        start=list((raw.get("start_activities") or {}).keys()),
        end=list((raw.get("end_activities") or {}).keys()),
    )


def _graph_from_petri(raw: dict[str, Any]) -> GraphData:
    places = raw.get("places") or []
    transitions = raw.get("transitions") or []
    arcs = raw.get("arcs") or []
    nodes = [
        GraphNode(id=str(p.get("id")), label=str(p.get("label", "")), kind="place") for p in places
    ]
    nodes += [
        GraphNode(
            id=str(t.get("id")),
            label="" if t.get("is_invisible") else str(t.get("label", t.get("name", ""))),
            kind="transition",
        )
        for t in transitions
    ]
    edges = [
        GraphEdge(
            id=str(a.get("id", f"a{i}")),
            source=str(a.get("source")),
            target=str(a.get("target")),
            value=a.get("weight") if isinstance(a.get("weight"), (int, float)) else None,
        )
        for i, a in enumerate(arcs)
    ]
    return GraphData(directed=True, nodes=nodes, edges=edges)


def _tree_from_process_tree(raw: dict[str, Any]) -> TreeData:
    counter = [0]

    def walk(node: dict[str, Any]) -> TreeNode:
        children = node.get("children") or []
        label = node.get("label") or node.get("operator") or "·"
        counter[0] += 1
        return TreeNode(
            id=str(node.get("id", f"n{counter[0]}")),
            label=str(label),
            children=[walk(c) for c in children],
        )

    root = raw.get("root")
    return TreeData(root=walk(root) if isinstance(root, dict) else TreeNode(id="root", label="·"))


def _tree_from_prefix(raw: dict[str, Any]) -> TreeData:
    flat = raw.get("nodes") or []
    by_id: dict[str, TreeNode] = {
        str(n.get("id")): TreeNode(
            id=str(n.get("id")),
            label=str(n.get("label") or "start"),
            value=n.get("frequency") if isinstance(n.get("frequency"), (int, float)) else None,
        )
        for n in flat
    }
    root: TreeNode | None = None
    for n in flat:
        node = by_id[str(n.get("id"))]
        parent = by_id.get(str(n.get("parent"))) if n.get("parent") is not None else None
        if parent is not None:
            parent.children.append(node)
        elif root is None:
            root = node
    return TreeData(root=root or TreeNode(id="root", label="start"))


def adapt(shape: str, raw: Any) -> DatasetEnvelope:
    """Normalize a module route response into the declared ``shape``."""
    obj: dict[str, Any] = raw if isinstance(raw, dict) else {}
    kind = obj.get("kind") if isinstance(obj.get("kind"), str) else ""

    if shape == "graph":
        g = _graph_from_petri(obj) if kind == "petri_net" else _graph_from_dfg(obj)
        return DatasetEnvelope(shape="graph", data=g, meta=DatasetMeta(sourceKind=kind))

    if shape == "tree":
        t = _tree_from_prefix(obj) if kind == "prefix_tree" else _tree_from_process_tree(obj)
        return DatasetEnvelope(shape="tree", data=t, meta=DatasetMeta(sourceKind=kind))

    if shape == "kpi":
        items: list[KpiMeasure] = []
        note: str | None = None
        if kind == "conformance":
            if obj.get("ran") is False or not isinstance(obj.get("kpis"), dict):
                note = "No conformance run yet - run conformance on this log first."
            else:
                items = _measures_from(obj["kpis"])
        elif isinstance(obj.get("basic"), dict) or isinstance(obj.get("enriched"), dict):
            items = _measures_from(obj.get("basic") or {})
            if isinstance(obj.get("enriched"), dict):
                items += _measures_from(obj["enriched"], "enriched")
        else:
            items = _measures_from(obj)
        return DatasetEnvelope(
            shape="kpi", data=KpiData(items=items), meta=DatasetMeta(sourceKind=kind, note=note)
        )

    if shape == "blob":
        value = obj.get("xml") if isinstance(obj.get("xml"), str) else obj.get("value") or ""
        media = "bpmn-xml" if kind == "bpmn" else "text"
        from mate.api.datasets.envelope import BlobData

        return DatasetEnvelope(
            shape="blob",
            data=BlobData(media=media, value=str(value)),
            meta=DatasetMeta(sourceKind=kind),
        )

    # table (default)
    rows: list[dict[str, Any]] = []
    note = None
    if kind == "conformance":
        rows = obj.get("per_activity") or []
        if not rows and obj.get("ran") is False:
            note = "No conformance run yet."
    elif isinstance(obj.get("drifts"), list):
        rows = obj["drifts"]
    else:
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    columns, rows = table_from_rows(rows)
    from mate.api.datasets.envelope import TableData

    return DatasetEnvelope(
        shape="table",
        schema=DatasetSchema(columns=columns),
        data=TableData(columns=columns, rows=rows),
        meta=DatasetMeta(
            sourceKind=kind, rowCount=len(rows), note=None if rows else (note or "No rows.")
        ),
    )


async def resolve_dataset(
    loader: Any,
    module_id: str,
    dataset_id: str,
    log_id: str,
    user_id: str,
    *,
    filter_override: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    restrict_event_log: bool = False,
) -> DatasetEnvelope:
    """Resolve a module dataset to a canonical envelope by invoking its route
    handler through the loader (reuses the module's result cache + filtering).
    ``restrict_event_log=True`` walls off raw event rows (AI/MCP callers)."""
    loaded = loader.loaded.get(module_id)
    if loaded is None:
        raise ValueError(f"Module {module_id!r} is not loaded.")
    entry = next((d for d in loaded.manifest.datasets if d.id == dataset_id), None)
    if entry is None:
        raise ValueError(f"Module {module_id!r} has no dataset {dataset_id!r}.")
    raw = await loader.run_dataset_route(
        module_id,
        entry.route,
        log_id,
        user_id,
        filter_override=filter_override,
        params=params,
        restrict_event_log=restrict_event_log,
    )
    return adapt(entry.shape, raw)
