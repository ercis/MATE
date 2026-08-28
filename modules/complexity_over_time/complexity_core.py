"""Complexity measures faithful to Rüschel & Langer (Complexity.py, WWU
WWU_processcomplexity reference implementation).

The module exposes two layers:

* ``build_epa`` / entropy helpers - work on a state dict that mirrors the
  original ``Graph`` / ``ActivityType`` shape so the c-index partition logic
  can be reproduced 1:1.
* ``compute_basic_metrics`` - bundles every scalar metric the user asked for
  for a *normal* (un-enriched) event log.

The enriched-EPA variant lives in :mod:`enriched_core`.

Performance notes:

* The log is sorted **once** (:class:`_Prep`); every sequence-derived metric
  (variants, DF edges, deviation-from-random, Lempel-Ziv, per-case gaps)
  reads the shared integer-coded arrays instead of re-sorting/re-grouping.
* ``affinity`` is exact but vectorised: variants with identical
  directly-follows pattern sets collapse into groups and the pairwise
  Jaccard sum becomes a blocked float32 matmul (documented top-frequency
  truncation only beyond :data:`_AFFINITY_MAX_PATTERNS`).
* The forgetting sequence entropies compute their event weights with numpy;
  NaT timestamps get weight 1.0 (the original's fallback) instead of
  poisoning the sums, and non-finite metric values are sanitised to ``None``
  so the cached JSON stays valid.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

State = dict[str, Any]

ProgressFn = Callable[[float, str], None]


# ── Shared single-pass preparation ────────────────────────────────────────────


class _Prep:
    """One stable timestamp sort + integer-coded case/activity arrays.

    ``grp_*`` arrays are case-contiguous with within-case order equal to the
    global timestamp order - exactly what ``sort_values("timestamp",
    kind="mergesort").groupby("case_id", sort=False)`` produced before.
    """

    __slots__ = (
        "act_labels",
        "bounds",
        "counts",
        "grp_act",
        "grp_case",
        "grp_ts_i8",
        "n_cases",
        "n_events",
        "seqs",
        "stream_act",
        "stream_case",
        "stream_ts_i8",
    )

    def __init__(self, df: pd.DataFrame) -> None:
        work = df.sort_values("timestamp", kind="mergesort")  # NaT sorts last

        acts = work["activity"].astype(str).to_numpy()
        self.act_labels, stream_act = np.unique(acts, return_inverse=True)
        self.stream_act = stream_act.astype(np.int64, copy=False)
        self.stream_case = pd.factorize(work["case_id"].to_numpy())[0]

        ts = pd.to_datetime(work["timestamp"], errors="coerce", utc=True)
        self.stream_ts_i8 = ts.to_numpy(dtype="datetime64[ns]").view("int64")

        self.n_events = len(work)
        self.n_cases = int(self.stream_case.max()) + 1 if self.n_events else 0

        order = np.argsort(self.stream_case, kind="stable")
        self.grp_act = self.stream_act[order]
        self.grp_case = self.stream_case[order]
        self.grp_ts_i8 = self.stream_ts_i8[order]
        if self.n_events:
            change = np.flatnonzero(self.grp_case[1:] != self.grp_case[:-1]) + 1
            self.bounds = np.concatenate(([0], change, [self.n_events]))
        else:
            self.bounds = np.array([0], dtype=np.int64)

        self.seqs: list[tuple[int, ...]] = [
            tuple(self.grp_act[s:e].tolist()) for s, e in pairwise(self.bounds)
        ]
        self.counts: Counter[tuple[int, ...]] = Counter(self.seqs)

    def transitions(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Directly-follows pairs: (src_codes, dst_codes, grouped-row index of dst)."""
        if self.n_events < 2:
            empty = np.array([], dtype=np.int64)
            return empty, empty, empty
        same = self.grp_case[1:] == self.grp_case[:-1]
        idx = np.flatnonzero(same) + 1
        return self.grp_act[idx - 1], self.grp_act[idx], idx


def _get_prep(df: pd.DataFrame, prep: _Prep | None = None) -> _Prep:
    return prep if prep is not None else _Prep(df)


_I8_NAT = np.int64(np.datetime64("NaT", "ns").view("int64"))


