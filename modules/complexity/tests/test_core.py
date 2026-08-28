"""Unit tests for the Complexity module's vectorised core.

Focus: numerical equivalence of the vectorised hot paths with naive
reference implementations, and robustness of the edge cases that used to
fail whole precompute jobs (overflow, NaT / unparseable timestamps,
non-finite JSON values).
"""

from __future__ import annotations

import json
import math
import random

import numpy as np
import pandas as pd
from modules.complexity.complexity_core import (
    affinity,
    compute_basic_metrics,
    lempel_ziv_complexity,
    pentland_process,
    sequence_entropy_forgetting,
    time_granularity,
)
from modules.complexity.enriched_core import compute_enriched_metrics, compute_metrics_bundle


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


# ── affinity: vectorised == naive O(V²) reference ─────────────────────────────


def _brute_affinity(df: pd.DataFrame) -> float | None:
    counts: dict[tuple[str, ...], int] = {}
    patterns: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        acts = tuple(str(a) for a in group["activity"].tolist())
        counts[acts] = counts.get(acts, 0) + 1
        patterns.setdefault(acts, {(acts[i - 1], acts[i]) for i in range(1, len(acts))})
    total = sum(counts.values())
    if total < 2:
        return None
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


def test_affinity_matches_bruteforce_on_edge_cases():
    cases = [
        {"a": 3, "b": 2},  # only single-event (empty-pattern) variants
        {"a": 3, "a,b": 2, "b,a,b": 4},  # mixed empty / nonempty patterns
        {"x,y,x,y": 2, "x,y,x": 3},  # distinct variants, identical patterns
        {"a,b,c": 5, "a,c": 3, "a,b,b,c": 2},
    ]
    for spec in cases:
        df = _log(spec)
        expected = _brute_affinity(df)
        got = affinity(df)
        assert got is not None and expected is not None
        assert math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-9), spec


def test_affinity_matches_bruteforce_fuzz():
    rng = random.Random(7)
    for _ in range(15):
        spec: dict[str, int] = {}
        for _ in range(rng.randint(1, 10)):
            length = rng.randint(1, 6)
            variant = ",".join(rng.choice("abcd") for _ in range(length))
            spec[variant] = spec.get(variant, 0) + rng.randint(1, 4)
        df = _log(spec)
        expected = _brute_affinity(df)
        got = affinity(df)
        if expected is None:
            assert got is None
        else:
            assert got is not None
            assert math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-9), spec


def test_affinity_single_case_is_none():
    assert affinity(_log({"a,b,c": 1})) is None


# ── Lempel-Ziv: trie parse == naive slice-hash parse ──────────────────────────


def _naive_lz(activities: list[str]) -> int:
    if not activities:
        return 0
    vocab = {a: i for i, a in enumerate(sorted(set(activities)))}
    seq = tuple(vocab[a] for a in activities)
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


def test_lempel_ziv_matches_naive_fuzz():
    rng = random.Random(11)
    for _ in range(10):
        n_cases = rng.randint(1, 6)
        rows = []
        t = pd.Timestamp("2023-01-01")
        stream: list[str] = []
        for c in range(n_cases):
            for _ in range(rng.randint(1, 40)):
                act = rng.choice("ab")
                t += pd.Timedelta(minutes=1)
                rows.append({"case_id": f"c{c}", "activity": act, "timestamp": t})
                stream.append(act)
        df = pd.DataFrame(rows)
        assert lempel_ziv_complexity(df) == _naive_lz(stream)


# ── pentland_process: overflow guard ──────────────────────────────────────────


def test_pentland_process_overflow_returns_none():
    # One long trace touching every ordered activity pair: e-v is huge, the
    # 10**exponent would overflow float64 - must yield None, not raise.
    acts = [f"a{i}" for i in range(70)]
    seq: list[str] = []
    for a in acts:
        for b in acts:
            seq.extend((a, b))
    t0 = pd.Timestamp("2023-01-01")
    df = pd.DataFrame(
        {
            "case_id": "c1",
            "activity": seq,
            "timestamp": [t0 + pd.Timedelta(seconds=i) for i in range(len(seq))],
        }
    )
    assert pentland_process(df) is None


# ── NaT / unparseable timestamps ──────────────────────────────────────────────


def test_nat_timestamps_do_not_poison_metrics():
    df = _log({"a,b,c": 4, "a,c": 3})
    df.loc[df.index[::5], "timestamp"] = pd.NaT
    out = compute_basic_metrics(df)
    assert out, "bundle must not be empty"
    for key, value in out.items():
        if isinstance(value, float):
            assert math.isfinite(value), f"{key} is not finite"
    # And the payload must serialise to strict JSON (no NaN/Infinity tokens).
    json.dumps(out, allow_nan=False)


def test_string_timestamps_are_coerced_not_fatal():
    df = _log({"a,b": 3, "a,b,c": 2})
    df["timestamp"] = df["timestamp"].astype(str)
    df.loc[df.index[0], "timestamp"] = "not-a-date"
    out = compute_basic_metrics(df)
    assert out["magnitude"] == len(df)
    assert out["time_granularity_s"] >= 0.0


def test_time_granularity_all_nat_is_zero():
    df = _log({"a,b,c": 2})
    df["timestamp"] = pd.NaT
    assert time_granularity(df) == 0.0


