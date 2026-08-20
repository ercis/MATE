"""Complexity measures faithful to Rüschel & Langer (Complexity.py, WWU
WWU_processcomplexity reference implementation).

The module exposes two layers:

* ``build_epa`` / entropy helpers - work on a state dict that mirrors the
  original ``Graph`` / ``ActivityType`` shape so the c-index partition logic
  can be reproduced 1:1.
* ``compute_basic_metrics`` - bundles every scalar metric the user asked for
  for a *normal* (un-enriched) event log.

The enriched-EPA variant lives in :mod:`enriched_core`.
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Any, Hashable

import pandas as pd


State = dict[str, Any]


def build_epa(
    df: pd.DataFrame,
    *,
    key_fn: Any = None,
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Build the Extended Prefix Automaton in global timestamp order.

    ``key_fn`` decides what makes two events follow the *same* successor edge.
    Default is the activity label (mirrors Complexity.py). The enriched
    variant passes a callable that also considers event/trace attributes
    (mirrors EnrichedComplexity.py).
    """
    if key_fn is None:
        def key_fn(row: Any) -> Hashable:
            return row.activity

    df_sorted = df.sort_values("timestamp", kind="mergesort")

    states: dict[int, State] = {
        0: {"c": 0, "j": 0, "children": {}, "timestamps": [], "activity": None}
    }
    last_state: dict[Any, int] = {}
    c_counter = 1
    next_id = 1

    for row in df_sorted.itertuples(index=False):
        case_id = row.case_id
        activity = row.activity
        ts = row.timestamp
        edge_key = key_fn(row)

        pred_id = last_state.get(case_id, 0)
        pred = states[pred_id]

        if edge_key in pred["children"]:
            curr_id = pred["children"][edge_key]
        else:
            if len(pred["children"]) > 0:
                c_counter += 1
                curr_c = c_counter
            else:
                curr_c = pred["c"] if pred_id != 0 else c_counter

            states[next_id] = {
                "c": curr_c,
                "j": pred["j"] + 1,
                "children": {},
                "timestamps": [],
                "activity": activity,
            }
            pred["children"][edge_key] = next_id
            curr_id = next_id
            next_id += 1

        states[curr_id]["timestamps"].append(ts)
        last_state[case_id] = curr_id

    c_index: dict[int, list[int]] = {}
    for sid in range(1, next_id):
        c = states[sid]["c"]
        c_index.setdefault(c, []).append(sid)

    return states, c_index


# ── Boltzmann entropy helper ──────────────────────────────────────────────────

def _boltzmann(total: float, partition_sizes: list[float]) -> tuple[float, float]:
    """``H = log(N)·N − Σ log(eᵢ)·eᵢ`` and its normaliser ``log(N)·N``."""
    if total <= 0:
        return 0.0, 0.0
    base = math.log(total) * total
    h = base
    for e in partition_sizes:
        if e > 0:
            h -= math.log(e) * e
    try:
        return h, h / base
    except ZeroDivisionError:
        return 0.0, 0.0


# ── EPA-based entropy ─────────────────────────────────────────────────────────

def variant_entropy(
    states: dict[int, State], c_index: dict[int, list[int]]
) -> tuple[float, float]:
    n_nodes = len(states) - 1
    if n_nodes <= 0:
        return 0.0, 0.0
    partition_sizes = [float(len(ids)) for ids in c_index.values()]
    return _boltzmann(float(n_nodes), partition_sizes)


def sequence_entropy(
    states: dict[int, State], c_index: dict[int, list[int]]
) -> tuple[float, float]:
    total = float(sum(len(states[sid]["timestamps"]) for sid in range(1, len(states))))
    if total <= 0:
        return 0.0, 0.0
    partition_sizes = [
        float(sum(len(states[sid]["timestamps"]) for sid in ids))
        for ids in c_index.values()
    ]
    return _boltzmann(total, partition_sizes)


