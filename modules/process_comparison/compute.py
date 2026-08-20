"""Pure comparison primitives - no ModuleContext, so they're unit-testable.

Everything here takes plain pandas DataFrames with the canonical platform
columns (``case_id`` / ``activity`` / ``timestamp``) and returns plain Python
structures. The route layer in ``module.py`` wires these to the event log and
caches the JSON.
"""

from __future__ import annotations

from typing import Any

# Cap the stochastic language fed to EMD: compute_emd is ~O(n·m) edit-distance
# pairs over variants, so a pathological log (thousands of unique variants)
# would dominate the request. Top-K by frequency keeps it bounded and the
# result barely moves - the long tail carries little probability mass.
_EMD_VARIANT_CAP = 100


def rename_pm4py(df: Any) -> Any:
    """Map the platform's canonical columns to pm4py's ``concept:name`` triad."""
    return df.rename(
        columns={
            "case_id": "case:concept:name",
            "activity": "concept:name",
            "timestamp": "time:timestamp",
        }
    )


def variant_counts(df: Any) -> dict[tuple[str, ...], int]:
    """Trace variant -> case count.

    Computed directly from the frame (sort by case + timestamp, collect each
    case's activity sequence) rather than via ``pm4py.get_variants`` whose
    return shape drifts across versions - this is deterministic and version-proof.
    """
    sorted_df = df.sort_values(["case_id", "timestamp"], kind="mergesort")
    counts: dict[tuple[str, ...], int] = {}
    for _, group in sorted_df.groupby("case_id", sort=False):
        variant = tuple(str(a) for a in group["activity"].tolist())
        counts[variant] = counts.get(variant, 0) + 1
    return counts


def activity_frequencies(df: Any) -> dict[str, int]:
    """Activity -> event count."""
    vc = df["activity"].astype(str).value_counts()
    return {str(k): int(v) for k, v in vc.items()}


def activity_mean_sojourn(df: Any) -> dict[str, float]:
    """Activity -> mean sojourn time in seconds (inter-event delay to the next
    event in the same case). Mirrors the performance module's sojourn logic.
    The last event of each case has no successor and is skipped.
    """
    sorted_df = df.sort_values(["case_id", "timestamp"], kind="mergesort")
    next_ts = sorted_df.groupby("case_id", sort=False)["timestamp"].shift(-1)
    sojourn = (next_ts - sorted_df["timestamp"]).dt.total_seconds()
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for activity, secs in zip(sorted_df["activity"].tolist(), sojourn.tolist(), strict=False):
        if secs is None or secs != secs or secs < 0:
            continue
        key = str(activity)
        sums[key] = sums.get(key, 0.0) + float(secs)
        counts[key] = counts.get(key, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}


def discover_dfg(df: Any) -> tuple[dict[tuple[str, str], int], dict[str, int], dict[str, int]]:
    """Directly-follows graph: (edge->freq, start_acts, end_acts)."""
    import pm4py

    renamed = rename_pm4py(df)
    dfg, start, end = pm4py.discover_dfg(renamed)
    edges = {(str(s), str(t)): int(f) for (s, t), f in dfg.items()}
    return (
        edges,
        {str(k): int(v) for k, v in start.items()},
        {str(k): int(v) for k, v in end.items()},
    )


def footprint_relations(df: Any) -> set[tuple[str, str]]:
    """Behavioural footprint as a set of labelled relations.

    Combines the directly-follows, parallel, start and end relations from
    ``pm4py.discover_footprints`` into one tagged set so a Jaccard over two
    logs' sets is a single behavioural-similarity number (1 = identical
    behaviour, 0 = disjoint).
    """
    import pm4py

    fp = pm4py.discover_footprints(rename_pm4py(df))
    rels: set[tuple[str, str]] = set()
    for s, t in fp.get("dfg", set()) or set():
        rels.add((f"df:{s}", str(t)))
    for pair in fp.get("parallel", set()) or set():
        a, b = sorted(str(x) for x in pair)
        rels.add((f"par:{a}", b))
    for a in fp.get("start_activities", set()) or set():
        rels.add(("start", str(a)))
    for a in fp.get("end_activities", set()) or set():
        rels.add(("end", str(a)))
    return rels


def _jaccard(a: set[Any], b: set[Any]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def stochastic_language(counts: dict[tuple[str, ...], int]) -> dict[tuple[str, ...], float]:
    """Variant counts -> probability distribution, capped to the top variants."""
    if len(counts) > _EMD_VARIANT_CAP:
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:_EMD_VARIANT_CAP]
        counts = dict(top)
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def emd_distance(
    counts_a: dict[tuple[str, ...], int], counts_b: dict[tuple[str, ...], int]
) -> float:
    """Earth Mover's Distance between two logs' variant distributions (0 = same)."""
    import pm4py

    lang_a = stochastic_language(counts_a)
    lang_b = stochastic_language(counts_b)
    if not lang_a or not lang_b:
        return 0.0 if lang_a == lang_b else 1.0
    return float(pm4py.compute_emd(lang_a, lang_b))


def pairwise_similarity(
    *,
    activities: list[set[str]],
    edges: list[set[tuple[str, str]]],
    variants: list[set[tuple[str, ...]]],
    footprints: list[set[tuple[str, str]]],
    variant_counts_list: list[dict[tuple[str, ...], int]],
) -> dict[str, list[list[float]]]:
    """Build the N-by-N metric matrices from per-log feature sets.

    Each list is indexed by log (baseline first). Returns symmetric matrices
    for: emd (distance), footprints_similarity, activity_overlap, edge_overlap,
    variant_overlap (all Jaccard, 1 = identical).
    """
    n = len(activities)
    emd = [[0.0] * n for _ in range(n)]
    fp_sim = [[1.0] * n for _ in range(n)]
    act_ov = [[1.0] * n for _ in range(n)]
    edge_ov = [[1.0] * n for _ in range(n)]
    var_ov = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            e = emd_distance(variant_counts_list[i], variant_counts_list[j])
            f = _jaccard(footprints[i], footprints[j])
            a = _jaccard(activities[i], activities[j])
            g = _jaccard(edges[i], edges[j])
            v = _jaccard(variants[i], variants[j])
            emd[i][j] = emd[j][i] = e
            fp_sim[i][j] = fp_sim[j][i] = f
            act_ov[i][j] = act_ov[j][i] = a
            edge_ov[i][j] = edge_ov[j][i] = g
            var_ov[i][j] = var_ov[j][i] = v
    return {
        "emd": emd,
        "footprints_similarity": fp_sim,
        "activity_overlap": act_ov,
        "edge_overlap": edge_ov,
        "variant_overlap": var_ov,
    }
