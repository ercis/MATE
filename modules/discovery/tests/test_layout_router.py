"""Backbone-v2 edge routing.

The load-bearing test in this file is `test_no_route_ever_crosses_a_node`:
"edges never pass under another node" is the requirement the whole router
exists for, and it is checked on the *emitted curve samples*, not the skeleton,
so a fillet that bulges fails it too.
"""

from __future__ import annotations

import time
from itertools import pairwise

import pytest
from modules.discovery.layout.model import LayoutOptions
from modules.discovery.layout.obstacles import build_field
from modules.discovery.layout.pipeline import compute_layout
from modules.discovery.layout.router import overlap_samples, route_edges
from modules.discovery.layout.virtual import Expanded

from .layout_fixtures import (
    GOLDEN_BACKBONE,
    crowded_row_request,
    hub_request,
    long_jump_request,
    random_request,
    toy_request,
)


def _v2(request: dict[str, object], **options: object) -> dict[str, object]:
    merged: dict[str, object] = {"algorithm": "backbone-v2"}
    merged.update(options)
    return compute_layout(
        request["nodes"],  # type: ignore[arg-type]
        request["edges"],  # type: ignore[arg-type]
        request["variants"],  # type: ignore[arg-type]
        request["start_id"],  # type: ignore[arg-type]
        request["end_id"],  # type: ignore[arg-type]
        merged,
    )


def _edge(response: dict[str, object], source: str, target: str) -> dict[str, object]:
    for edge in response["edges"]:  # type: ignore[index]
        if edge["source"] == source and edge["target"] == target:
            return edge
    raise AssertionError(f"edge ({source}, {target}) missing from the response")


@pytest.mark.parametrize(
    "name,request_factory",
    [
        ("toy", toy_request),
        ("hub", hub_request),
        ("crowded_row", crowded_row_request),
        ("long_jump", long_jump_request),
        ("random", lambda: random_request(seed=7, node_count=60, edge_count=200)),
    ],
)
def test_no_route_ever_crosses_a_node(name: str, request_factory) -> None:  # type: ignore[no-untyped-def]
    response = _v2(request_factory())
    metrics = response["metrics"]
    assert metrics["qm_no_overlap"] == 0.0, f"{name}: a route entered a node"
    # A repair means an invariant the router relies on did not hold. It is a
    # canary, not a failure mode — if this ever trips, the geometry changed.
    assert metrics["qm_repairs"] == 0.0, f"{name}: needed {metrics['qm_repairs']} repair(s)"


def test_every_edge_carries_geometry() -> None:
    response = _v2(toy_request())
    for edge in response["edges"]:
        if edge["self_loop"]:
            continue
        assert edge["path"].startswith("M "), edge
        assert edge["source_port"] is not None and edge["target_port"] is not None
        assert edge["arrow"] is not None and edge["label_at"] is not None
        assert len(edge["polyline"]) >= 2


def test_backbone_spine_is_straight_and_bendless() -> None:
    response = _v2(toy_request())
    for source, target in pairwise(GOLDEN_BACKBONE):
        edge = _edge(response, source, target)
        assert edge["bends"] == 0, f"{source}->{target} bent"
        assert edge["source_port"]["face"] == "bottom"
        assert edge["target_port"]["face"] == "top"
        # One straight run: the path is a single line, no curve commands.
        assert "C" not in edge["path"], edge["path"]


def test_same_rank_pair_uses_side_faces_and_stays_straight() -> None:
    """B and G share a rank (the bidirectional pair the rank IP puts level).

    v1 drew this as a diagonal from bottom-centre to top-centre; v2 must go
    side to side, and the two directions must not sit on top of each other.
    """
    response = _v2(toy_request(), time_limit_s=10.0)
    if response["rank"]["B"] != response["rank"]["G"]:
        pytest.skip("no CP-SAT: the heuristic ranks put B and G on separate ranks")

    forward = _edge(response, "B", "G")
    reverse = _edge(response, "G", "B")
    assert {forward["source_port"]["face"], forward["target_port"]["face"]} == {"right", "left"}
    assert {reverse["source_port"]["face"], reverse["target_port"]["face"]} == {"right", "left"}
    assert forward["bends"] == 0 and reverse["bends"] == 0
    # Parallel, not collinear: the pair is split by `pair_bow`.
    assert forward["source_port"]["y"] != reverse["target_port"]["y"]


