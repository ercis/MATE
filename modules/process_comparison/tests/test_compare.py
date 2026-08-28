"""Unit tests for the pure comparison primitives + serialisers.

Builds small synthetic logs (identical, then one with an extra edge and a
dropped variant) and asserts the diff classification, EMD, and variant diff -
no ``ModuleContext`` needed.
"""

from __future__ import annotations

import pandas as pd
from modules.process_comparison import compute as comp
from modules.process_comparison.serializers import (
    build_activity_diff,
    serialize_activity_deltas,
    serialize_bpmn,
    serialize_dfg_diff,
    serialize_summary_delta,
    serialize_variant_diff,
)


def _log(traces: list[list[str]]) -> pd.DataFrame:
    """One row per event; each trace is a case. Timestamps step by case/event so
    ordering is unambiguous."""
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2020-01-01")
    for ci, acts in enumerate(traces):
        for ei, a in enumerate(acts):
            rows.append(
                {
                    "case_id": f"c{ci}",
                    "activity": a,
                    "timestamp": base + pd.Timedelta(hours=ci * 24 + ei),
                }
            )
    return pd.DataFrame(rows)


# Baseline: mostly a→b→c with a rare a→c→b.
LOG_A = _log([["a", "b", "c"]] * 3 + [["a", "c", "b"]])
# Comparison: drops the a→c→b variant, adds an a→b→d variant (new edge b→d, new
# activity d; loses edge c→? and activity-level differences).
LOG_B = _log([["a", "b", "c"]] * 2 + [["a", "b", "d"]] * 2)


def test_identical_logs_are_maximally_similar() -> None:
    counts = comp.variant_counts(LOG_A)
    assert comp.emd_distance(counts, counts) == 0.0

    dfg, sa, ea = comp.discover_dfg(LOG_A)
    diff = serialize_dfg_diff(dfg, sa, ea, dfg, sa, ea)
    assert diff["counts"]["only_a_edges"] == 0
    assert diff["counts"]["only_b_edges"] == 0
    assert all(e["status"] == "shared" for e in diff["edges"])
    assert all(e["freq_a"] == e["freq_b"] for e in diff["edges"])

    fp = comp.footprint_relations(LOG_A)
    # Jaccard of a set with itself == 1.
    metrics = comp.pairwise_similarity(
        activities=[set(comp.activity_frequencies(LOG_A))] * 2,
        edges=[set(dfg)] * 2,
        variants=[set(counts)] * 2,
        footprints=[fp, fp],
        variant_counts_list=[counts, counts],
    )
    assert metrics["footprints_similarity"][0][1] == 1.0
    assert metrics["activity_overlap"][0][1] == 1.0
    assert metrics["edge_overlap"][0][1] == 1.0
    assert metrics["emd"][0][1] == 0.0


def test_different_logs_diff_classification() -> None:
    dfg_a, sa, ea = comp.discover_dfg(LOG_A)
    dfg_b, sb, eb = comp.discover_dfg(LOG_B)
    diff = serialize_dfg_diff(dfg_a, sa, ea, dfg_b, sb, eb)

    edges_by_id = {e["id"]: e for e in diff["edges"]}
    # b→d exists only in B; c→b exists only in A.
    assert edges_by_id["b__d"]["status"] == "only_b"
    assert edges_by_id["c__b"]["status"] == "only_a"
    # a→b is in both.
    assert edges_by_id["a__b"]["status"] == "shared"

    # Activity d is new in B; classified only_b.
    acts_by_id = {a["id"]: a for a in diff["activities"]}
    assert acts_by_id["d"]["status"] == "only_b"

    # EMD between differing distributions is strictly positive.
    assert comp.emd_distance(comp.variant_counts(LOG_A), comp.variant_counts(LOG_B)) > 0.0


