"""Complexity v2 - the full event-log complexity suite from Langer (2026),
*Understanding Business Process Complexity* (WWU master thesis,
``MrWaitomo/WWU_processcomplexity``).

This implements the metrics in scope of the thesis (Table 3.3), faithful to
the definitions in §2.4 / §3.3, grouped by the paper's categories:

* **Entropy** - variant / sequence entropy and their normalised forms,
  computed over the Extended Prefix Automaton (EPA) of the log.
* **Enriched Entropy** - the same four measures over an *Enriched* EPA whose
  edges also key on the IEEE-XES event/trace attributes, when present.
* **Size** - number of events / event types / sequences, sequence-length
  statistics, and the average time difference between consecutive events.
* **Variation** - number of acyclic paths and ties in the transition matrix,
  Lempel-Ziv complexity, (percentage of) unique sequences, average distinct
  events per sequence, order variation and activity variation.
* **Distance** - average affinity, structure, deviation from random, average
  (pairwise Levenshtein) edit distance and structural process variety
  (Levenshtein distance matrix → agglomerative clustering → Σ merge heights).

The ``prob-act-pairs`` metric (Grisold et al., 2022) is a transition
*matrix*, not a scalar - :func:`transition_probability_matrix` returns it for
the panel's heatmap, mirroring the thesis appendix D.

Performance notes (the suite must finish on 100k+-event, 1000+-variant logs):

* The log is sorted **once**; every sequence-derived metric (variants,
  directly-follows edges, order variation, deviation-from-random, LZ,
  transition matrix, inter-event gaps) reads the shared :class:`_Prep`
  arrays instead of re-sorting / re-grouping per metric.
* ``affinity`` is exact but vectorised: variants are grouped by their
  directly-follows pattern set and the pairwise Jaccard sum becomes a
  blocked float32 matmul over the pattern-incidence matrix (documented
  top-frequency truncation only beyond :data:`_AFFINITY_MAX_PATTERNS`).
* Pairwise Levenshtein uses Myers' bit-parallel algorithm (O(len/w) words
  per DP column instead of a full Python DP row), with a documented
  word-op budget that shrinks the variant selection for extreme logs.

Everything is pure-function and self-contained: the module never imports from
``apps/*`` or a sibling module.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Hashable, Sequence
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

State = dict[str, Any]


# ── Metric metadata (paper labels, names, sources) ────────────────────────────
# Drives the panel: one row per metric, grouped by category, exactly as in the
# thesis Table 3.3. ``key`` is the field returned by :func:`compute_all`.

METRIC_DEFS: list[dict[str, str]] = [
    # Entropy
    {
        "key": "var_e",
        "label": "var-e",
        "name": "Variant Entropy",
        "category": "Entropy",
        "source": "Augusto et al. (2022)",
        "description": "Boltzmann graph entropy over the EPA state partitions.",
    },
    {
        "key": "seq_e",
        "label": "seq-e",
        "name": "Sequence Entropy",
        "category": "Entropy",
        "source": "Augusto et al. (2022)",
        "description": "EPA entropy weighted by the event frequency per state.",
    },
    {
        "key": "nvar_e",
        "label": "nvar-e",
        "name": "Normalized Variant Entropy",
        "category": "Entropy",
        "source": "Augusto et al. (2022)",
        "description": "Variant entropy scaled to [0,1] by |S|·log|S|.",
    },
    {
        "key": "nseq_e",
        "label": "nseq-e",
        "name": "Normalized Sequence Entropy",
        "category": "Entropy",
        "source": "Augusto et al. (2022)",
        "description": "Sequence entropy scaled to [0,1].",
    },
    # Enriched entropy
    {
        "key": "en_var_e",
        "label": "en-var-e",
        "name": "Enriched Variant Entropy",
        "category": "Enriched Entropy",
        "source": "Vidgof & Mendling (2023)",
        "description": "Variant entropy over an EPA keyed on event/trace attributes.",
    },
    {
        "key": "en_seq_e",
        "label": "en-seq-e",
        "name": "Enriched Sequence Entropy",
        "category": "Enriched Entropy",
        "source": "Vidgof & Mendling (2023)",
        "description": "Sequence entropy over the enriched EPA.",
    },
    {
        "key": "en_nvar_e",
        "label": "en-nvar-e",
        "name": "Enriched Normalized Variant Entropy",
        "category": "Enriched Entropy",
        "source": "Vidgof & Mendling (2023)",
        "description": "Normalized enriched variant entropy.",
    },
    {
        "key": "en_nseq_e",
        "label": "en-nseq-e",
        "name": "Enriched Normalized Sequence Entropy",
        "category": "Enriched Entropy",
        "source": "Vidgof & Mendling (2023)",
        "description": "Normalized enriched sequence entropy.",
    },
    # Size
    {
        "key": "n_events",
        "label": "#-e",
        "name": "Number of Events",
        "category": "Size",
        "source": "Günther (2009)",
        "description": "Total events across all traces (magnitude).",
    },
    {
        "key": "n_event_types",
        "label": "#-et",
        "name": "Number of Event Types",
        "category": "Size",
        "source": "Günther (2009)",
        "description": "Distinct activities (variety).",
    },
    {
        "key": "n_sequences",
        "label": "#-seq",
        "name": "Number of Sequences",
        "category": "Size",
        "source": "Günther (2009)",
        "description": "Total traces (support).",
    },
    {
        "key": "min_seq_len",
        "label": "min-seq-len",
        "name": "Minimum Sequence Length",
        "category": "Size",
        "source": "van der Aalst (2016)",
        "description": "Shortest trace, in events.",
    },
    {
        "key": "avg_seq_len",
        "label": "avg-seq-len",
        "name": "Average Sequence Length",
        "category": "Size",
        "source": "van der Aalst (2016)",
        "description": "Mean events per trace.",
    },
    {
        "key": "max_seq_len",
        "label": "max-seq-len",
        "name": "Maximum Sequence Length",
        "category": "Size",
        "source": "van der Aalst (2016)",
        "description": "Longest trace, in events.",
    },
    {
        "key": "avg_td_e",
        "label": "avg-td-e",
        "name": "Avg. Time Diff. between Consecutive Events",
        "category": "Size",
        "source": "Günther (2009)",
        "description": "Mean (over traces) of the mean inter-event gap, in seconds.",
    },
    # Variation
    {
        "key": "n_acyclic_paths",
        "label": "#-acyclic-paths",
        "name": "Number of Acyclic Paths",
        "category": "Variation",
        "source": "Hærem et al. (2015)",
        "description": "10^(0.08*(1 + edges - vertices)) over the transition matrix.",
    },
    {
        "key": "n_ties",
        "label": "#-ties",
        "name": "Number of Ties",
        "category": "Variation",
        "source": "Hærem et al. (2015)",
        "description": "Σ root→end-node path lengths across all variants (EPA leaf depths).",
    },
    {
        "key": "lempel_ziv",
        "label": "lempel-ziv",
        "name": "Lempel-Ziv Complexity",
        "category": "Variation",
        "source": "Pentland (2003)",
        "description": "LZ76 phrases over the time-ordered activity stream.",
    },
    {
        "key": "n_unique_seq",
        "label": "#-unique-seq",
        "name": "Number of Unique Sequences",
        "category": "Variation",
        "source": "van der Aalst (2016)",
        "description": "Distinct trace variants.",
    },
    {
        "key": "perc_unique_seq",
        "label": "perc-unique-seq",
        "name": "Percentage of Unique Sequences",
        "category": "Variation",
        "source": "van der Aalst (2016)",
        "description": "Unique variants / traces · 100.",
    },
    {
        "key": "avg_distinct_e",
        "label": "avg-distinct-e",
        "name": "Avg. Distinct Events per Sequence",
        "category": "Variation",
        "source": "Günther (2009)",
        "description": "Mean number of distinct activities per trace.",
    },
    {
        "key": "order_var",
        "label": "order_var",
        "name": "Order Variation",
        "category": "Variation",
        "source": "Lindberg et al. (2016)",
        "description": "Activity-type transitions (changes) / total events.",
    },
    {
        "key": "activity_var",
        "label": "activity-var",
        "name": "Activity Variation",
        "category": "Variation",
        "source": "Lindberg et al. (2016)",
        "description": "Shannon entropy of the activity-occurrence shares.",
    },
    # Distance
    {
        "key": "affinity",
        "label": "affinity",
        "name": "Average Affinity",
        "category": "Distance",
        "source": "Günther (2009)",
        "description": "Weighted Jaccard similarity of variants' DF patterns.",
    },
    {
        "key": "structure",
        "label": "structure",
        "name": "Structure",
        "category": "Distance",
        "source": "Günther (2009)",
        "description": "1 - |DF edges| / vertices^2.",
    },
    {
        "key": "dev_random",
        "label": "dev-random",
        "name": "Deviation from Random",
        "category": "Distance",
        "source": "Pentland (2003)",
        "description": "1 - ||transition matrix - uniform|| (normalised).",
    },
    {
        "key": "avg_edit_distance",
        "label": "avg-edit-distance",
        "name": "Average Edit Distance",
        "category": "Distance",
        "source": "Pentland (2003)",
        "description": "Mean pairwise Levenshtein distance between traces.",
    },
    {
        "key": "structural_var",
        "label": "structural-var",
        "name": "Structural Process Variety",
        "category": "Distance",
        "source": "Schreiber & Abbad-Andaloussi (2024)",
        "description": "Σ merge heights of agglomerative clustering on the Levenshtein matrix.",
    },
]

CATEGORY_ORDER: list[str] = ["Entropy", "Enriched Entropy", "Size", "Variation", "Distance"]


# ── Shared single-pass preparation ────────────────────────────────────────────
#
# Every sequence-derived metric used to re-sort the full log and re-group it
# with a per-case Python loop (7 sorts + 5 groupby loops per compute_all run).
# `_Prep` does it once: one stable timestamp sort, integer-coded activities
# and cases, and a case-contiguous view whose within-case order equals the
# global timestamp order - exactly what `sort_values("timestamp",
# kind="mergesort").groupby("case_id", sort=False)` produced before.


class _Prep:
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
    )

    def __init__(self, df: pd.DataFrame) -> None:
        work = df.sort_values("timestamp", kind="mergesort")  # NaT sorts last

        acts = work["activity"].astype(str).to_numpy()
        self.act_labels, stream_act = np.unique(acts, return_inverse=True)
        self.stream_act = stream_act.astype(np.int64, copy=False)
        self.stream_case = pd.factorize(work["case_id"].to_numpy())[0]

        ts = pd.to_datetime(work["timestamp"], errors="coerce", utc=True)
        ts_i8 = ts.to_numpy(dtype="datetime64[ns]").view("int64")

        self.n_events = len(work)
        self.n_cases = int(self.stream_case.max()) + 1 if self.n_events else 0

        # Case-contiguous view, within-case order = global timestamp order.
        order = np.argsort(self.stream_case, kind="stable")
        self.grp_act = self.stream_act[order]
        self.grp_case = self.stream_case[order]
        self.grp_ts_i8 = ts_i8[order]
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

    def label_seq(self, seq: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(str(self.act_labels[c]) for c in seq)


def _get_prep(df: pd.DataFrame, prep: _Prep | None = None) -> _Prep:
    return prep if prep is not None else _Prep(df)


# ── EPA construction (faithful to the thesis reference implementation) ─────────


def build_epa(
    df: pd.DataFrame,
    *,
    key_fn: Any = None,
    prep: _Prep | None = None,
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Build the Extended Prefix Automaton in global timestamp order.

    ``key_fn`` decides what makes two events follow the *same* successor edge.
    Default is the activity label; the enriched variant also keys on the
    selected event/trace attributes. The default path runs over pre-coded
    integer arrays (one shared sort) instead of ``itertuples``.
    """
    if key_fn is None:
        p = _get_prep(df, prep)
        return _build_epa_coded(p.stream_case, p.stream_act, p.act_labels)

    df_sorted = df.sort_values("timestamp", kind="mergesort")

    states: dict[int, State] = {
        0: {"c": 0, "j": 0, "children": {}, "n_events": 0, "activity": None}
    }
    last_state: dict[Any, int] = {}
    c_counter = 1
    next_id = 1

    for row in df_sorted.itertuples(index=False):
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
                "n_events": 0,
                "activity": activity,
            }
            pred["children"][edge_key] = next_id
            curr_id = next_id
            next_id += 1

        states[curr_id]["n_events"] += 1
        last_state[case_id] = curr_id

    return states, _c_index_of(states, next_id)


