"""Serialise pm4py discovery outputs to plain JSON for the xyflow canvases.

The frontend lays out via dagre / a recursive walker - the JSON contains no
coordinates, only graph structure plus weights / labels.
"""

from __future__ import annotations

from typing import Any


def serialize_dfg(
    dfg: dict[tuple[str, str], int],
    start_acts: dict[str, int],
    end_acts: dict[str, int],
    durations: dict[tuple[str, str], float] | None = None,
    mean_positions: dict[str, float] | None = None,
) -> dict[str, Any]:
    activities: dict[str, int] = {}
    for (src, tgt), freq in dfg.items():
        activities[src] = activities.get(src, 0) + freq
        activities[tgt] = activities.get(tgt, 0)
    for a, freq in start_acts.items():
        activities[a] = max(activities.get(a, 0), freq)
    for a, freq in end_acts.items():
        activities[a] = max(activities.get(a, 0), freq)

    edges: list[dict[str, Any]] = []
    for (src, tgt), freq in dfg.items():
        edge: dict[str, Any] = {
            "id": f"{src}__{tgt}",
            "source": src,
            "target": tgt,
            "frequency": int(freq),
        }
        if durations is not None:
            dur = durations.get((src, tgt))
            # Reject NaN - pandas mean over a single-event group returns NaN.
            if dur is not None and dur == dur:
                edge["performance_seconds"] = float(dur)
        edges.append(edge)

    activity_payload: list[dict[str, Any]] = []
    for a, freq in activities.items():
        item: dict[str, Any] = {"id": a, "label": a, "frequency": int(freq)}
        if mean_positions is not None:
            pos = mean_positions.get(a)
            if pos is not None and pos == pos:
                item["mean_trace_position"] = float(pos)
        activity_payload.append(item)

    return {
        "kind": "dfg",
        # Bumped when the shape gains a field - used by the route's cache
        # check to invalidate older snapshots automatically.
        "version": 3,
        "activities": activity_payload,
        "edges": edges,
        "start_activities": {a: int(f) for a, f in start_acts.items()},
        "end_activities": {a: int(f) for a, f in end_acts.items()},
    }


def serialize_petri_net(net: Any, im: Any, fm: Any) -> dict[str, Any]:
    """Serialise a pm4py PetriNet + initial / final markings."""
    initial_places = {p for p in im}
    final_places = {p for p in fm}

    place_id = {p: f"p{i}" for i, p in enumerate(net.places)}
    transition_id = {t: f"t{i}" for i, t in enumerate(net.transitions)}

    places = [
        {
            "id": place_id[p],
            "label": str(p.name),
            "is_initial": p in initial_places,
            "is_final": p in final_places,
            "tokens": int(im[p]) if p in initial_places else 0,
        }
        for p in net.places
    ]

    transitions = []
    for t in net.transitions:
        label = t.label
        is_invisible = label is None
        transitions.append(
            {
                "id": transition_id[t],
                "label": "" if is_invisible else str(label),
                "is_invisible": is_invisible,
                "name": str(t.name),
            }
        )

    arcs = []
    for arc in net.arcs:
        src = arc.source
        tgt = arc.target
        src_id = place_id.get(src) or transition_id.get(src)
        tgt_id = place_id.get(tgt) or transition_id.get(tgt)
        if src_id is None or tgt_id is None:
            continue
        weight = getattr(arc, "weight", 1) or 1
        arcs.append(
            {
                "id": f"{src_id}__{tgt_id}",
                "source": src_id,
                "target": tgt_id,
                "weight": int(weight),
            }
        )

    return {
        "kind": "petri_net",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
    }


def _operator_to_string(op: Any) -> str | None:
    if op is None:
        return None
    name = getattr(op, "name", None) or str(op)
    return name.lower()


def serialize_process_tree(tree: Any) -> dict[str, Any]:
    """Walk the pm4py ProcessTree depth-first and emit a recursive dict."""
    counter = {"n": 0}

    def _walk(node: Any) -> dict[str, Any]:
        node_id = f"n{counter['n']}"
        counter["n"] += 1
        operator = _operator_to_string(getattr(node, "operator", None))
        label = getattr(node, "label", None)
        children_attr = getattr(node, "children", None) or []
        children = [_walk(child) for child in children_attr]
        return {
            "id": node_id,
            "operator": operator,
            "label": str(label) if label is not None else None,
            "children": children,
        }

    return {"kind": "process_tree", "root": _walk(tree)}


