"""Mennens et al. 2019 heuristic ranking (guide §10) — the paper's benchmark,
exposed to users as the "sugiyama" algorithm and reused as the size-cap
fallback for the backbone algorithm.

Reconstructed from the paper's description: variants are processed in
descending case-frequency order; a variant's not-yet-ranked activities are
appended *below* every rank fixed by earlier variants, keeping every processed
edge strictly downward (horizontal edges forbidden). Revisits of already-ranked
activities become back edges — the benchmark's known cost on QM_be. The end
terminal is re-pinned below everything at the close, and ranks are compressed
dense so the grid carries no empty rows.
"""

from __future__ import annotations

from .backbone import Variant
from .model import LayoutGraph


def mennens_ranks(graph: LayoutGraph, variants: list[Variant]) -> dict[str, int]:
    visible = set(graph.node_ids()) - {
        terminal for terminal in (graph.start_id, graph.end_id) if terminal is not None
    }
    ranks: dict[str, int] = {}
    max_body_rank = 0  # excludes the end terminal so later variants stay above it

    def _assign(activity: str, candidate: int) -> None:
        nonlocal max_body_rank
        ranks[activity] = candidate
        if activity != graph.end_id:
            max_body_rank = max(max_body_rank, candidate)

    for sequence, _count in sorted(variants, key=lambda v: (-v[1], v[0])):
        walk = [activity for activity in sequence if activity in visible]
        if graph.start_id is not None:
            walk.insert(0, graph.start_id)
        if graph.end_id is not None:
            walk.append(graph.end_id)
        for index, activity in enumerate(walk):
            if activity in ranks:
                continue
            previous = walk[index - 1] if index else None
            below_all = max_body_rank + 1
            if previous is not None and previous in ranks:
                _assign(activity, max(ranks[previous] + 1, below_all))
            else:
                _assign(activity, below_all if ranks else 1)

    # Visible activities missing from the (truncated) variant list still need a
    # rank; append them below, one rank each — horizontal edges stay forbidden.
    for activity in sorted(visible - set(ranks)):
        _assign(activity, max_body_rank + 1)
    for terminal in (graph.start_id, graph.end_id):
        if terminal is not None and terminal not in ranks:
            _assign(terminal, 1 if terminal == graph.start_id else max_body_rank + 1)
    if graph.end_id is not None:
        ranks[graph.end_id] = max_body_rank + 1

    # Dense compression: strictness is preserved under any monotone remap.
    remap = {value: index + 1 for index, value in enumerate(sorted(set(ranks.values())))}
    return {activity: remap[value] for activity, value in ranks.items()}