def test_variant_diff_surfaces_dropped_and_added() -> None:
    ids = ["A", "B"]
    counts = [comp.variant_counts(LOG_A), comp.variant_counts(LOG_B)]
    out = serialize_variant_diff(ids, counts)

    by_label = {tuple(v["activities"]): v for v in out["variants"]}
    # a→c→b is only in the baseline (A).
    acb = by_label[("a", "c", "b")]
    assert acb["counts"] == [1, 0]
    assert acb["in_baseline"] is True
    # a→b→d is only in the comparison (B).
    abd = by_label[("a", "b", "d")]
    assert abd["counts"] == [0, 2]
    assert abd["in_baseline"] is False
    # Shared a→b→c present in both.
    assert by_label[("a", "b", "c")]["counts"] == [3, 2]


def test_activity_deltas_share_and_sojourn() -> None:
    ids = ["A", "B"]
    freqs = [comp.activity_frequencies(LOG_A), comp.activity_frequencies(LOG_B)]
    sojourns = [comp.activity_mean_sojourn(LOG_A), comp.activity_mean_sojourn(LOG_B)]
    out = serialize_activity_deltas(ids, freqs, sojourns)

    by_act = {r["activity"]: r for r in out["activities"]}
    # d appears only in B → baseline share 0, positive delta in B.
    d_row = by_act["d"]
    assert d_row["frequencies"][0] == 0
    assert d_row["freq_share_delta_vs_baseline"][0] == 0.0
    assert d_row["freq_share_delta_vs_baseline"][1] > 0.0
    # Every activity carries a sojourn entry per log (0.0 when absent).
    assert len(d_row["avg_sojourn_s"]) == 2


def test_summary_kpis_exact() -> None:
    # LOG_A: 4 cases, 12 events, activities {a,b,c}, variants {abc, acb}; every
    # case spans exactly 2h (events step 1h apart) → mean throughput 7200s.
    k = comp.summary_kpis(LOG_A)
    assert k["cases"] == 4
    assert k["events"] == 12
    assert k["activities"] == 3
    assert k["variants"] == 2
    assert k["throughput_s"] == 7200.0


def test_summary_delta_sign_and_pct() -> None:
    out = serialize_summary_delta(comp.summary_kpis(LOG_A), comp.summary_kpis(LOG_B))
    by_key = {k["key"]: k for k in out["kpis"]}
    # B adds activity d → distinct activities 3 → 4.
    acts = by_key["activities"]
    assert acts["value_a"] == 3
    assert acts["value_b"] == 4
    assert acts["delta"] == 1
    assert acts["pct_delta"] == 1 / 3
    # Throughput is flagged "lower is better" so the panel can colour it.
    thr = by_key["throughput_s"]
    assert thr["unit"] == "seconds"
    assert thr["lower_is_better"] is True


def test_summary_delta_pct_guards_zero_baseline() -> None:
    a = {"cases": 0, "events": 0, "activities": 0, "variants": 0, "throughput_s": 0.0}
    b = {"cases": 5, "events": 9, "activities": 3, "variants": 2, "throughput_s": 60.0}
    by_key = {k["key"]: k for k in serialize_summary_delta(a, b)["kpis"]}
    assert by_key["cases"]["delta"] == 5
    assert by_key["cases"]["pct_delta"] is None  # baseline 0 → no percentage
    assert by_key["throughput_s"]["delta"] == 60.0


def test_build_activity_diff_statuses() -> None:
    dfg_a, sa, ea = comp.discover_dfg(LOG_A)
    dfg_b, sb, eb = comp.discover_dfg(LOG_B)
    rows = {r["id"]: r for r in build_activity_diff(dfg_a, sa, ea, dfg_b, sb, eb)}
    assert rows["d"]["status"] == "only_b"  # new in B
    assert rows["a"]["status"] == "shared"
    assert rows["a"]["is_start"] is True  # every trace starts with a


def test_serialize_bpmn_emits_xml_with_activities() -> None:
    payload = serialize_bpmn(comp.discover_bpmn(LOG_A))
    assert payload["kind"] == "bpmn"
    xml = payload["xml"]
    assert "definitions" in xml.lower()
    # Activities surface as task names in the BPMN export.
    for act in ("a", "b", "c"):
        assert f'name="{act}"' in xml