def serialize_prefix_tree(cases: list[list[str]]) -> dict[str, Any]:
    """Build a prefix tree (trie) from a list of activity sequences.

    Returns a flat node list with parent-id references instead of a nested
    tree, so FastAPI's JSON encoder never recurses deeply regardless of the
    maximum trace length.
    """
    # node_id -> {id, label, frequency, parent}
    node_store: dict[str, dict[str, Any]] = {
        "n0": {"id": "n0", "label": None, "frequency": len(cases), "parent": None}
    }
    # parent_id -> {activity -> child_id}
    children_map: dict[str, dict[str, str]] = {"n0": {}}
    id_seq = [1]

    for case in cases:
        current = "n0"
        for act in case:
            ch = children_map[current]
            if act not in ch:
                nid = f"n{id_seq[0]}"
                id_seq[0] += 1
                node_store[nid] = {"id": nid, "label": act, "frequency": 0, "parent": current}
                children_map[nid] = {}
                ch[act] = nid
            child = ch[act]
            node_store[child]["frequency"] += 1
            current = child

    return {"kind": "prefix_tree", "nodes": list(node_store.values())}


def serialize_bpmn(bpmn_graph: Any) -> dict[str, Any]:
    """Serialise a pm4py BPMN graph to its standard XML form.

    pm4py exposes write_bpmn() but not a bytes/stream API, so we round-trip
    through a NamedTemporaryFile. The XML it emits has no BPMNDI (diagram
    interchange) section - coordinates are filled in client-side by
    bpmn-auto-layout before bpmn-js renders.
    """
    import tempfile
    from pathlib import Path

    import pm4py

    with tempfile.NamedTemporaryFile(suffix=".bpmn", delete=False) as fh:
        tmp = Path(fh.name)
    try:
        # auto_layout=True would invoke pm4py's graphviz-based layouter - a
        # heavy dep we'd rather not require. bpmn-auto-layout on the client
        # fills in BPMNDI just before render.
        pm4py.write_bpmn(bpmn_graph, str(tmp), auto_layout=False)
        xml = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)

    return {"kind": "bpmn", "version": 1, "xml": xml}


def serialize_heuristics_net(hnet: Any) -> dict[str, Any]:
    """Serialise pm4py HeuristicsNet (frequency + dependency)."""
    occurrences: dict[str, int] = dict(getattr(hnet, "activities_occurrences", {}) or {})
    activities_attr = getattr(hnet, "activities", None) or []
    for a in activities_attr:
        occurrences.setdefault(a, 0)

    dfg = getattr(hnet, "dfg", None) or {}
    dependency_matrix = getattr(hnet, "dependency_matrix", None) or {}

    edges = []
    for (src, tgt), freq in dfg.items():
        dep = None
        if isinstance(dependency_matrix, dict):
            row = dependency_matrix.get(src)
            if isinstance(row, dict):
                dep = row.get(tgt)
        edges.append(
            {
                "id": f"{src}__{tgt}",
                "source": src,
                "target": tgt,
                "frequency": int(freq),
                "dependency": float(dep) if dep is not None else None,
            }
        )

    def _flatten(d: Any) -> dict[str, int]:
        if isinstance(d, dict):
            return {k: int(v) if not isinstance(v, dict) else int(sum(v.values())) for k, v in d.items()}
        if isinstance(d, list):
            out: dict[str, int] = {}
            for item in d:
                if isinstance(item, dict):
                    for k, v in item.items():
                        out[k] = out.get(k, 0) + int(v if not isinstance(v, dict) else sum(v.values()))
            return out
        return {}

    return {
        "kind": "heuristics_net",
        "activities": [{"id": a, "label": a, "frequency": int(occ)} for a, occ in occurrences.items()],
        "edges": edges,
        "start_activities": _flatten(getattr(hnet, "start_activities", {}) or {}),
        "end_activities": _flatten(getattr(hnet, "end_activities", {}) or {}),
    }
