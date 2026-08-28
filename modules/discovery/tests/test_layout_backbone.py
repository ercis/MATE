"""Backbone determination (guide §2) against the toy goldens."""

from __future__ import annotations

from modules.discovery.layout.backbone import backbone_edges, extract_backbone, project_variant
from modules.discovery.layout.model import normalize_graph

from .layout_fixtures import (
    END,
    GOLDEN_BACKBONE,
    START,
    TOY_EDGES,
    TOY_VARIANTS,
    toy_graph,
    toy_nodes,
)


def test_toy_backbone_is_most_frequent_variant_with_terminals() -> None:
    assert extract_backbone(toy_graph(), TOY_VARIANTS) == GOLDEN_BACKBONE


def test_loops_deduplicate_in_first_appearance_order() -> None:
    graph = toy_graph()
    variants = [(["A", "B", "G", "B", "C"], 99)]
    assert extract_backbone(graph, variants) == [START, "A", "B", "G", "C", END]


def test_projection_drops_filtered_activities_without_fragmenting() -> None:
    graph, _ = normalize_graph(
        [n for n in toy_nodes() if n.id != "B"],
        [(s, t) for s, t in TOY_EDGES if "B" not in (s, t)],
        START,
        END,
    )
    assert extract_backbone(graph, TOY_VARIANTS) == [START, "A", "C", "D", "E", END]


def test_fully_filtered_top_variant_falls_through_to_next() -> None:
    graph, _ = normalize_graph(
        [n for n in toy_nodes() if n.id in ("H", "I", START, END)],
        [("H", "I")],
        START,
        END,
    )
    # Variant 1 (A,B,C,D,E) projects to nothing; variant 3 carries H/I.
    assert extract_backbone(graph, TOY_VARIANTS) == [START, "H", "I", END]


def test_no_variants_yields_none() -> None:
    assert extract_backbone(toy_graph(), []) is None


def test_backbone_edges_require_presence_in_e() -> None:
    edge_set = set(TOY_EDGES)
    be = backbone_edges(GOLDEN_BACKBONE, edge_set)
    assert be == {
        (START, "A"),
        ("A", "B"),
        ("B", "C"),
        ("C", "D"),
        ("D", "E"),
        ("E", END),
    }
    # A gap in E just drops that pair — the sequence itself stays intact.
    assert ("A", "B") not in backbone_edges(GOLDEN_BACKBONE, edge_set - {("A", "B")})


def test_project_variant_unique_in_order() -> None:
    assert project_variant(["A", "B", "A", "C"], {"A", "C"}) == ["A", "C"]