def test_forgetting_entropy_single_event_log():
    df = _log({"a": 2})
    out = compute_basic_metrics(df)
    assert out["sequence_entropy_linear"] == 0.0
    assert out["sequence_entropy_exponential"] == 0.0


# ── forgetting entropy: vectorised == naive reference ─────────────────────────


def _naive_forgetting(df: pd.DataFrame, forgetting: str, k: float = 1.0) -> tuple[float, float]:
    from modules.complexity.complexity_core import build_epa

    states, c_index = build_epa(df)
    all_ts = [(sid, ts) for sid in range(1, len(states)) for ts in states[sid]["timestamps"]]
    if not all_ts:
        return 0.0, 0.0
    last_ts = max(ts for _, ts in all_ts)
    first_ts = min(ts for _, ts in all_ts)
    timespan = float(last_ts - first_ts)

    def weight(ts: int) -> float:
        if timespan <= 0:
            return 1.0
        t = float(last_ts - ts) / timespan
        return (1.0 - t) if forgetting == "linear" else math.exp(-k * t)

    total_events = float(len(all_ts))
    normalize = total_events * math.log(total_events)
    total_w = sum(weight(ts) for _, ts in all_ts)
    if total_w <= 0:
        return 0.0, 0.0
    h = math.log(total_w) * total_w
    for ids in c_index.values():
        e = sum(weight(ts) for sid in ids for ts in states[sid]["timestamps"])
        if e > 0:
            h -= math.log(e) * e
    try:
        return h, h / normalize
    except ZeroDivisionError:
        return 0.0, 0.0


def test_forgetting_entropy_matches_reference():
    from modules.complexity.complexity_core import build_epa

    df = _log({"a,b,c": 6, "a,c,d": 4, "a,b,d": 3})
    states, c_index = build_epa(df)
    for mode in ("linear", "exp"):
        expected = _naive_forgetting(df, mode)
        got = sequence_entropy_forgetting(states, c_index, mode)
        assert math.isclose(got[0], expected[0], rel_tol=1e-9)
        assert math.isclose(got[1], expected[1], rel_tol=1e-9)


# ── enriched bundle reuse ─────────────────────────────────────────────────────


def test_enriched_reuses_sequence_only_metrics():
    df = _log({"a,b,c": 4, "a,c": 2})
    basic = compute_basic_metrics(df)
    enriched = compute_enriched_metrics(df, basic_metrics=basic)
    for key in ("affinity", "structure", "lempel_ziv", "pentland_process", "magnitude"):
        assert enriched[key] == basic[key]
    # EPA-derived values exist independently.
    assert enriched["variant_entropy"] >= 0.0
    assert enriched["pentland_task"] >= 0


def test_metrics_bundle_shape():
    df = _log({"a,b,c": 4, "a,c": 2})
    bundle = compute_metrics_bundle(df, None)
    assert bundle["enriched_supported"] is False
    assert bundle["enriched"] is None
    assert bundle["basic"]["support"] == 6


# ── progress callback ─────────────────────────────────────────────────────────


def test_progress_callback_receives_monotonic_fractions():
    df = _log({"a,b,c": 5, "a,c": 3})
    ticks: list[tuple[float, str]] = []
    compute_basic_metrics(df, progress=lambda f, m: ticks.append((f, m)))
    assert len(ticks) >= 4
    fractions = [f for f, _ in ticks]
    assert fractions == sorted(fractions)
    assert all(0.0 <= f <= 1.0 for f in fractions)


# ── output shape stays stable ─────────────────────────────────────────────────


def test_bundle_key_set_is_stable():
    df = _log({"a,b,c": 5, "a,c": 3})
    out = compute_basic_metrics(df)
    expected = {
        "magnitude",
        "support",
        "variety",
        "level_of_detail",
        "time_granularity_s",
        "structure",
        "affinity",
        "trace_length_min",
        "trace_length_avg",
        "trace_length_max",
        "distinct_traces_pct",
        "deviation_from_random",
        "lempel_ziv",
        "pentland_task",
        "pentland_process",
        "variant_entropy",
        "normalized_variant_entropy",
        "sequence_entropy",
        "normalized_sequence_entropy",
        "sequence_entropy_linear",
        "normalized_sequence_entropy_linear",
        "sequence_entropy_exponential",
        "normalized_sequence_entropy_exponential",
        "exponential_k",
    }
    assert set(out) == expected


def test_empty_log_returns_empty_dict():
    empty = pd.DataFrame({"case_id": [], "activity": [], "timestamp": []})
    assert compute_basic_metrics(empty) == {}


def test_values_reasonable_on_small_log():
    df = _log({"a,b,c": 5, "a,c": 3})
    out = compute_basic_metrics(df)
    assert out["magnitude"] == 21
    assert out["support"] == 8
    assert out["variety"] == 3
    assert out["trace_length_min"] == 2.0
    assert out["trace_length_max"] == 3.0
    assert math.isclose(out["distinct_traces_pct"], 25.0)
    assert 0.0 <= out["normalized_variant_entropy"] <= 1.0
    assert 0.0 <= out["normalized_sequence_entropy"] <= 1.0
    # inter-event gap is exactly 1h in the fixture
    assert math.isclose(out["time_granularity_s"], 3600.0)
    assert isinstance(out["lempel_ziv"], (int, np.integer))
