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

Everything is pure-function and self-contained: the module never imports from
``apps/*`` or a sibling module.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Hashable
from statistics import mean
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


# ── EPA construction (faithful to the thesis reference implementation) ─────────


def build_epa(
    df: pd.DataFrame,
    *,
    key_fn: Any = None,
) -> tuple[dict[int, State], dict[int, list[int]]]:
    """Build the Extended Prefix Automaton in global timestamp order.

    ``key_fn`` decides what makes two events follow the *same* successor edge.
    Default is the activity label; the enriched variant also keys on the
    selected event/trace attributes.
    """
    if key_fn is None:

        def key_fn(row: Any) -> Hashable:
            return row.activity

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

    c_index: dict[int, list[int]] = {}
    for sid in range(1, next_id):
        c = states[sid]["c"]
        c_index.setdefault(c, []).append(sid)

    return states, c_index


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


# ── Variant / DF-pattern helpers ──────────────────────────────────────────────


def variant_counts(df: pd.DataFrame) -> dict[tuple[str, ...], int]:
    counts: dict[tuple[str, ...], int] = {}
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        acts = tuple(str(a) for a in group["activity"].tolist())
        counts[acts] = counts.get(acts, 0) + 1
    return counts


def _df_patterns(counts: dict[tuple[str, ...], int]) -> dict[tuple[str, ...], set[tuple[str, str]]]:
    return {acts: {(acts[i - 1], acts[i]) for i in range(1, len(acts))} for acts in counts}


def _df_edges_and_vertices(df: pd.DataFrame, counts: dict[tuple[str, ...], int]) -> tuple[int, int]:
    """Distinct directly-follows edges (e) and distinct activities (v)."""
    v = int(df["activity"].nunique())
    edges: set[tuple[str, str]] = set()
    for pat in _df_patterns(counts).values():
        edges |= pat
    return len(edges), v


# ── Distance - affinity / structure / deviation from random ───────────────────


def affinity(counts: dict[tuple[str, ...], int]) -> float | None:
    total_cases = sum(counts.values())
    if total_cases < 2:
        return None
    patterns = _df_patterns(counts)
    variants = list(counts.keys())

    m_affinity = 0.0
    for i, v1 in enumerate(variants):
        for j, v2 in enumerate(variants):
            if i != j:
                overlap = len(patterns[v1] & patterns[v2])
                union = len(patterns[v1] | patterns[v2])
                if union > 0:
                    m_affinity += (overlap / union) * counts[v1] * counts[v2]
            else:
                c = counts[v1]
                m_affinity += c * (c - 1)

    denom = total_cases * (total_cases - 1)
    return m_affinity / denom if denom else None


def structure(e: int, v: int) -> float | None:
    if v == 0:
        return None
    return 1.0 - e / (v * v)


def deviation_from_random(df: pd.DataFrame) -> float | None:
    activities = df["activity"].astype(str).unique().tolist()
    v = len(activities)
    if v == 0:
        return None
    idx = {a: i for i, a in enumerate(activities)}
    net = [[0] * v for _ in range(v)]
    n_trans = 0
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        acts = [str(a) for a in group["activity"].tolist()]
        for i in range(1, len(acts)):
            net[idx[acts[i - 1]]][idx[acts[i]]] += 1
            n_trans += 1
    if n_trans == 0:
        return None
    a_mean = n_trans / (v * v)
    dev = math.sqrt(sum(((c - a_mean) / n_trans) ** 2 for row in net for c in row))
    return 1.0 - dev


# ── Variation - order / activity variation ────────────────────────────────────


def order_variation(df: pd.DataFrame) -> float | None:
    """Activity-type transitions (consecutive events whose type changes)
    divided by the total number of events (Lindberg et al., 2016)."""
    total_events = len(df)
    if total_events == 0:
        return None
    changes = 0
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        acts = [str(a) for a in group["activity"].tolist()]
        for i in range(1, len(acts)):
            if acts[i] != acts[i - 1]:
                changes += 1
    return changes / total_events


def activity_variation(df: pd.DataFrame) -> float | None:
    """Shannon entropy (natural log) over the activity-occurrence shares
    (Lindberg et al., 2016)."""
    total = len(df)
    if total == 0:
        return None
    counts = Counter(str(a) for a in df["activity"].tolist())
    h = 0.0
    for n in counts.values():
        p = n / total
        if p > 0:
            h -= p * math.log(p)
    return h


# ── Size - sequence-length stats & avg time diff ──────────────────────────────


def seq_len_stats(df: pd.DataFrame) -> dict[str, float]:
    lengths = df.groupby("case_id").size().tolist()
    if not lengths:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    return {"min": float(min(lengths)), "avg": float(mean(lengths)), "max": float(max(lengths))}


def avg_time_diff(df: pd.DataFrame) -> float | None:
    """Mean (over traces) of each trace's mean inter-event gap, in seconds.

    The "average" counterpart of Günther's time granularity, which uses the
    per-trace *minimum* gap (thesis §2.4.3)."""
    per_case_mean: list[float] = []
    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce", utc=True)
    for _, group in work.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        diffs = group["timestamp"].diff().dropna().dt.total_seconds()
        if not diffs.empty:
            per_case_mean.append(float(diffs.mean()))
    return float(mean(per_case_mean)) if per_case_mean else None


# ── Distance - Levenshtein matrix → avg edit distance & structural variety ────


def _levenshtein(a: tuple[str, ...], b: tuple[str, ...]) -> int:
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


def _select_variants(
    counts: dict[tuple[str, ...], int], max_variants: int
) -> tuple[list[tuple[str, ...]], list[int], bool]:
    """Top-``max_variants`` variants by case frequency. Returns (variants,
    counts, downsampled?). Levenshtein over distinct variants is far cheaper
    than over all traces while capturing the dominant behaviour; the thesis
    likewise downsamples large logs for its distance metrics."""
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    downsampled = len(ordered) > max_variants
    ordered = ordered[:max_variants]
    variants = [v for v, _ in ordered]
    weights = [c for _, c in ordered]
    return variants, weights, downsampled


def _levenshtein_matrix(variants: list[tuple[str, ...]]) -> np.ndarray:
    u = len(variants)
    d = np.zeros((u, u), dtype=float)
    for i in range(u):
        for j in range(i + 1, u):
            dist = float(_levenshtein(variants[i], variants[j]))
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


def transition_probability_matrix(df: pd.DataFrame, top_k: int = 25) -> dict[str, Any]:
    """Row-stochastic direct-follows transition matrix restricted to the
    ``top_k`` most frequent activities (for the panel heatmap)."""
    freq = Counter(str(a) for a in df["activity"].tolist())
    if not freq:
        return {"activities": [], "matrix": [], "truncated": False}
    activities = [a for a, _ in freq.most_common(top_k)]
    truncated = len(freq) > len(activities)
    idx = {a: i for i, a in enumerate(activities)}
    k = len(activities)
    counts = np.zeros((k, k), dtype=float)
    for _, group in df.sort_values("timestamp", kind="mergesort").groupby("case_id", sort=False):
        acts = [str(a) for a in group["activity"].tolist()]
        for i in range(1, len(acts)):
            a, b = acts[i - 1], acts[i]
            if a in idx and b in idx:
                counts[idx[a], idx[b]] += 1
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

    counts = variant_counts(df)
    n_events = len(df)
    n_cases = int(df["case_id"].nunique())
    n_variants = len(counts)
    e_edges, v_vertices = _df_edges_and_vertices(df, counts)

    # Entropy (plain EPA).
    states, c_index = build_epa(df)
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
    lens = seq_len_stats(df)

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
    # structural-var.
    variants, weights, downsampled = _select_variants(counts, max_variants)
    d_matrix = _levenshtein_matrix(variants)
    avg_edit = avg_edit_distance_from_matrix(d_matrix, weights)
    struct_var = structural_variety_from_matrix(d_matrix)

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
        "avg_td_e": avg_time_diff(df),
        # Variation
        "n_acyclic_paths": n_acyclic,
        "n_acyclic_paths_log10": exp10,
        "n_ties": n_ties(states),
        "lempel_ziv": lempel_ziv_complexity(df),
        "n_unique_seq": n_variants,
        "perc_unique_seq": (n_variants / n_cases) * 100.0 if n_cases else None,
        "avg_distinct_e": float(df.groupby("case_id")["activity"].nunique().mean()),
        "order_var": order_variation(df),
        "activity_var": activity_variation(df),
        # Distance
        "affinity": affinity(counts),
        "structure": structure(e_edges, v_vertices),
        "dev_random": deviation_from_random(df),
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