def _one_row(node_xs: dict[str, float], edges: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    """Route ``edges`` over hand-placed nodes that all share rank 1.

    Same-rank edges only appear when the rank IP levels a bidirectional pair,
    which no fixture can force reliably — and the interesting case (something
    sitting between the endpoints) depends on how crossmin ordered the row. So
    build the geometry directly and test the router itself.
    """
    ranks = {node: 1 for node in node_xs}
    centers_y = {node: 149.0 for node in node_xs}
    sizes = {node: (220.0, 59.0) for node in node_xs}
    expanded = Expanded(
        ranks=dict(ranks),
        real_ids=frozenset(node_xs),
        virtual_ids=frozenset(),
        unit_edges=[],
        horizontal_edges=list(edges),
        chains={edge: [edge[0], edge[1]] for edge in edges},
        backbone=[],
    )
    opts = LayoutOptions(algorithm="backbone-v2")
    field = build_field(
        list(node_xs),
        ranks,
        node_xs,
        centers_y,
        sizes,
        clearance=opts.route_clearance,
        fallback_channel_h=opts.v_gap,
    )
    result = route_edges(
        expanded,
        edges,
        set(edges),
        node_xs,
        centers_y,
        sizes,
        field,
        opts,
        fillet=True,
    )
    return result, field


def test_same_rank_edge_routes_around_the_node_between() -> None:
    """The case v1 got wrong: a straight line from A to C drawn through B."""
    result, field = _one_row({"A": 0.0, "B": 280.0, "C": 560.0}, [("A", "C")])
    route = result.routes[("A", "C")]

    assert route.bends == 2, "expected the U through the channel"
    assert route.port_s is not None and route.port_t is not None
    # Both endpoints leave through the same horizontal face and meet in the
    # channel. Which side that is depends on which channel exists — with only
    # one rank in play there is nothing below, so it dips up instead.
    assert route.port_s.face == route.port_t.face
    assert route.port_s.face in ("top", "bottom")
    assert overlap_samples(route, field, frozenset({"A", "C"})) == 0
    assert result.repairs == 0


def test_adjacent_same_rank_edge_is_a_straight_side_to_side_line() -> None:
    result, field = _one_row({"A": 0.0, "B": 280.0}, [("A", "B")])
    route = result.routes[("A", "B")]

    assert route.bends == 0
    assert route.port_s is not None and route.port_t is not None
    assert (route.port_s.face, route.port_t.face) == ("right", "left")
    assert overlap_samples(route, field, frozenset({"A", "B"})) == 0


def test_bidirectional_same_rank_pair_draws_as_two_parallel_lines() -> None:
    result, _field = _one_row({"A": 0.0, "B": 280.0}, [("A", "B"), ("B", "A")])
    forward = result.routes[("A", "B")]
    reverse = result.routes[("B", "A")]

    assert forward.bends == 0 and reverse.bends == 0
    assert forward.port_s is not None and reverse.port_t is not None
    # Split by `pair_bow`, so the two directions do not draw on top of each other.
    assert abs(forward.port_s.y - reverse.port_t.y) >= LayoutOptions().pair_bow - 0.01


def test_hub_fans_across_more_than_one_face() -> None:
    response = _v2(hub_request())
    faces = {
        edge["source_port"]["face"]
        for edge in response["edges"]
        if edge["source"] == "HUB" and not edge["self_loop"]
    }
    assert len(faces) > 1, f"ten out-edges all crammed onto {faces}"
    assert response["metrics"]["qm_no_overlap"] == 0.0


def test_parallel_runs_in_one_channel_get_distinct_tracks() -> None:
    """Two horizontal runs that overlap in x must not share a y."""
    response = _v2(hub_request())
    runs: list[tuple[float, float, float]] = []
    for edge in response["edges"]:
        points = edge.get("polyline") or []
        for (ax, ay), (bx, by) in pairwise(points):
            if abs(ay - by) < 0.01 and abs(ax - bx) > 1.0:
                runs.append((ay, min(ax, bx), max(ax, bx)))
    for index, (y0, lo0, hi0) in enumerate(runs):
        for y1, lo1, hi1 in runs[index + 1 :]:
            if abs(y0 - y1) < 0.01:
                assert hi0 <= lo1 + 0.01 or hi1 <= lo0 + 0.01, "collinear runs overlap"


def test_bends_and_length_beat_v1_on_the_toy() -> None:
    request = toy_request()
    v1 = compute_layout(
        request["nodes"],  # type: ignore[arg-type]
        request["edges"],  # type: ignore[arg-type]
        request["variants"],  # type: ignore[arg-type]
        request["start_id"],  # type: ignore[arg-type]
        request["end_id"],  # type: ignore[arg-type]
        {"algorithm": "backbone"},
    )
    v2 = _v2(request)
    assert v2["metrics"]["qm_el"] < v1["metrics"]["qm_el"]
    assert v2["metrics"]["qm_straight_frac"] > 0.5
    # "As gently as the free space allows": no corner tighter than a 20px radius.
    assert v2["metrics"]["qm_min_radius"] >= 20.0


def test_routing_is_deterministic() -> None:
    first = _v2(toy_request())
    second = _v2(toy_request())
    assert [edge["path"] for edge in first["edges"]] == [edge["path"] for edge in second["edges"]]
    assert first["metrics"] == second["metrics"]


def test_v2_reuses_the_v1_ranks_and_orders() -> None:
    """ "Based on the backbone layout" is a contract, not a description: v2 is a
    routing change, so every placement stage must land in the same place."""
    request = toy_request()
    common = (
        request["nodes"],
        request["edges"],
        request["variants"],
        request["start_id"],
        request["end_id"],
    )
    v1 = compute_layout(*common, {"algorithm": "backbone"})  # type: ignore[arg-type]
    v2 = compute_layout(*common, {"algorithm": "backbone-v2"})  # type: ignore[arg-type]
    assert v1["rank"] == v2["rank"]
    assert v1["order"] == v2["order"]
    assert v1["x"] == v2["x"]  # x is never touched, only channels grow


def test_blocked_column_is_repaired_not_drawn_through() -> None:
    """Squeeze the geometry until the optimistic routes stop fitting.

    A huge clearance makes almost every column look blocked, which is the only
    way to reach the repair lattice from the outside. The contract under stress
    is unchanged: still no route through a node.
    """
    response = _v2(random_request(seed=3, node_count=40, edge_count=140), route_clearance=28.0)
    assert response["metrics"]["qm_no_overlap"] == 0.0


def test_large_graph_routes_inside_the_interactive_budget() -> None:
    request = random_request(seed=11, node_count=400, edge_count=1500)
    started = time.perf_counter()
    response = _v2(request, max_ip_nodes=2)  # skip CP-SAT; we are timing the router
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response["metrics"]["qm_no_overlap"] == 0.0
    assert elapsed_ms < 10_000.0, f"{elapsed_ms:.0f}ms for 400 nodes / 1500 edges"
