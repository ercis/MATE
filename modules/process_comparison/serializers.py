"""Serialise comparison results to plain JSON for the panel.

The DFG-diff payload mirrors the *shape* of the discovery module's
``serialize_dfg`` (activities + edges, no coordinates - the canvas lays out
client-side) but tags every node and edge with a diff ``status`` plus both
logs' frequencies. Kept self-contained: modules can't import each other.
"""

from __future__ import annotations

from typing import Any


def _status(in_a: bool, in_b: bool) -> str:
    if in_a and in_b:
        return "shared"
    return "only_a" if in_a else "only_b"


def _activity_freq(
    dfg: dict[tuple[str, str], int], starts: dict[str, int], ends: dict[str, int]
) -> dict[str, int]:
    """Per-activity frequency = outgoing-edge sum, floored by its start/end
    weight (mirrors discovery's serialize_dfg activity accounting)."""
    acts: dict[str, int] = {}
    for (src, tgt), freq in dfg.items():
        acts[src] = acts.get(src, 0) + freq
        acts.setdefault(tgt, 0)
    for a, f in starts.items():
        acts[a] = max(acts.get(a, 0), f)
    for a, f in ends.items():
        acts[a] = max(acts.get(a, 0), f)
    return acts


def build_activity_diff(
    dfg_a: dict[tuple[str, str], int],
    starts_a: dict[str, int],
    ends_a: dict[str, int],
    dfg_b: dict[tuple[str, str], int],
    starts_b: dict[str, int],
    ends_b: dict[str, int],
) -> list[dict[str, Any]]:
    """Per-activity diff rows (id/label/status/freq_a/freq_b/is_start/is_end).

    Shared by the DFG-diff payload and the BPMN-diff payload so the BPMN overlay
    can colour each task by the same status the graph uses.
    """
    freq_a = _activity_freq(dfg_a, starts_a, ends_a)
    freq_b = _activity_freq(dfg_b, starts_b, ends_b)
    rows: list[dict[str, Any]] = []
    for act in sorted(set(freq_a) | set(freq_b)):
        in_a, in_b = act in freq_a, act in freq_b
        rows.append(
            {
                "id": act,
                "label": act,
                "status": _status(in_a, in_b),
                "freq_a": int(freq_a.get(act, 0)),
                "freq_b": int(freq_b.get(act, 0)),
                "is_start": act in starts_a or act in starts_b,
                "is_end": act in ends_a or act in ends_b,
            }
        )
    return rows


def serialize_dfg_diff(
    dfg_a: dict[tuple[str, str], int],
    starts_a: dict[str, int],
    ends_a: dict[str, int],
    dfg_b: dict[tuple[str, str], int],
    starts_b: dict[str, int],
    ends_b: dict[str, int],
) -> dict[str, Any]:
    """Overlay two directly-follows graphs, classifying each node/edge.

    ``status`` is one of ``shared`` / ``only_a`` (baseline only) / ``only_b``
    (comparison only); ``freq_a`` / ``freq_b`` carry both counts so the canvas
    can label the delta.
    """

    activities = build_activity_diff(dfg_a, starts_a, ends_a, dfg_b, starts_b, ends_b)

    edges: list[dict[str, Any]] = []
    for src, tgt in sorted(set(dfg_a) | set(dfg_b)):
        in_a, in_b = (src, tgt) in dfg_a, (src, tgt) in dfg_b
        edges.append(
            {
                "id": f"{src}__{tgt}",
                "source": src,
                "target": tgt,
                "status": _status(in_a, in_b),
                "freq_a": int(dfg_a.get((src, tgt), 0)),
                "freq_b": int(dfg_b.get((src, tgt), 0)),
            }
        )

    return {
        "kind": "dfg_diff",
        "version": 1,
        "activities": activities,
        "edges": edges,
        "start_activities": sorted(set(starts_a) | set(starts_b)),
        "end_activities": sorted(set(ends_a) | set(ends_b)),
        "counts": {
            "shared_edges": sum(1 for e in edges if e["status"] == "shared"),
            "only_a_edges": sum(1 for e in edges if e["status"] == "only_a"),
            "only_b_edges": sum(1 for e in edges if e["status"] == "only_b"),
        },
    }


