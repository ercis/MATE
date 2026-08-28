"""Backbone determination (guide §2, paper Def. 11).

The backbone is the most frequent variant's activity sequence, deduplicated in
order of first appearance, **projected onto the visible node set** — the client
filters activities before requesting a layout, so backbone activities that were
filtered out must simply drop from the sequence rather than fragment it.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from .model import LayoutGraph

Variant = tuple[list[str], int]  # (activity sequence, case count)


def project_variant(variant: Sequence[str], visible: set[str]) -> list[str]:
    """Unique-in-order activities of ``variant`` that are visible."""
    seen: set[str] = set()
    projected: list[str] = []
    for activity in variant:
        if activity in visible and activity not in seen:
            seen.add(activity)
            projected.append(activity)
    return projected


def extract_backbone(graph: LayoutGraph, variants: list[Variant]) -> list[str] | None:
    """Backbone node sequence, or None when no variant projects onto the graph.

    Variants are tried most-frequent-first (ties broken by sequence) until one
    projects to at least one visible activity — a fully filtered-out top
    variant must not kill the layout. Terminals (the paper's artificial
    source/sink) are prepended/appended when the request carries them; they are
    excluded from the visibility projection since they never appear in traces.
    """
    terminals = {t for t in (graph.start_id, graph.end_id) if t is not None}
    visible = set(graph.node_ids()) - terminals

    ordered = sorted(variants, key=lambda v: (-v[1], v[0]))
    body: list[str] = []
    for sequence, _count in ordered:
        body = project_variant(sequence, visible)
        if body:
            break
    if not body:
        return None

    backbone = list(body)
    if graph.start_id is not None:
        backbone.insert(0, graph.start_id)
    if graph.end_id is not None:
        backbone.append(graph.end_id)
    return backbone


def backbone_edges(backbone: Sequence[str], edge_set: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """BE = consecutive backbone pairs that exist in E (paper Def. 11)."""
    return {(u, v) for u, v in pairwise(backbone) if (u, v) in edge_set}