# ── EPA construction ──────────────────────────────────────────────────────────


def build_epa(
    df: pd.DataFrame,
    *,
    key_fn: Any = None,
    prep: _Prep | None = None,
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Build the Extended Prefix Automaton in global timestamp order.

    ``key_fn`` decides what makes two events follow the *same* successor edge.
    Default is the activity label (mirrors Complexity.py). The enriched
    variant passes a callable that also considers event/trace attributes
    (mirrors EnrichedComplexity.py).

    State ``timestamps`` are stored as int64 epoch-nanoseconds (``NaT`` →
    sentinel) so the entropy weights can be vectorised.
    """
    if key_fn is None:
        p = _get_prep(df, prep)
        return _build_epa_coded(p.stream_case, p.stream_act, p.stream_ts_i8, p.act_labels)

    df_sorted = df.sort_values("timestamp", kind="mergesort")
    ts_i8 = (
        pd.to_datetime(df_sorted["timestamp"], errors="coerce", utc=True)
        .to_numpy(dtype="datetime64[ns]")
        .view("int64")
    )

    states: dict[int, State] = {
        0: {"c": 0, "j": 0, "children": {}, "timestamps": [], "activity": None}
    }
    last_state: dict[Any, int] = {}
    c_counter = 1
    next_id = 1

    for row, ts in zip(df_sorted.itertuples(index=False), ts_i8.tolist(), strict=True):
        case_id = row.case_id
        activity = row.activity
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

    return states, _c_index_of(states, next_id)


def _build_epa_coded(
    case_codes: np.ndarray,
    act_codes: np.ndarray,
    ts_i8: np.ndarray,
    act_labels: np.ndarray,
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Array fast path of :func:`build_epa` - identical automaton, int edge keys."""
    states: dict[int, State] = {
        0: {"c": 0, "j": 0, "children": {}, "timestamps": [], "activity": None}
    }
    last_state: dict[int, int] = {}
    c_counter = 1
    next_id = 1

    for case_id, act, ts in zip(
        case_codes.tolist(), act_codes.tolist(), ts_i8.tolist(), strict=True
    ):
        pred_id = last_state.get(case_id, 0)
        pred = states[pred_id]
        children = pred["children"]

        curr_id = children.get(act)
        if curr_id is None:
            if children:
                c_counter += 1
                curr_c = c_counter
            else:
                curr_c = pred["c"] if pred_id != 0 else c_counter

            states[next_id] = {
                "c": curr_c,
                "j": pred["j"] + 1,
                "children": {},
                "timestamps": [],
                "activity": str(act_labels[act]),
            }
            children[act] = next_id
            curr_id = next_id
            next_id += 1

        states[curr_id]["timestamps"].append(ts)
        last_state[case_id] = curr_id

    return states, _c_index_of(states, next_id)


def _c_index_of(states: dict[int, State], next_id: int) -> dict[int, list[int]]:
    c_index: dict[int, list[int]] = {}
    for sid in range(1, next_id):
        c_index.setdefault(states[sid]["c"], []).append(sid)
    return c_index


# ── Boltzmann entropy helper ──────────────────────────────────────────────────


def _boltzmann(total: float, partition_sizes: list[float]) -> tuple[float, float]:
    """``H = log(N)·N - Σ log(eᵢ)·eᵢ`` and its normaliser ``log(N)·N``."""
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


def variant_entropy(states: dict[int, State], c_index: dict[int, list[int]]) -> tuple[float, float]:
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
        float(sum(len(states[sid]["timestamps"]) for sid in ids)) for ids in c_index.values()
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
    where ``normalize`` is computed once outside the branches). Weights are
    computed vectorised over int64-ns timestamps; events with an unparseable
    timestamp (NaT) fall back to weight 1.0, mirroring the original's
    exception fallback, instead of poisoning the sums with NaN.
    """
    n_parts = len(c_index)
    ts_chunks: list[list[int]] = []
    part_chunks: list[np.ndarray] = []
    for pi, ids in enumerate(c_index.values()):
        for sid in ids:
            tlist = states[sid]["timestamps"]
            if tlist:
                ts_chunks.append(tlist)
                part_chunks.append(np.full(len(tlist), pi, dtype=np.int64))
    if not ts_chunks:
        return 0.0, 0.0

    ts = np.asarray([t for chunk in ts_chunks for t in chunk], dtype=np.int64)
    part = np.concatenate(part_chunks)

    valid = ts != _I8_NAT
    timespan = 0.0
    last = np.int64(0)
    if np.any(valid):
        last = ts[valid].max()
        first = ts[valid].min()
        timespan = float(last - first)

    weights = np.ones(ts.shape[0], dtype=np.float64)
    if timespan > 0:
        # Only valid timestamps get a forgetting weight (NaT keeps 1.0);
        # computing on the valid slice also avoids int64 overflow noise.
        t_rel = (last - ts[valid]).astype(np.float64) / timespan
        if forgetting == "linear":
            weights[valid] = 1.0 - t_rel
        else:
            weights[valid] = np.exp(-k * t_rel)

    total_events = float(ts.shape[0])
    if total_events < 1:
        return 0.0, 0.0
    normalize = total_events * math.log(total_events)

    total_w = float(weights.sum())
    if total_w <= 0:
        return 0.0, 0.0

    h = math.log(total_w) * total_w
    part_sums = np.bincount(part, weights=weights, minlength=n_parts)
    pos = part_sums > 0
    h -= float(np.sum(np.log(part_sums[pos]) * part_sums[pos]))

    try:
        return h, h / normalize
    except ZeroDivisionError:
        return 0.0, 0.0


# ── Lempel-Ziv complexity ─────────────────────────────────────────────────────


def _lz76_phrases(codes: Sequence[int]) -> int:
    """LZ76 phrase count via an incremental trie - O(n) total instead of the
    O(n·k) tuple-slice hashing of the previous ``seq[i:i+k] in seen`` scan.
    Identical parse: every phrase extends an already-seen phrase by one
    symbol, so the seen-set is exactly a trie."""
    n = len(codes)
    if n == 0:
        return 0
    root: dict[int, Any] = {}
    phrases = 0
    i = 0
    while i < n:
        node = root
        j = i
        while j < n:
            nxt = node.get(codes[j])
            if nxt is None:
                node[codes[j]] = {}
                break
            node = nxt
            j += 1
        phrases += 1
        i = j + 1
    return phrases


def lempel_ziv_complexity(df: pd.DataFrame, *, prep: _Prep | None = None) -> int:
    p = _get_prep(df, prep)
    return _lz76_phrases(p.stream_act.tolist())


# ── Affinity ──────────────────────────────────────────────────────────────────

# Exact affinity only depends on each variant's directly-follows pattern
# *set*: variants with identical patterns collapse into one group and the
# pairwise Jaccard sum becomes a blocked float32 matmul over the groupxedge
# incidence matrix. Beyond the caps the least-frequent pattern groups are
# dropped and the metric is computed over the retained trace population - a
# documented approximation for extreme logs (the exact pairwise sum over
# hundreds of thousands of variants is intractable in any representation).
_AFFINITY_MAX_PATTERNS = 4000
_AFFINITY_MAX_CELLS = 50_000_000
_AFFINITY_BLOCK_ROWS = 1024


def _affinity_from_counts(counts: dict[tuple[Hashable, ...], int]) -> float | None:
    total_cases = sum(counts.values())
    if total_cases < 2:
        return None

    pattern_counts: dict[frozenset, int] = {}
    empty_sq = 0.0
    empty_total = 0
    for acts, c in counts.items():
        if len(acts) > 1:
            pat = frozenset(pairwise(acts))
            pattern_counts[pat] = pattern_counts.get(pat, 0) + c
        else:
            empty_sq += float(c) * float(c)
            empty_total += c

    patterns = sorted(pattern_counts.items(), key=lambda kv: (-kv[1], len(kv[0])))
    if len(patterns) > _AFFINITY_MAX_PATTERNS:
        patterns = patterns[:_AFFINITY_MAX_PATTERNS]
    while patterns:
        edge_freq: Counter = Counter(e for pat, _ in patterns for e in pat)
        n_shared = sum(1 for n in edge_freq.values() if n >= 2)
        if len(patterns) * max(n_shared, 1) <= _AFFINITY_MAX_CELLS:
            break
        patterns = patterns[: max(len(patterns) // 2, 1)]

    included_cases = float(sum(c for _, c in patterns)) + float(empty_total)
    if included_cases < 2:
        return None

    m_nonempty = 0.0
    if patterns:
        # Only edges shared by ≥2 patterns can contribute *cross*-pattern
        # overlap; union sizes still use each pattern's full edge count. The
        # diagonal (a pattern vs itself) is exactly J=1 and is set
        # analytically because dropped single-pattern edges would otherwise
        # undercount self-overlap. Edges are sorted for run-to-run
        # determinism of the float summation order.
        edge_freq = Counter(e for pat, _ in patterns for e in pat)
        edge_idx = {e: i for i, e in enumerate(sorted(e for e, n in edge_freq.items() if n >= 2))}
        u = len(patterns)
        sizes = np.array([float(len(pat)) for pat, _ in patterns])
        weights = np.array([float(c) for _, c in patterns])

        # Diagonal contribution: J_gg = 1 → Σ_g C_g².
        m_nonempty = float(np.sum(weights * weights))

        if edge_idx:
            m_mat = np.zeros((u, len(edge_idx)), dtype=np.float32)
            for i, (pat, _) in enumerate(patterns):
                cols = [edge_idx[e] for e in pat if e in edge_idx]
                if cols:
                    m_mat[i, cols] = 1.0
            for s in range(0, u, _AFFINITY_BLOCK_ROWS):
                blk = m_mat[s : s + _AFFINITY_BLOCK_ROWS]
                overlap = (blk @ m_mat.T).astype(np.float64)
                union = sizes[s : s + _AFFINITY_BLOCK_ROWS, None] + sizes[None, :] - overlap
                jac = overlap / union  # patterns are nonempty → union ≥ 1
                rows = np.arange(s, min(s + _AFFINITY_BLOCK_ROWS, u))
                jac[rows - s, rows] = 0.0  # diagonal handled analytically
                m_nonempty += float(weights[s : s + _AFFINITY_BLOCK_ROWS] @ jac @ weights)

    m_affinity = m_nonempty + empty_sq - included_cases
    denom = included_cases * (included_cases - 1)
    return m_affinity / denom if denom else None


def affinity(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    """Average affinity (Günther 2009): mean over ordered case pairs of the
    Jaccard similarity of their DF pattern sets. Vectorised but numerically
    identical to the naive O(V²) double loop it replaces (same-variant pairs
    contribute ``c·(c-1)``; empty-vs-empty cross-variant pairs contribute 0).
    """
    p = _get_prep(df, prep)
    return _affinity_from_counts(p.counts)


# ── Structure ─────────────────────────────────────────────────────────────────


def _edges_and_vertices(p: _Prep) -> tuple[int, int]:
    v = len(p.act_labels)
    src, dst, _ = p.transitions()
    if src.size == 0:
        return 0, v
    return int(np.unique(src * np.int64(max(v, 1)) + dst).size), v


def structure(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    p = _get_prep(df, prep)
    e, v = _edges_and_vertices(p)
    if v == 0:
        return None
    return 1.0 - e / (v * v)


# ── Pentland measures ─────────────────────────────────────────────────────────


def pentland_task(states: dict[int, State]) -> int:
    return sum(s["j"] for sid, s in states.items() if sid != 0 and len(s["children"]) == 0)


def pentland_process(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    p = _get_prep(df, prep)
    e, v = _edges_and_vertices(p)
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


def deviation_from_random(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    p = _get_prep(df, prep)
    v = len(p.act_labels)
    if v == 0:
        return None
    src, dst, _ = p.transitions()
    n_trans = int(src.size)
    if n_trans == 0:
        return None
    net = np.bincount(src * np.int64(v) + dst, minlength=v * v).astype(np.float64)
    a_mean = n_trans / (v * v)
    dev = math.sqrt(float(np.sum(((net - a_mean) / n_trans) ** 2)))
    return 1.0 - dev


# ── Simple measures ───────────────────────────────────────────────────────────


def magnitude(df: pd.DataFrame) -> int:
    return len(df)


def support(df: pd.DataFrame) -> int:
    return int(df["case_id"].nunique())


def variety(df: pd.DataFrame) -> int:
    return int(df["activity"].nunique())


def level_of_detail(df: pd.DataFrame, *, prep: _Prep | None = None) -> float:
    p = _get_prep(df, prep)
    if not p.seqs:
        return 0.0
    distinct = [np.unique(p.grp_act[s:e]).size for s, e in pairwise(p.bounds)]
    return float(np.mean(distinct))


def trace_length_stats(df: pd.DataFrame, *, prep: _Prep | None = None) -> dict[str, float]:
    p = _get_prep(df, prep)
    lengths = np.diff(p.bounds)
    if lengths.size == 0:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {
        "min": float(lengths.min()),
        "avg": float(lengths.mean()),
        "max": float(lengths.max()),
    }


def pct_distinct_traces(df: pd.DataFrame, *, prep: _Prep | None = None) -> float:
    p = _get_prep(df, prep)
    if p.n_cases == 0:
        return 0.0
    return (len(p.counts) / p.n_cases) * 100.0


def time_granularity(df: pd.DataFrame, *, prep: _Prep | None = None) -> float:
    """Mean of the per-case minimum inter-event delta (seconds).

    Vectorised, and robust to unparseable timestamps: NaT gaps are skipped
    (the previous per-group ``pd.to_datetime`` raised on mixed-timezone or
    malformed values and failed the whole job)."""
    p = _get_prep(df, prep)
    _, _, idx = p.transitions()
    if idx.size == 0:
        return 0.0
    cur = p.grp_ts_i8[idx]
    prev = p.grp_ts_i8[idx - 1]
    valid = (cur != _I8_NAT) & (prev != _I8_NAT)
    if not np.any(valid):
        return 0.0
    secs = (cur[valid] - prev[valid]).astype(np.float64) / 1e9
    cases = p.grp_case[idx][valid]
    mins = np.full(p.n_cases, np.inf)
    np.minimum.at(mins, cases, secs)
    got = mins[np.isfinite(mins)]
    return float(got.mean()) if got.size else 0.0


# ── Sanitiser - JSON has no inf / nan ─────────────────────────────────────────


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ── Public bundle ─────────────────────────────────────────────────────────────


def compute_basic_metrics(
    df: pd.DataFrame,
    *,
    exponential_k: float = 1.0,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Compute the user-requested set of measures for a normal event log.

    ``progress`` (optional) receives coarse ``(fraction, message)`` ticks
    between metric groups so long computations stay observable.
    """
    if df.empty or df["case_id"].nunique() == 0:
        return {}

    def _tick(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, message)

    _tick(0.05, "Preparing event stream")
    prep = _Prep(df)

    _tick(0.15, "Building prefix automaton")
    states, c_index = build_epa(df, prep=prep)

    _tick(0.45, "Computing entropies")
    h_var, h_var_norm = variant_entropy(states, c_index)
    h_seq, h_seq_norm = sequence_entropy(states, c_index)
    h_lin, h_lin_norm = sequence_entropy_forgetting(states, c_index, "linear")
    h_exp, h_exp_norm = sequence_entropy_forgetting(states, c_index, "exp", k=exponential_k)

    _tick(0.65, "Computing variant distances")
    struct_val = structure(df, prep=prep)
    affinity_val = affinity(df, prep=prep)
    dev_val = deviation_from_random(df, prep=prep)
    pentland_proc = pentland_process(df, prep=prep)

    _tick(0.85, "Computing Lempel-Ziv complexity")
    lz = lempel_ziv_complexity(df, prep=prep)

    _tick(0.92, "Computing size measures")
    tl = trace_length_stats(df, prep=prep)

    values = {
        "magnitude": magnitude(df),
        "support": support(df),
        "variety": variety(df),
        "level_of_detail": level_of_detail(df, prep=prep),
        "time_granularity_s": time_granularity(df, prep=prep),
        "structure": struct_val,
        "affinity": affinity_val,
        "trace_length_min": tl["min"],
        "trace_length_avg": tl["avg"],
        "trace_length_max": tl["max"],
        "distinct_traces_pct": pct_distinct_traces(df, prep=prep),
        "deviation_from_random": dev_val,
        "lempel_ziv": lz,
        "pentland_task": pentland_task(states),
        "pentland_process": pentland_proc,
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
    return {key: _finite(val) for key, val in values.items()}