def _build_epa_coded(
    case_codes: np.ndarray, act_codes: np.ndarray, act_labels: np.ndarray
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Array fast path of :func:`build_epa` - identical automaton, int edge keys."""
    states: dict[int, State] = {
        0: {"c": 0, "j": 0, "children": {}, "n_events": 0, "activity": None}
    }
    last_state: dict[int, int] = {}
    c_counter = 1
    next_id = 1

    for case_id, act in zip(case_codes.tolist(), act_codes.tolist(), strict=True):
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
                "n_events": 0,
                "activity": str(act_labels[act]),
            }
            children[act] = next_id
            curr_id = next_id
            next_id += 1

        states[curr_id]["n_events"] += 1
        last_state[case_id] = curr_id

    return states, _c_index_of(states, next_id)


def _c_index_of(states: dict[int, State], next_id: int) -> dict[int, list[int]]:
    c_index: dict[int, list[int]] = {}
    for sid in range(1, next_id):
        c_index.setdefault(states[sid]["c"], []).append(sid)
    return c_index


def _boltzmann(total: float, partition_sizes: list[float]) -> tuple[float, float]:
    """``H = log(N)*N - sum(log(e_i)*e_i)`` and its normaliser ``log(N)*N``."""
    if total <= 0:
        return 0.0, 0.0
    base = math.log(total) * total
    h = base
    for e in partition_sizes:
        if e > 0:
            h -= math.log(e) * e
    if base == 0:
        return 0.0, 0.0
    return h, h / base


def variant_entropy(states: dict[int, State], c_index: dict[int, list[int]]) -> tuple[float, float]:
    n_nodes = len(states) - 1
    if n_nodes <= 0:
        return 0.0, 0.0
    partition_sizes = [float(len(ids)) for ids in c_index.values()]
    return _boltzmann(float(n_nodes), partition_sizes)


def sequence_entropy(
    states: dict[int, State], c_index: dict[int, list[int]]
) -> tuple[float, float]:
    total = float(sum(states[sid]["n_events"] for sid in range(1, len(states))))
    if total <= 0:
        return 0.0, 0.0
    partition_sizes = [
        float(sum(states[sid]["n_events"] for sid in ids)) for ids in c_index.values()
    ]
    return _boltzmann(total, partition_sizes)


def n_ties(states: dict[int, State]) -> int:
    """Number of ties: Σ root→end-node path lengths over all variants.

    Each EPA leaf is the end of one variant; its depth ``j`` equals the
    variant's length, so summing leaf depths counts every tie traversed.
    """
    return sum(s["j"] for sid, s in states.items() if sid != 0 and len(s["children"]) == 0)


# ── Lempel-Ziv complexity ─────────────────────────────────────────────────────


def _lz76_phrases(codes: Sequence[int]) -> int:
    """LZ76 phrase count via an incremental trie - O(n) total instead of the
    O(n·k) tuple-slice hashing of the previous ``seq[i:i+k] in seen`` scan.

    Identical parse: every phrase extends an already-seen phrase by one
    symbol, so the seen-set is exactly a trie; walking it consumes each
    symbol once.
    """
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


# ── Variant / DF-pattern helpers ──────────────────────────────────────────────


def variant_counts(df: pd.DataFrame, *, prep: _Prep | None = None) -> dict[tuple[str, ...], int]:
    p = _get_prep(df, prep)
    out: dict[tuple[str, ...], int] = {}
    for seq, c in p.counts.items():
        out[p.label_seq(seq)] = c
    return out


def _df_patterns(
    counts: dict[tuple[Hashable, ...], int],
) -> dict[tuple[Hashable, ...], set[tuple[Hashable, Hashable]]]:
    return {acts: set(pairwise(acts)) for acts in counts}


def _df_edges_and_vertices(
    df: pd.DataFrame,
    counts: dict[tuple[Hashable, ...], int] | None = None,
    *,
    prep: _Prep | None = None,
) -> tuple[int, int]:
    """Distinct directly-follows edges (e) and distinct activities (v)."""
    p = _get_prep(df, prep)
    v = len(p.act_labels)
    src, dst, _ = p.transitions()
    if src.size == 0:
        return 0, v
    e = int(np.unique(src * np.int64(max(v, 1)) + dst).size)
    return e, v


# ── Distance - affinity / structure / deviation from random ───────────────────

# Exact affinity only depends on each variant's directly-follows pattern
# *set*, so variants with identical patterns collapse into one group and the
# pairwise Jaccard sum becomes a blocked float32 matmul over the groupxedge
# incidence matrix. Beyond the caps below the least-frequent pattern groups
# are dropped and the metric is computed over the retained trace population -
# a documented approximation for logs where the exact U² sum is intractable.
_AFFINITY_MAX_PATTERNS = 4000
# Bound on the dense groupxedge incidence matrix (float32 cells ≈ 200 MB).
_AFFINITY_MAX_CELLS = 50_000_000
_AFFINITY_BLOCK_ROWS = 1024


def affinity(counts: dict[tuple[Hashable, ...], int]) -> float | None:
    """Average affinity (Günther 2009): mean over ordered case pairs of the
    Jaccard similarity of their DF pattern sets.

    Vectorised but numerically identical to the naive O(V²) double loop:
    ``m = Σ_{g,h} J(g,h)·C_g·C_h  (nonempty pattern groups)
         + Σ_{empty-pattern variants} c²  -  N``
    and ``affinity = m / (N·(N-1))`` - same-variant pairs contribute
    ``c·(c-1)`` and empty-vs-empty cross-variant pairs contribute 0, exactly
    as before.
    """
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

    # Deterministic order: by weight desc, then pattern size (tie-break only).
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


def structure(e: int, v: int) -> float | None:
    if v == 0:
        return None
    return 1.0 - e / (v * v)


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


# ── Variation - order / activity variation ────────────────────────────────────


def order_variation(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    """Activity-type transitions (consecutive events whose type changes)
    divided by the total number of events (Lindberg et al., 2016)."""
    p = _get_prep(df, prep)
    if p.n_events == 0:
        return None
    src, dst, _ = p.transitions()
    changes = int(np.count_nonzero(src != dst))
    return changes / p.n_events


def activity_variation(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    """Shannon entropy (natural log) over the activity-occurrence shares
    (Lindberg et al., 2016)."""
    p = _get_prep(df, prep)
    if p.n_events == 0:
        return None
    freq = np.bincount(p.stream_act, minlength=len(p.act_labels)).astype(np.float64)
    probs = freq[freq > 0] / p.n_events
    return float(-np.sum(probs * np.log(probs)))


# ── Size - sequence-length stats & avg time diff ──────────────────────────────


def seq_len_stats(df: pd.DataFrame, *, prep: _Prep | None = None) -> dict[str, float]:
    p = _get_prep(df, prep)
    lengths = np.diff(p.bounds)
    if lengths.size == 0:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {
        "min": float(lengths.min()),
        "avg": float(lengths.mean()),
        "max": float(lengths.max()),
    }


_I8_NAT = np.int64(np.datetime64("NaT", "ns").view("int64"))


def avg_time_diff(df: pd.DataFrame, *, prep: _Prep | None = None) -> float | None:
    """Mean (over traces) of each trace's mean inter-event gap, in seconds.

    The "average" counterpart of Günther's time granularity, which uses the
    per-trace *minimum* gap (thesis §2.4.3). NaT timestamps are skipped
    instead of poisoning the mean."""
    p = _get_prep(df, prep)
    _, _, idx = p.transitions()
    if idx.size == 0:
        return None
    cur = p.grp_ts_i8[idx]
    prev = p.grp_ts_i8[idx - 1]
    valid = (cur != _I8_NAT) & (prev != _I8_NAT)
    if not np.any(valid):
        return None
    secs = (cur[valid] - prev[valid]).astype(np.float64) / 1e9
    cases = p.grp_case[idx][valid]
    sums = np.bincount(cases, weights=secs, minlength=p.n_cases)
    cnts = np.bincount(cases, minlength=p.n_cases)
    per_case_mean = sums[cnts > 0] / cnts[cnts > 0]
    return float(per_case_mean.mean()) if per_case_mean.size else None


# ── Distance - Levenshtein matrix → avg edit distance & structural variety ────


def _levenshtein(a: tuple[Hashable, ...], b: tuple[Hashable, ...]) -> int:
    """Reference dynamic-programming Levenshtein. Kept as the correctness
    oracle for :func:`_myers_distance`; not used on the hot path."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    prev = list(range(la + 1))
    for j in range(1, lb + 1):
        cur = [j] + [0] * la
        bj = b[j - 1]
        for i in range(1, la + 1):
            cost = 0 if a[i - 1] == bj else 1
            cur[i] = min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + cost)
        prev = cur
    return prev[la]