def sequence_entropy_forgetting(
    states: dict[int, State],
    c_index: dict[int, list[int]],
    forgetting: str,
    k: float = 1.0,
) -> tuple[float, float]:
    """Sequence entropy with linear or exponential temporal forgetting.

    ``forgetting`` is one of ``"linear"`` / ``"exp"``. The normaliser is the
    unweighted-event Boltzmann base (matches Complexity.py's behaviour
    where ``normalize`` is computed once outside the branches).
    """
    all_ts: list[tuple[int, Any]] = [
        (sid, ts)
        for sid in range(1, len(states))
        for ts in states[sid]["timestamps"]
    ]
    if not all_ts:
        return 0.0, 0.0

    last_ts = max(ts for _, ts in all_ts)
    first_ts = min(ts for _, ts in all_ts)
    try:
        timespan = (last_ts - first_ts).total_seconds()
    except Exception:
        timespan = 0.0

    def _weight(ts: Any) -> float:
        try:
            t = (last_ts - ts).total_seconds() / timespan
        except (ZeroDivisionError, TypeError):
            return 1.0
        if forgetting == "linear":
            return 1.0 - t
        return math.exp(-k * t)

    total_events = float(len(all_ts))
    if total_events < 1:
        return 0.0, 0.0
    normalize = total_events * math.log(total_events)

    total_w = sum(_weight(ts) for _, ts in all_ts)
    if total_w <= 0:
        return 0.0, 0.0

    h = math.log(total_w) * total_w
    for ids in c_index.values():
        e = sum(_weight(ts) for sid in ids for ts in states[sid]["timestamps"])
        if e > 0:
            h -= math.log(e) * e

    try:
        return h, h / normalize
    except ZeroDivisionError:
        return 0.0, 0.0


# ── Lempel-Ziv complexity ─────────────────────────────────────────────────────

def lempel_ziv_complexity(df: pd.DataFrame) -> int:
    activities = df.sort_values("timestamp", kind="mergesort")["activity"].tolist()
    if not activities:
        return 0
    vocab = {a: i for i, a in enumerate(sorted({str(a) for a in activities}))}
    seq = tuple(vocab[str(a)] for a in activities)

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


# ── Variant DF-pattern helpers ────────────────────────────────────────────────

def _variant_df_patterns(
    df: pd.DataFrame,
) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], set[tuple[str, str]]]]:
    counts: dict[tuple[str, ...], int] = {}
    patterns: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby(
        "case_id", sort=False
    ):
        acts = tuple(str(a) for a in group["activity"].tolist())
        counts[acts] = counts.get(acts, 0) + 1
        if acts not in patterns:
            patterns[acts] = {
                (acts[i - 1], acts[i]) for i in range(1, len(acts))
            }
    return counts, patterns


# ── Affinity ──────────────────────────────────────────────────────────────────

def affinity(df: pd.DataFrame) -> float | None:
    counts, patterns = _variant_df_patterns(df)
    total_cases = sum(counts.values())
    if total_cases < 2:
        return None

    variants = list(counts.keys())
    m_affinity = 0.0
    for i, v1 in enumerate(variants):
        for j, v2 in enumerate(variants):
            if i != j:
                overlap = len(patterns[v1] & patterns[v2])
                union = len(patterns[v1] | patterns[v2])
                if union > 0:
                    m_affinity += (
                        (overlap / union) * counts[v1] * counts[v2]
                    )
            else:
                c = counts[v1]
                m_affinity += c * (c - 1)

    denom = total_cases * (total_cases - 1)
    return m_affinity / denom if denom else None


# ── Structure ─────────────────────────────────────────────────────────────────

def structure(df: pd.DataFrame) -> float | None:
    v = int(df["activity"].nunique())
    if v == 0:
        return None
    _counts, patterns = _variant_df_patterns(df)
    df_edges: set[tuple[str, str]] = set()
    for s in patterns.values():
        df_edges |= s
    return 1.0 - len(df_edges) / (v * v)


# ── Pentland measures ─────────────────────────────────────────────────────────

def pentland_task(states: dict[int, State]) -> int:
    return sum(
        s["j"]
        for sid, s in states.items()
        if sid != 0 and len(s["children"]) == 0
    )


def pentland_process(df: pd.DataFrame) -> float | None:
    v = int(df["activity"].nunique())
    _counts, patterns = _variant_df_patterns(df)
    df_edges: set[tuple[str, str]] = set()
    for s in patterns.values():
        df_edges |= s
    e = len(df_edges)
    # 10 ** (0.08 * (1 + e - v)) overflows float64 once the exponent passes ~308
    # - a wide log (many directly-follows edges e relative to activities v) makes
    # the metric astronomically large. Return None ("out of representable range",
    # like the other optional metrics here) rather than raising OverflowError and
    # failing the whole precompute job.
    exponent = 0.08 * (1 + e - v)
    if exponent > 300.0:
        return None
    return 10.0**exponent


