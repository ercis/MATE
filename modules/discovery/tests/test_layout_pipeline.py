"""End-to-end pipeline (compute_layout) — both algorithms, degenerate inputs.

Runs with or without ortools: the backbone algorithm's solver status is
asserted as a set covering both the optimal and the no-solver fallback path.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from modules.discovery.layout.mennens import mennens_ranks
from modules.discovery.layout.pipeline import compute_layout

from .layout_fixtures import (
    END,
    START,
    TOY_ACTIVITIES,
    TOY_VARIANTS,
    toy_graph,
    toy_request,
)


def _compute(algorithm: str, options: dict | None = None):
    request = toy_request()
    merged = {**(options or {}), "algorithm": algorithm}
    return compute_layout(
        request["nodes"],  # type: ignore[arg-type]
        request["edges"],  # type: ignore[arg-type]
        request["variants"],  # type: ignore[arg-type]
        request["start_id"],  # type: ignore[arg-type]
        request["end_id"],  # type: ignore[arg-type]
        merged,
    )


def _without_timings(response: dict) -> dict:
    stripped = {key: value for key, value in response.items() if key not in ("wall_ms", "route_ms")}
    stripped["solver"] = {
        key: value for key, value in response["solver"].items() if key != "wall_ms"
    }
    return stripped


ALL_IDS = [*TOY_ACTIVITIES, START, END]


def test_backbone_end_to_end() -> None:
    response = _compute("backbone")
    assert response["kind"] == "dfg_layout"
    assert response["algorithm"] == "backbone"
    assert response["solver"]["status"] in {"optimal", "fallback_no_solver"}
    assert set(response["x"]) == set(ALL_IDS)
    assert set(response["y"]) == set(ALL_IDS)
    # Straight spine through node centers, regardless of solver availability.
    spine_centers = {
        response["x"][node] + (112.0 if node in (START, END) else 220.0) / 2.0
        for node in (START, "A", "B", "C", "D", "E", END)
    }
    assert len(spine_centers) == 1
    # Top-left normalization: nothing negative, something at each origin.
    assert min(response["x"].values()) >= 0.0
    assert min(response["y"].values()) >= 0.0
    # Response edges parallel the request.
    request_pairs = [tuple(edge) for edge in toy_request()["edges"]]  # type: ignore[union-attr]
    assert [(e["source"], e["target"]) for e in response["edges"]] == request_pairs
    bidirectional = {(e["source"], e["target"]) for e in response["edges"] if e["bidirectional"]}
    assert bidirectional == {("B", "G"), ("G", "B")}
    assert response["metrics"]["qm_ec"] == 0.0


def test_backbone_long_edges_carry_waypoints() -> None:
    response = _compute("backbone")
    by_pair = {(e["source"], e["target"]): e for e in response["edges"]}
    # (C,D) spans ranks 4->7: two virtual waypoints, pinned to the spine column
    # (its virtuals are backbone-spliced), one rank row apart.
    waypoints = by_pair[("C", "D")]["waypoints"]
    assert len(waypoints) == 2
    spine_center = response["x"]["C"] + 110.0
    assert all(point[0] == pytest.approx(spine_center) for point in waypoints)
    assert waypoints[1][1] - waypoints[0][1] == pytest.approx(149.0)
    assert len(by_pair[("F", "E")]["waypoints"]) == 3
    assert len(by_pair[("I", "D")]["waypoints"]) == 1
    assert by_pair[("A", "B")]["waypoints"] == []


def test_sugiyama_end_to_end_forbids_horizontal_edges() -> None:
    response = _compute("sugiyama")
    assert response["solver"]["status"] == "heuristic"
    ranks = response["rank"]
    for edge in response["edges"]:
        if not edge["self_loop"]:
            assert ranks[edge["source"]] != ranks[edge["target"]]
    # The benchmark trades rank count for it: more ranks than the IP's 10.
    assert max(ranks.values()) >= 10
    assert set(response["x"]) == set(ALL_IDS)


def test_mennens_first_variant_forms_strict_chain() -> None:
    ranks = mennens_ranks(toy_graph(), TOY_VARIANTS)
    chain = [START, "A", "B", "C", "D", "E"]
    for previous, current in pairwise(chain):
        assert ranks[previous] < ranks[current]
    assert ranks[END] == max(ranks.values())


def test_empty_graph() -> None:
    response = compute_layout([], [], [], None, None, {"algorithm": "backbone"})
    assert response["solver"]["status"] == "empty"
    assert response["x"] == {}
    assert response["edges"] == []


def test_single_node_with_self_loop() -> None:
    response = compute_layout(
        [{"id": "only"}],
        [["only", "only"]],
        [(["only"], 3)],
        None,
        None,
        {"algorithm": "backbone"},
    )
    assert response["solver"]["status"] == "trivial"
    assert response["x"] == {"only": 0.0}
    assert response["edges"][0]["self_loop"] is True


def test_self_loops_pass_through_untouched() -> None:
    request = toy_request()
    edges = [*request["edges"], ["B", "B"]]  # type: ignore[misc]
    response = compute_layout(
        request["nodes"],
        edges,
        request["variants"],
        START,
        END,  # type: ignore[arg-type]
        {"algorithm": "backbone"},
    )
    loop = response["edges"][-1]
    assert loop["self_loop"] is True
    assert loop["waypoints"] == []
    assert response["metrics"]["qm_ec"] == 0.0


def test_disconnected_node_still_gets_coordinates() -> None:
    request = toy_request()
    nodes = [*request["nodes"], {"id": "Orphan", "width": 220.0, "height": 59.0}]  # type: ignore[misc]
    response = compute_layout(
        nodes,
        request["edges"],
        request["variants"],
        START,
        END,  # type: ignore[arg-type]
        {"algorithm": "backbone"},
    )
    assert "Orphan" in response["x"]
    assert "Orphan" in response["rank"]


def test_size_cap_degrades_to_heuristic_ranks_with_straight_spine() -> None:
    capped = _compute("backbone", options={"max_ip_nodes": 2})
    assert capped["solver"]["status"] == "fallback_size_cap"
    spine_centers = {
        capped["x"][node] + (112.0 if node in (START, END) else 220.0) / 2.0
        for node in (START, "A", "B", "C", "D", "E", END)
    }
    assert len(spine_centers) == 1  # backbone treatment stays on


def test_no_variants_falls_back_for_backbone() -> None:
    request = toy_request()
    response = compute_layout(
        request["nodes"],
        request["edges"],
        [],
        START,
        END,  # type: ignore[arg-type]
        {"algorithm": "backbone"},
    )
    assert response["solver"]["status"] == "fallback_no_backbone"
    assert set(response["x"]) == set(ALL_IDS)


def test_unknown_edge_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown node id"):
        compute_layout([{"id": "a"}], [["a", "ghost"]], [], None, None, {"algorithm": "backbone"})


def test_determinism_across_runs() -> None:
    assert _without_timings(_compute("backbone")) == _without_timings(_compute("backbone"))
    assert _without_timings(_compute("sugiyama")) == _without_timings(_compute("sugiyama"))


def test_v1_response_carries_no_v2_fields() -> None:
    """backbone-v2 is additive: the older algorithms' payload is untouched.

    Kept as a shape assertion rather than a golden blob so a legitimate v1
    change does not need this file regenerated — what must not happen is v2
    leaking a field or a metric into a response that never asked for one.
    """
    v1_metrics = {"qm_be", "qm_bal", "qm_ec", "qm_el", "qm_eo", "qm_no"}
    v1_edge_keys = {
        "source",
        "target",
        "waypoints",
        "self_loop",
        "back_edge",
        "bidirectional",
    }
    for algorithm in ("backbone", "sugiyama"):
        response = _compute(algorithm)
        assert "route_ms" not in response
        assert set(response["metrics"]) == v1_metrics
        for edge in response["edges"]:
            assert set(edge) == v1_edge_keys, f"{algorithm}: {set(edge) - v1_edge_keys}"