def _myers_masks(seq: Sequence[Hashable]) -> dict[Hashable, int]:
    """Per-symbol position bitmasks for ``seq`` (Myers' PEq table)."""
    pm: dict[Hashable, int] = {}
    bit = 1
    for c in seq:
        pm[c] = pm.get(c, 0) | bit
        bit <<= 1
    return pm


def _myers_distance(pm: dict[Hashable, int], m: int, text: Sequence[Hashable]) -> int:
    """Levenshtein distance via Myers' bit-parallel algorithm (1999).

    ``pm``/``m`` describe the pattern (precomputed via :func:`_myers_masks`);
    each text symbol costs O(⌈m/64⌉) bigint words instead of a full Python DP
    row - ~two orders of magnitude faster for long traces. Verified against
    :func:`_levenshtein` in the module tests.
    """
    if m == 0:
        return len(text)
    mask = (1 << m) - 1
    top = 1 << (m - 1)
    vp = mask
    vn = 0
    score = m
    for c in text:
        eq = pm.get(c, 0)
        xv = eq | vn
        xh = (((eq & vp) + vp) ^ vp) | eq
        ph = vn | (~(xh | vp) & mask)
        mh = vp & xh
        if ph & top:
            score += 1
        elif mh & top:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        vp = (mh | (~(xv | ph) & mask)) & mask
        vn = ph & xv
    return score