# ── Deviation from random ─────────────────────────────────────────────────────

def deviation_from_random(df: pd.DataFrame) -> float | None:
    activities = df["activity"].astype(str).unique().tolist()
    v = len(activities)
    if v == 0:
        return None
    idx = {a: i for i, a in enumerate(activities)}
    net = [[0] * v for _ in range(v)]
    n_trans = 0
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby(
        "case_id", sort=False
    ):
        acts = [str(a) for a in group["activity"].tolist()]
        for i in range(1, len(acts)):
            net[idx[acts[i - 1]]][idx[acts[i]]] += 1
            n_trans += 1
    if n_trans == 0:
        return None
    a_mean = n_trans / (v * v)
    dev = math.sqrt(
        sum(((c - a_mean) / n_trans) ** 2 for row in net for c in row)
    )
    return 1.0 - dev


# ── Simple measures ───────────────────────────────────────────────────────────

def magnitude(df: pd.DataFrame) -> int:
    return int(len(df))


def support(df: pd.DataFrame) -> int:
    return int(df["case_id"].nunique())


def variety(df: pd.DataFrame) -> int:
    return int(df["activity"].nunique())


def level_of_detail(df: pd.DataFrame) -> float:
    per_case = df.groupby("case_id")["activity"].nunique()
    return float(per_case.mean()) if len(per_case) else 0.0


def trace_length_stats(df: pd.DataFrame) -> dict[str, float]:
    lengths = df.groupby("case_id").size().tolist()
    if not lengths:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {
        "min": float(min(lengths)),
        "avg": float(mean(lengths)),
        "max": float(max(lengths)),
    }


def pct_distinct_traces(df: pd.DataFrame) -> float:
    n_cases = int(df["case_id"].nunique())
    if n_cases == 0:
        return 0.0
    n_variants = int(
        df.sort_values("timestamp", kind="mergesort")
        .groupby("case_id")["activity"]
        .apply(lambda s: tuple(s.tolist()))
        .nunique()
    )
    return (n_variants / n_cases) * 100.0


def time_granularity(df: pd.DataFrame) -> float:
    """Mean of the per-case minimum inter-event delta (seconds)."""
    per_case_min: list[float] = []
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby(
        "case_id", sort=False
    ):
        ts = pd.to_datetime(group["timestamp"])
        diffs = ts.diff().dropna().dt.total_seconds()
        if not diffs.empty:
            per_case_min.append(float(diffs.min()))
    return float(mean(per_case_min)) if per_case_min else 0.0


# ── Public bundle ─────────────────────────────────────────────────────────────

def compute_basic_metrics(
    df: pd.DataFrame,
    *,
    exponential_k: float = 1.0,
) -> dict[str, Any]:
    """Compute the user-requested set of measures for a normal event log."""
    if df.empty or df["case_id"].nunique() == 0:
        return {}

    states, c_index = build_epa(df)

    h_var, h_var_norm = variant_entropy(states, c_index)
    h_seq, h_seq_norm = sequence_entropy(states, c_index)
    h_lin, h_lin_norm = sequence_entropy_forgetting(states, c_index, "linear")
    h_exp, h_exp_norm = sequence_entropy_forgetting(
        states, c_index, "exp", k=exponential_k
    )

    tl = trace_length_stats(df)

    return {
        "magnitude": magnitude(df),
        "support": support(df),
        "variety": variety(df),
        "level_of_detail": level_of_detail(df),
        "time_granularity_s": time_granularity(df),
        "structure": structure(df),
        "affinity": affinity(df),
        "trace_length_min": tl["min"],
        "trace_length_avg": tl["avg"],
        "trace_length_max": tl["max"],
        "distinct_traces_pct": pct_distinct_traces(df),
        "deviation_from_random": deviation_from_random(df),
        "lempel_ziv": lempel_ziv_complexity(df),
        "pentland_task": pentland_task(states),
        "pentland_process": pentland_process(df),
        "variant_entropy": h_var,
        "normalized_variant_entropy": h_var_norm,
        "sequence_entropy": h_seq,
        "normalized_sequence_entropy": h_seq_norm,
        "sequence_entropy_linear": h_lin,
        "normalized_sequence_entropy_linear": h_lin_norm,
        "sequence_entropy_exponential": h_exp,
        "normalized_sequence_entropy_exponential": h_exp_norm,
        "exponential_k": exponential_k,
    }