def serialize_variant_diff(
    log_ids: list[str],
    counts_per_log: list[dict[tuple[str, ...], int]],
    *,
    top_n: int = 60,
) -> dict[str, Any]:
    """Variant x log frequency matrix, sorted by impact vs the baseline.

    Baseline is the first log. ``counts``/``shares`` are aligned to ``log_ids``;
    ``max_abs_share_delta`` (share vs baseline) drives the sort so the variants
    that diverge most surface first.
    """
    totals = [sum(c.values()) or 1 for c in counts_per_log]
    all_variants = set().union(*counts_per_log) if counts_per_log else set()

    rows: list[dict[str, Any]] = []
    for variant in all_variants:
        counts = [int(c.get(variant, 0)) for c in counts_per_log]
        shares = [counts[i] / totals[i] for i in range(len(counts))]
        baseline_share = shares[0] if shares else 0.0
        max_delta = max((abs(s - baseline_share) for s in shares[1:]), default=0.0)
        rows.append(
            {
                "activities": list(variant),
                "label": " → ".join(variant) if variant else "(empty)",
                "counts": counts,
                "shares": shares,
                "in_baseline": counts[0] > 0,
                "max_abs_share_delta": max_delta,
            }
        )

    rows.sort(key=lambda r: r["max_abs_share_delta"], reverse=True)
    return {
        "kind": "variant_diff",
        "log_ids": log_ids,
        "totals": [int(t) for t in totals],
        "total_variants": len(all_variants),
        "variants": rows[:top_n],
    }


def serialize_activity_deltas(
    log_ids: list[str],
    freq_per_log: list[dict[str, int]],
    sojourn_per_log: list[dict[str, float]],
) -> dict[str, Any]:
    """Per-activity frequency + mean sojourn across logs, with deltas vs baseline.

    ``freq_share`` normalises out log-size differences; the delta arrays are
    each log minus the baseline (first log).
    """
    totals = [sum(f.values()) or 1 for f in freq_per_log]
    all_acts = set().union(*freq_per_log) if freq_per_log else set()

    rows: list[dict[str, Any]] = []
    for act in sorted(all_acts):
        freqs = [int(f.get(act, 0)) for f in freq_per_log]
        shares = [freqs[i] / totals[i] for i in range(len(freqs))]
        sojourns = [float(s.get(act, 0.0)) for s in sojourn_per_log]
        base_share = shares[0] if shares else 0.0
        rows.append(
            {
                "activity": act,
                "frequencies": freqs,
                "freq_shares": shares,
                "avg_sojourn_s": sojourns,
                "freq_share_delta_vs_baseline": [s - base_share for s in shares],
            }
        )

    rows.sort(
        key=lambda r: max((abs(d) for d in r["freq_share_delta_vs_baseline"][1:]), default=0.0),
        reverse=True,
    )
    return {
        "kind": "activity_deltas",
        "log_ids": log_ids,
        "activities": rows,
    }


def serialize_bpmn(bpmn_graph: Any) -> dict[str, Any]:
    """Serialise a pm4py BPMN graph to its standard XML form.

    Copied from the discovery module (modules can't import each other). pm4py
    exposes ``write_bpmn`` but no bytes/stream API, so we round-trip through a
    NamedTemporaryFile. ``auto_layout=False`` keeps the heavy graphviz layouter
    out of the request - the emitted XML has no BPMNDI and bpmn-auto-layout fills
    coordinates in client-side just before bpmn-js renders.
    """
    import tempfile
    from pathlib import Path

    import pm4py

    with tempfile.NamedTemporaryFile(suffix=".bpmn", delete=False) as fh:
        tmp = Path(fh.name)
    try:
        pm4py.write_bpmn(bpmn_graph, str(tmp), auto_layout=False)
        xml = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)

    return {"kind": "bpmn", "version": 1, "xml": xml}


# (key, label, unit, lower_is_better) - drives the KPI cards order + formatting.
_KPI_META: list[tuple[str, str, str, bool]] = [
    ("cases", "Cases", "count", False),
    ("events", "Events", "count", False),
    ("activities", "Distinct activities", "count", False),
    ("variants", "Variants", "count", False),
    ("throughput_s", "Mean throughput", "seconds", True),
]


def serialize_summary_delta(kpis_a: dict[str, float], kpis_b: dict[str, float]) -> dict[str, Any]:
    """Pairwise KPI delta (baseline ``a`` vs comparison ``b``).

    ``delta = value_b - value_a``; ``pct_delta`` is ``None`` when the baseline
    value is 0 (avoids divide-by-zero). ``lower_is_better`` lets the panel hint
    that a *negative* throughput delta is an improvement.
    """
    rows: list[dict[str, Any]] = []
    for key, label, unit, lower_is_better in _KPI_META:
        va = kpis_a.get(key, 0)
        vb = kpis_b.get(key, 0)
        delta = vb - va
        rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "value_a": va,
                "value_b": vb,
                "delta": delta,
                "pct_delta": (delta / va) if va else None,
                "lower_is_better": lower_is_better,
            }
        )
    return {"kind": "summary_delta", "kpis": rows}