# Even bit-parallel Levenshtein is O(pairs · len · ⌈len/64⌉) word-ops; the
# selection shrinks (most-frequent variants kept) until the estimate fits.
_LEV_WORD_OP_BUDGET = 300_000_000


def _lev_word_ops(lens: list[int]) -> float:
    """Estimated Myers word-ops for the full pairwise matrix over ``lens``."""
    if len(lens) < 2:
        return 0.0
    arr = np.asarray(lens, dtype=np.float64)
    total = float(arr.sum())
    return (total * total - float(np.sum(arr * arr))) / 2.0 / 64.0


def _select_variants(
    counts: dict[tuple[Hashable, ...], int], max_variants: int
) -> tuple[list[tuple[Hashable, ...]], list[int], bool]:
    """Top-``max_variants`` variants by case frequency. Returns (variants,
    counts, downsampled?). Levenshtein over distinct variants is far cheaper
    than over all traces while capturing the dominant behaviour; the thesis
    likewise downsamples large logs for its distance metrics. A secondary,
    documented word-op budget guards logs with extremely long traces."""
    # Stable sort by frequency only - count ties keep their first-appearance
    # order, matching the historical selection exactly.
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    downsampled = len(ordered) > max_variants
    ordered = ordered[:max_variants]

    lens = [len(v) for v, _ in ordered]
    while len(ordered) > 2 and _lev_word_ops(lens) > _LEV_WORD_OP_BUDGET:
        keep = min(len(ordered) - 1, max(2, int(len(ordered) * 0.8)))
        ordered = ordered[:keep]
        lens = lens[:keep]
        downsampled = True

    variants = [v for v, _ in ordered]
    weights = [c for _, c in ordered]
    return variants, weights, downsampled


