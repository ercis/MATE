"""Unit tests for the Complexity v2 metric suite.

Exercises the pure functions in :mod:`modules.complexity_v2.metrics_core`
against synthetic logs - no ``ModuleContext`` needed.
"""

from __future__ import annotations

import math

import pandas as pd
from modules.complexity_v2.metrics_core import (
    METRIC_DEFS,
    _levenshtein,
    activity_variation,
    compute_all,
    order_variation,
    transition_probability_matrix,
)


def _log(variants: dict[str, int], *, base: str = "2023-01-01") -> pd.DataFrame:
    """Build a log from ``{"a,b,c": n_cases}`` variant specs."""
    rows: list[dict[str, object]] = []
    cid = 0
    start = pd.Timestamp(base)
    for spec, n in variants.items():
        acts = spec.split(",")
        for _ in range(n):
            cid += 1
            case = f"c{cid}"
            t0 = start + pd.Timedelta(days=cid)
            for step, act in enumerate(acts):
                rows.append(
                    {"case_id": case, "activity": act, "timestamp": t0 + pd.Timedelta(hours=step)}
                )
    return pd.DataFrame(rows)


# ── Shape ─────────────────────────────────────────────────────────────────────


def test_compute_all_returns_every_metric_key():
    df = _log({"a,b,c": 5, "a,c": 3, "a,b,b,c": 2})
    out = compute_all(df)
    for d in METRIC_DEFS:
        assert d["key"] in out, f"missing metric {d['key']}"


def test_empty_log_returns_sentinel():
    out = compute_all(pd.DataFrame({"case_id": [], "activity": [], "timestamp": []}))
    assert out == {"empty": True}


# ── Size ──────────────────────────────────────────────────────────────────────


def test_size_metrics():
    df = _log({"a,b,c": 5, "a,c": 3})  # 5·3 + 3·2 = 21 events, 8 cases, 3 types
    out = compute_all(df)
    assert out["n_events"] == 21
    assert out["n_sequences"] == 8
    assert out["n_event_types"] == 3
    assert out["min_seq_len"] == 2.0
    assert out["max_seq_len"] == 3.0
    assert out["n_unique_seq"] == 2
    assert math.isclose(out["perc_unique_seq"], (2 / 8) * 100.0)
    # avg inter-event gap is 1 hour everywhere → 3600 s.
    assert math.isclose(out["avg_td_e"], 3600.0, rel_tol=1e-6)


# ── Variation: order / activity variation ─────────────────────────────────────


def test_activity_variation_two_equal_activities_is_ln2():
    df = _log({"a,b": 10})  # 10 a's, 10 b's → uniform over 2 → ln 2
    assert math.isclose(activity_variation(df), math.log(2), rel_tol=1e-9)


def test_single_activity_has_zero_variation():
    df = _log({"a,a,a": 4})
    out = compute_all(df)
    assert out["activity_var"] == 0.0
    assert out["order_var"] == 0.0  # no activity-type changes


def test_order_variation_counts_only_changes():
    # one trace a,a,b,c : changes at b and c → 2 changes / 4 events.
    df = _log({"a,a,b,c": 1})
    assert math.isclose(order_variation(df), 2 / 4)


# ── Entropy bounds ────────────────────────────────────────────────────────────


def test_entropy_non_negative_and_normalized_in_unit_interval():
    df = _log({"a,b,c": 6, "a,c,d": 4, "a,b,d": 3, "a,b,b,c,d": 2})
    out = compute_all(df)
    for key in ("var_e", "seq_e"):
        assert out[key] >= 0
    for key in ("nvar_e", "nseq_e"):
        assert 0.0 <= out[key] <= 1.0 + 1e-9


# ── Distance: Levenshtein + structural variety ────────────────────────────────


def test_levenshtein_basic():
    assert _levenshtein(("a", "b", "c"), ("a", "b", "c")) == 0
    assert _levenshtein(("a", "b", "c"), ("a", "c")) == 1  # delete b
    assert _levenshtein((), ("a", "b")) == 2


