"""Rank assignment (guide §3, paper Eqs. 1-7).

CP-SAT integer program: minimize ``λ1·Σalpha² + λ2·r_end`` where alpha is an edge's
precedence-violation severity (0 downward, 1 horizontal, >1 upward). Squaring
makes a shared rank for a bidirectional pair (1²+1² = 2) cheaper than adjacent
ranks (2²+0² = 4), which is exactly where horizontal edges are wanted; the
``r_end`` term prevents vertical stretching.

The published objective has multiple optima (free nodes can float between their
neighbours without changing Σalpha² or r_end), which would make golden tests and
cross-version determinism flaky. A lexicographic ``+ Σr`` tie-break — every
node pulled as early as possible — makes the paper's Table 4 toy optimum
unique, so goldens hold across ortools versions and no seed juggling is needed.

`ortools` is imported inside :func:`solve_ranks` only: the platform interpreter
must never pay the import, and the pipeline degrades to heuristic ranks (with
an explicit `solver.status`) when the wheel is missing.
"""

from __future__ import annotations

import time
from itertools import pairwise

from .model import LayoutGraph, LayoutOptions, SolverInfo


def _forward_dag(graph: LayoutGraph) -> set[tuple[str, str]]:
    """Drop DFS back edges so the remainder is acyclic. Deterministic: roots and
    adjacency are id-sorted, with the artificial start (when present) first."""
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in graph.node_ids()}
    for source, target in sorted(graph.edges):
        adjacency[source].append(target)

    roots = sorted(adjacency)
    if graph.start_id is not None:
        roots.remove(graph.start_id)
        roots.insert(0, graph.start_id)

    forward: set[tuple[str, str]] = set()
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    for root in roots:
        if state.get(root):
            continue
        state[root] = 1
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, neighbours = stack[-1]
            nxt = next(neighbours, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:
                continue  # back edge — breaking the cycle here keeps a DAG
            forward.add((node, nxt))
            if not state.get(nxt):
                state[nxt] = 1
                stack.append((nxt, iter(adjacency[nxt])))
    return forward


def longest_path_ranks(graph: LayoutGraph, backbone: list[str] | None) -> dict[str, int]:
    """Cycle-broken longest-path ranks — CP-SAT warm start and last-resort
    fallback. Bumps the backbone chain afterwards so downstream stages can rely
    on strictly increasing backbone ranks even without a solver."""
    ids = sorted(graph.node_ids())
    if not ids:
        return {}
    forward = _forward_dag(graph)

    indegree = {node_id: 0 for node_id in ids}
    successors: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for source, target in sorted(forward):
        indegree[target] += 1
        successors[source].append(target)

    base = 1 if graph.start_id is None else 2
    ranks = {node_id: (1 if node_id == graph.start_id else base) for node_id in ids}
    ready = sorted(node_id for node_id in ids if indegree[node_id] == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in successors[node]:
            ranks[nxt] = max(ranks[nxt], ranks[node] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                # Insert keeping `ready` sorted — determinism over speed at DFG scale.
                lo = 0
                while lo < len(ready) and ready[lo] < nxt:
                    lo += 1
                ready.insert(lo, nxt)

    if backbone:
        bump_backbone(ranks, backbone)
    if graph.end_id is not None:
        others = [rank for node_id, rank in ranks.items() if node_id != graph.end_id]
        if others:
            ranks[graph.end_id] = max(ranks[graph.end_id], max(others) + 1)
    return ranks


def bump_backbone(ranks: dict[str, int], backbone: list[str]) -> None:
    """Force strictly increasing ranks along the backbone (paper Eq. 3).

    Heuristic ranks can place consecutive backbone nodes on one rank; two
    order-0 nodes on the same rank would collide in positioning, so dependents
    are pushed down in place.
    """
    for previous, current in pairwise(backbone):
        if previous in ranks and current in ranks and ranks[current] <= ranks[previous]:
            ranks[current] = ranks[previous] + 1


def solve_ranks(
    graph: LayoutGraph,
    backbone: list[str],
    opts: LayoutOptions,
) -> tuple[dict[str, int], SolverInfo]:
    """CP-SAT rank assignment. Never raises on solver trouble — every failure
    path returns heuristic ranks with an explicit ``SolverInfo.status``."""
    started = time.perf_counter()
    ids = sorted(graph.node_ids())
    if not ids:
        return {}, SolverInfo("optimal", 0.0, 0.0)
    if len(ids) == 1:
        return {ids[0]: 1}, SolverInfo("optimal", 0.0, 0.0)

    hint = longest_path_ranks(graph, backbone)

    def _elapsed() -> float:
        return (time.perf_counter() - started) * 1000.0

    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return dict(hint), SolverInfo("fallback_no_solver", _elapsed(), None)

    n = len(ids)
    edges = sorted(graph.edges)
    edge_set = graph.edge_set()

    model = cp_model.CpModel()
    rank = {node_id: model.NewIntVar(1, n, f"r_{i}") for i, node_id in enumerate(ids)}
    alpha = {edge: model.NewIntVar(0, n, f"a_{i}") for i, edge in enumerate(edges)}
    alpha_sq = {edge: model.NewIntVar(0, n * n, f"q_{i}") for i, edge in enumerate(edges)}
    for edge in edges:
        model.AddMultiplicationEquality(alpha_sq[edge], [alpha[edge], alpha[edge]])
        source, target = edge
        model.Add(rank[source] + 1 <= rank[target] + alpha[edge])  # Eq. 2

    for previous, current in pairwise(backbone):  # Eq. 3
        model.Add(rank[previous] + 1 <= rank[current])

    # Eqs. 4/5 — rank separation, reified instead of big-M. Bidirectional pairs
    # are exempt while horizontal edges are allowed: shared ranks are the point.
    separated: set[tuple[str, str]] = set()
    for index, (source, target) in enumerate(edges):
        symmetric = (target, source) in edge_set
        if symmetric and opts.allow_horizontal_edges:
            continue
        pair = (min(source, target), max(source, target))
        if pair in separated:
            continue
        separated.add(pair)
        above = model.NewBoolVar(f"y_{index}")
        model.Add(rank[source] <= rank[target] - 1).OnlyEnforceIf(above.Not())
        model.Add(rank[source] >= rank[target] + 1).OnlyEnforceIf(above)

    if graph.start_id is not None:  # Eq. 6
        model.Add(rank[graph.start_id] == 1)
        for node_id in ids:
            if node_id != graph.start_id:
                model.Add(rank[node_id] >= rank[graph.start_id] + 1)
    if graph.end_id is not None:  # Eq. 7
        end_term = rank[graph.end_id]
        for node_id in ids:
            if node_id != graph.end_id:
                model.Add(rank[node_id] + 1 <= rank[graph.end_id])
    else:
        end_term = model.NewIntVar(1, n, "r_max")
        model.AddMaxEquality(end_term, list(rank.values()))

    # Integerized weights (λ step is 0.1) with the Σr tie-break strictly
    # subordinate: any 1-unit primary improvement beats the whole Σr range.
    weight_sq = max(1, round(opts.lambda_sq * 10))
    weight_end = max(1, round(opts.lambda_end * 10))
    scale = n * n + 1
    primary = weight_sq * sum(alpha_sq.values()) + weight_end * end_term
    model.Minimize(scale * primary + sum(rank.values()))

    for node_id in ids:
        model.AddHint(rank[node_id], min(max(hint.get(node_id, 1), 1), n))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = opts.time_limit_s
    solver.parameters.num_search_workers = 1  # determinism: single worker + fixed seed
    solver.parameters.random_seed = opts.seed

    try:
        status = solver.Solve(model)
    except Exception:
        return dict(hint), SolverInfo("fallback_longest_path", _elapsed(), None)

    if status == cp_model.OPTIMAL:
        label = "optimal"
    elif status == cp_model.FEASIBLE:
        label = "feasible_timeout"
    else:
        return dict(hint), SolverInfo("fallback_longest_path", _elapsed(), None)

    ranks = {node_id: int(solver.Value(rank[node_id])) for node_id in ids}
    objective = float(
        opts.lambda_sq * sum(solver.Value(alpha_sq[edge]) for edge in edges)
        + opts.lambda_end * solver.Value(end_term)
    )
    return ranks, SolverInfo(label, _elapsed(), objective)