def _levenshtein_matrix(variants: list[tuple[Hashable, ...]]) -> np.ndarray:
    u = len(variants)
    d = np.zeros((u, u), dtype=float)
    if u < 2:
        return d
    # Recode symbols as small ints (cheaper dict hits) and precompute each
    # variant's Myers mask table once; each pair is then O(shorter side).
    vocab: dict[Hashable, int] = {}
    coded: list[tuple[int, ...]] = []
    for v in variants:
        coded.append(tuple(vocab.setdefault(a, len(vocab)) for a in v))
    masks = [_myers_masks(s) for s in coded]
    lens = [len(s) for s in coded]

    for i in range(u):
        si, mi, pmi = coded[i], lens[i], masks[i]
        for j in range(i + 1, u):
            sj, mj = coded[j], lens[j]
            if si == sj:
                continue
            if mi >= mj:
                dist = float(_myers_distance(pmi, mi, sj))
            else:
                dist = float(_myers_distance(masks[j], mj, si))
            d[i, j] = dist
            d[j, i] = dist
    return d


def avg_edit_distance_from_matrix(d: np.ndarray, weights: list[int]) -> float | None:
    """Mean pairwise Levenshtein over the (weighted) trace population the
    selected variants stand for. Same-variant pairs contribute distance 0."""
    n = int(sum(weights))
    if n < 2:
        return None
    w = np.asarray(weights, dtype=float)
    # Σ_{i<j} d_ij · w_i · w_j  (cross-variant; same-variant distance is 0).
    cross = float(np.sum(np.triu(d, k=1) * np.outer(w, w)))
    total_pairs = n * (n - 1) / 2.0
    return cross / total_pairs if total_pairs else None