def test_distance_metrics_present_and_non_negative():
    df = _log({"a,b,c": 5, "a,c": 3, "a,b,b,c": 2, "x,y,z": 4})
    out = compute_all(df)
    assert out["avg_edit_distance"] is not None and out["avg_edit_distance"] >= 0
    assert out["structural_var"] is not None and out["structural_var"] >= 0
    assert 0.0 <= out["structure"] <= 1.0


def test_downsample_flag_trips_under_cap():
    df = _log({f"a,b{i}": 1 for i in range(20)})  # 20 distinct variants
    out = compute_all(df, max_variants=5)
    assert out["downsampled"] is True
    assert out["distance_variants_used"] == 5
    full = compute_all(df, max_variants=1000)
    assert full["downsampled"] is False


# ── Variation: acyclic-paths overflow guard ───────────────────────────────────


def test_acyclic_paths_overflow_is_guarded():
    # Many activities + dense direct-follows → huge exponent. Must not raise;
    # the linear value may be None but the log10 must stay finite.
    acts = [chr(ord("A") + i) for i in range(26)]
    spec = ",".join(acts)
    df = _log({spec: 3, ",".join(reversed(acts)): 3})
    out = compute_all(df)
    assert "n_acyclic_paths" in out
    assert math.isfinite(out["n_acyclic_paths_log10"])


# ── Enriched entropy gating ───────────────────────────────────────────────────


def test_enriched_absent_without_schema():
    df = _log({"a,b,c": 4, "a,c": 2})
    out = compute_all(df, detected_schema=None)
    assert out["enriched_supported"] is False
    for key in ("en_var_e", "en_seq_e", "en_nvar_e", "en_nseq_e"):
        assert out[key] is None


def test_enriched_present_with_full_xes_schema():
    df = _log({"a,b,c": 4, "a,c": 2})
    schema = {
        "trace_attributes": ["variant", "concept:name", "creator", "variant-index"],
        "event_attributes": [
            "time:timestamp",
            "Resource",
            "lifecycle:transition",
            "concept:name",
            "Activity",
            "org:resource",
        ],
    }
    out = compute_all(df, detected_schema=schema)
    assert out["enriched_supported"] is True
    for key in ("en_var_e", "en_seq_e", "en_nvar_e", "en_nseq_e"):
        assert out[key] is not None


# ── prob-act-pairs transition matrix ──────────────────────────────────────────


def test_transition_matrix_rows_are_stochastic():
    df = _log({"a,b,c": 5, "a,c": 3})
    tm = transition_probability_matrix(df, top_k=10)
    assert set(tm["activities"]) == {"a", "b", "c"}
    idx = {a: i for i, a in enumerate(tm["activities"])}
    # 'a' goes to b (5x) and c (3x); its row sums to 1. 'c' is terminal -> row sums 0.
    a_row = tm["matrix"][idx["a"]]
    assert math.isclose(sum(a_row), 1.0, rel_tol=1e-6)
    c_row = tm["matrix"][idx["c"]]
    assert math.isclose(sum(c_row), 0.0, abs_tol=1e-9)


# ── Myers bit-parallel Levenshtein == reference DP ────────────────────────────


def test_myers_distance_matches_dp_fuzz():
    import random

    from modules.complexity_v2.metrics_core import _myers_distance, _myers_masks

    rng = random.Random(42)
    for _ in range(300):
        la, lb = rng.randint(0, 40), rng.randint(0, 40)
        a = tuple(rng.randint(0, 5) for _ in range(la))
        b = tuple(rng.randint(0, 5) for _ in range(lb))
        expected = _levenshtein(a, b)
        if len(a) >= len(b):
            got = _myers_distance(_myers_masks(a), len(a), b)
        else:
            got = _myers_distance(_myers_masks(b), len(b), a)
        assert got == expected, (a, b)


def test_myers_distance_long_sequences():
    from modules.complexity_v2.metrics_core import _myers_distance, _myers_masks

    # > 64 symbols forces the multi-word bigint path.
    a = tuple(i % 3 for i in range(150))
    b = tuple((i + 1) % 3 for i in range(140))
    expected = _levenshtein(a, b)
    got = _myers_distance(_myers_masks(a), len(a), b)
    assert got == expected