def structural_variety_from_matrix(d: np.ndarray) -> float | None:
    """Σ of the cluster-merge heights from agglomerative (average-linkage)
    hierarchical clustering on the Levenshtein distance matrix
    (Schreiber & Abbad-Andaloussi, 2024)."""
    u = d.shape[0]
    if u < 2:
        return None
    from scipy.cluster.hierarchy import linkage
    from scipy.spatial.distance import squareform

    condensed = squareform(d, checks=False)
    z = linkage(condensed, method="average")
    return float(np.sum(z[:, 2]))


# ── prob-act-pairs - transition probability matrix (Grisold et al., 2022) ─────


def transition_probability_matrix(
    df: pd.DataFrame, top_k: int = 25, *, prep: _Prep | None = None
) -> dict[str, Any]:
    """Row-stochastic direct-follows transition matrix restricted to the
    ``top_k`` most frequent activities (for the panel heatmap)."""
    p = _get_prep(df, prep)
    if p.n_events == 0:
        return {"activities": [], "matrix": [], "truncated": False}

    freq = np.bincount(p.stream_act, minlength=len(p.act_labels))
    order = np.argsort(-freq, kind="stable")[: min(top_k, len(p.act_labels))]
    activities = [str(p.act_labels[i]) for i in order]
    truncated = len(p.act_labels) > len(activities)

    k = len(activities)
    remap = np.full(len(p.act_labels), -1, dtype=np.int64)
    remap[order] = np.arange(k)

    src, dst, _ = p.transitions()
    counts = np.zeros((k, k), dtype=float)
    if src.size:
        s = remap[src]
        t = remap[dst]
        keep = (s >= 0) & (t >= 0)
        if np.any(keep):
            flat = np.bincount(s[keep] * k + t[keep], minlength=k * k)
            counts = flat.reshape(k, k).astype(float)

    row_sums = counts.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        probs = np.divide(counts, row_sums, out=np.zeros_like(counts), where=row_sums > 0)
    return {
        "activities": activities,
        "matrix": [[round(float(x), 4) for x in row] for row in probs],
        "truncated": truncated,
    }