# ── Affinity: vectorised == naive O(V²) reference ─────────────────────────────


def _brute_affinity(counts: dict[tuple[str, ...], int]) -> float | None:
    from itertools import pairwise

    total = sum(counts.values())
    if total < 2:
        return None
    patterns = {v: set(pairwise(v)) for v in counts}
    variants = list(counts.keys())
    m = 0.0
    for i, v1 in enumerate(variants):
        for j, v2 in enumerate(variants):
            if i != j:
                overlap = len(patterns[v1] & patterns[v2])
                union = len(patterns[v1] | patterns[v2])
                if union > 0:
                    m += (overlap / union) * counts[v1] * counts[v2]
            else:
                c = counts[v1]
                m += c * (c - 1)
    denom = total * (total - 1)
    return m / denom if denom else None


def test_affinity_matches_bruteforce():
    import random

    from modules.complexity_v2.metrics_core import affinity

    cases: list[dict[tuple[str, ...], int]] = [
        {("a",): 3, ("b",): 2},  # only empty patterns
        {("a",): 3, ("a", "b"): 2, ("b", "a", "b"): 4},  # mixed empty / nonempty
        {("x", "y", "x", "y"): 2, ("x", "y", "x"): 3},  # identical pattern sets
        {("a", "b", "c"): 5, ("a", "c"): 3, ("a", "b", "b", "c"): 2},
    ]
    rng = random.Random(3)
    for _ in range(15):
        fuzz: dict[tuple[str, ...], int] = {}
        for _ in range(rng.randint(1, 10)):
            variant = tuple(rng.choice("abcd") for _ in range(rng.randint(1, 6)))
            fuzz[variant] = fuzz.get(variant, 0) + rng.randint(1, 4)
        cases.append(fuzz)

    for counts in cases:
        expected = _brute_affinity(counts)
        got = affinity(counts)
        if expected is None:
            assert got is None
        else:
            assert got is not None
            assert math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-9), counts


# ── Lempel-Ziv: trie parse == naive slice-hash parse ──────────────────────────


def test_lz76_matches_naive_parse():
    import random

    from modules.complexity_v2.metrics_core import _lz76_phrases

    def naive(seq: tuple[int, ...]) -> int:
        n = len(seq)
        seen: set[tuple[int, ...]] = set()
        complexity = 0
        i = 0
        while i < n:
            k = 1
            while i + k <= n and seq[i : i + k] in seen:
                k += 1
            seen.add(seq[i : i + k])
            complexity += 1
            i += k
        return complexity

    rng = random.Random(5)
    for _ in range(20):
        seq = tuple(rng.randint(0, 2) for _ in range(rng.randint(0, 200)))
        assert _lz76_phrases(seq) == naive(seq)


# ── Robustness: NaT timestamps & JSON-safety ──────────────────────────────────


def test_nat_timestamps_do_not_break_compute_all():
    import json

    df = _log({"a,b,c": 5, "a,c": 3})
    df.loc[df.index[::4], "timestamp"] = pd.NaT
    out = compute_all(df)
    assert out["n_events"] == len(df)
    for key, value in out.items():
        if isinstance(value, float):
            assert math.isfinite(value), f"{key} is not finite"
    json.dumps(out, allow_nan=False)


def test_all_nat_timestamps_yield_null_time_metric():
    df = _log({"a,b,c": 3})
    df["timestamp"] = pd.NaT
    out = compute_all(df)
    assert out["avg_td_e"] is None


# ── Word-op budget: extreme traces shrink the Levenshtein selection ───────────


def test_levenshtein_word_op_budget_shrinks_selection(monkeypatch):
    from modules.complexity_v2 import metrics_core

    monkeypatch.setattr(metrics_core, "_LEV_WORD_OP_BUDGET", 10.0)
    counts = {tuple(f"a{i}" for i in range(30 + j)): 1 for j in range(20)}
    variants, weights, downsampled = metrics_core._select_variants(counts, 300)
    assert downsampled is True
    assert 2 <= len(variants) < 20
    assert len(weights) == len(variants)