# ── Enriched EPA support (IEEE-XES attribute keying) ──────────────────────────

REQUIRED_TRACE_ATTRS: frozenset[str] = frozenset(
    {"variant", "concept:name", "creator", "variant-index"}
)
REQUIRED_EVENT_ATTRS: frozenset[str] = frozenset(
    {
        "time:timestamp",
        "Resource",
        "lifecycle:transition",
        "concept:name",
        "Activity",
        "org:resource",
    }
)
_CANONICAL_TO_XES = {
    "case_id": "concept:name",
    "activity": "concept:name",
    "timestamp": "time:timestamp",
    "resource": "org:resource",
    "lifecycle": "lifecycle:transition",
}


def is_enriched_supported(detected_schema: dict[str, Any] | None) -> bool:
    if not detected_schema:
        return False
    trace = set(detected_schema.get("trace_attributes") or [])
    event = set(detected_schema.get("event_attributes") or [])
    return REQUIRED_TRACE_ATTRS.issubset(trace) and REQUIRED_EVENT_ATTRS.issubset(event)


def _attr_to_field(attr: str) -> str:
    """itertuples replaces non-identifier chars with underscores."""
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in attr)
    if out and out[0].isdigit():
        out = "_" + out
    return out


def _to_hashable(v: Any) -> Hashable:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return str(v)
    except Exception:
        return repr(v)


def _attr_columns(df: pd.DataFrame, required: set[str]) -> list[str]:
    cols = set(df.columns)
    resolved: list[str] = []
    for xes_key in sorted(required):
        if xes_key in cols:
            resolved.append(xes_key)
            continue
        field_name = _attr_to_field(xes_key)
        if field_name != xes_key and field_name in cols:
            resolved.append(field_name)
            continue
        for canonical, xes in _CANONICAL_TO_XES.items():
            if xes == xes_key and canonical in cols:
                resolved.append(canonical)
                break
    return resolved


def _build_enriched_key_fn(df: pd.DataFrame) -> Any:
    event_cols = _attr_columns(df, set(REQUIRED_EVENT_ATTRS))
    trace_cols = _attr_columns(df, set(REQUIRED_TRACE_ATTRS))
    skip = {"case_id", "activity", "timestamp"}
    attr_cols = sorted({c for c in (event_cols + trace_cols) if c not in skip})

    if not attr_cols:

        def key_only_activity(row: Any) -> Hashable:
            return row.activity

        return key_only_activity

    field_names = ["activity", *attr_cols]

    def key_with_attrs(row: Any) -> Hashable:
        return tuple(
            (name, _to_hashable(getattr(row, _attr_to_field(name), None))) for name in field_names
        )

    return key_with_attrs


def _enriched_entropies(df: pd.DataFrame) -> dict[str, float]:
    df_renamed = df.rename(
        columns={c: _attr_to_field(c) for c in df.columns if c != _attr_to_field(c)}
    )
    key_fn = _build_enriched_key_fn(df_renamed)
    states, c_index = build_epa(df_renamed, key_fn=key_fn)
    h_var, h_var_norm = variant_entropy(states, c_index)
    h_seq, h_seq_norm = sequence_entropy(states, c_index)
    return {
        "en_var_e": h_var,
        "en_seq_e": h_seq,
        "en_nvar_e": h_var_norm,
        "en_nseq_e": h_seq_norm,
    }


# ── Sanitiser - JSON has no inf / nan ─────────────────────────────────────────


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ── Public entry point ────────────────────────────────────────────────────────


def compute_all(
    df: pd.DataFrame,
    *,
    detected_schema: dict[str, Any] | None = None,
    max_variants: int = 300,
) -> dict[str, Any]:
    """Compute the full Table 3.3 metric suite for one (case-centric) log.

    Returns a flat ``key -> value`` mapping (the keys in :data:`METRIC_DEFS`)
    plus run metadata (``enriched_supported``, ``downsampled`` ...).
    """
    if df.empty or df["case_id"].nunique() == 0:
        return {"empty": True}

    prep = _Prep(df)
    counts = prep.counts
    n_events = prep.n_events
    n_cases = prep.n_cases
    n_variants = len(counts)
    e_edges, v_vertices = _df_edges_and_vertices(df, counts, prep=prep)

    # Entropy (plain EPA).
    states, c_index = build_epa(df, prep=prep)
    var_e, nvar_e = variant_entropy(states, c_index)
    seq_e, nseq_e = sequence_entropy(states, c_index)

    # Enriched entropy (when the XES attribute set is present).
    enriched_supported = is_enriched_supported(detected_schema)
    enriched = (
        _enriched_entropies(df)
        if enriched_supported
        else {
            "en_var_e": None,
            "en_seq_e": None,
            "en_nvar_e": None,
            "en_nseq_e": None,
        }
    )

    # Size.
    lens = seq_len_stats(df, prep=prep)

    # Variation - number of acyclic paths (guard the exponential overflow the
    # thesis itself notes for large logs; keep the log10 for display).
    exp10 = 0.08 * (1 + e_edges - v_vertices)
    try:
        n_acyclic = 10.0**exp10
        if not math.isfinite(n_acyclic):
            n_acyclic = None
    except OverflowError:
        n_acyclic = None

    # Distance - one Levenshtein matrix feeds both avg-edit-distance and
    # structural-var. Variants stay integer-coded (cheaper hashing).
    variants, weights, downsampled = _select_variants(counts, max_variants)
    d_matrix = _levenshtein_matrix(variants)
    avg_edit = avg_edit_distance_from_matrix(d_matrix, weights)
    struct_var = structural_variety_from_matrix(d_matrix)

    avg_distinct = float(
        np.mean([np.unique(prep.grp_act[s:e]).size for s, e in pairwise(prep.bounds)])
    )

    values: dict[str, Any] = {
        # Entropy
        "var_e": var_e,
        "seq_e": seq_e,
        "nvar_e": nvar_e,
        "nseq_e": nseq_e,
        # Enriched entropy
        **enriched,
        # Size
        "n_events": n_events,
        "n_event_types": v_vertices,
        "n_sequences": n_cases,
        "min_seq_len": lens["min"],
        "avg_seq_len": lens["avg"],
        "max_seq_len": lens["max"],
        "avg_td_e": avg_time_diff(df, prep=prep),
        # Variation
        "n_acyclic_paths": n_acyclic,
        "n_acyclic_paths_log10": exp10,
        "n_ties": n_ties(states),
        "lempel_ziv": lempel_ziv_complexity(df, prep=prep),
        "n_unique_seq": n_variants,
        "perc_unique_seq": (n_variants / n_cases) * 100.0 if n_cases else None,
        "avg_distinct_e": avg_distinct,
        "order_var": order_variation(df, prep=prep),
        "activity_var": activity_variation(df, prep=prep),
        # Distance
        "affinity": affinity(counts),
        "structure": structure(e_edges, v_vertices),
        "dev_random": deviation_from_random(df, prep=prep),
        "avg_edit_distance": avg_edit,
        "structural_var": struct_var,
        # Metadata
        "df_edges": e_edges,
        "enriched_supported": enriched_supported,
        "downsampled": downsampled,
        "distance_variants_used": len(variants),
        "max_variants": max_variants,
    }
    return {k: _finite(v) for k, v in values.items()}
