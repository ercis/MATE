"""Rank assignment (guide §3): CP-SAT goldens + heuristic fallback paths.

CP-SAT cases skip when `ortools` is absent (the root venv does not carry it);
run the full suite with:
    uv run --extra dev --with "ortools>=9.11,<10" pytest modules/discovery/tests -v
"""

from __future__ import annotations

import sys
from itertools import pairwise

import pytest
from modules.discovery.layout.model import LayoutOptions
from modules.discovery.layout.rank_ip import bump_backbone, longest_path_ranks, solve_ranks

from .layout_fixtures import (
    GOLDEN_BACKBONE,
    GOLDEN_OBJECTIVE,
    GOLDEN_RANKS,
    toy_graph,
)


def test_longest_path_ranks_toy() -> None:
    ranks = longest_path_ranks(toy_graph(), GOLDEN_BACKBONE)
    # Longest-path agrees with the IP optimum everywhere except G: without the
    # shared-rank exemption it lands below B (tree edge B->G).
    expected = dict(GOLDEN_RANKS, G=4)
    assert ranks == expected


def test_longest_path_ranks_invariants() -> None:
    graph = toy_graph()
    ranks = longest_path_ranks(graph, GOLDEN_BACKBONE)
    assert ranks[graph.start_id or ""] == 1
    assert all(rank >= 2 for node, rank in ranks.items() if node != graph.start_id)
    assert ranks[graph.end_id or ""] == max(ranks.values())
    for previous, current in pairwise(GOLDEN_BACKBONE):
        assert ranks[previous] < ranks[current]


def test_bump_backbone_forces_strict_increase() -> None:
    ranks = {"a": 3, "b": 3, "c": 2}
    bump_backbone(ranks, ["a", "b", "c"])
    assert ranks == {"a": 3, "b": 4, "c": 5}


def test_fallback_when_solver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Poisoning sys.modules makes `import ortools` raise even when installed —
    # including the submodule keys, which the import system checks first once
    # another test has already imported them.
    for name in ("ortools", "ortools.sat", "ortools.sat.python"):
        monkeypatch.setitem(sys.modules, name, None)
    ranks, info = solve_ranks(toy_graph(), GOLDEN_BACKBONE, LayoutOptions())
    assert info.status == "fallback_no_solver"
    assert info.objective is None
    assert ranks == dict(GOLDEN_RANKS, G=4)  # the longest-path fallback


def test_golden_ranks_table4() -> None:
    pytest.importorskip("ortools")
    ranks, info = solve_ranks(toy_graph(), GOLDEN_BACKBONE, LayoutOptions())
    assert info.status == "optimal"
    assert ranks == GOLDEN_RANKS  # B and G share rank 3 — the horizontal edge
    assert info.objective == pytest.approx(GOLDEN_OBJECTIVE)


def test_horizontal_edges_flag_off_separates_bidirectional_pair() -> None:
    pytest.importorskip("ortools")
    opts = LayoutOptions(allow_horizontal_edges=False)
    ranks, info = solve_ranks(toy_graph(), GOLDEN_BACKBONE, opts)
    assert info.status == "optimal"
    assert ranks["G"] != ranks["B"]
    # Forced separation costs an upward alpha of 2 on one direction: 2² + r_end.
    assert info.objective == pytest.approx(14.0)
    # The Σr tie-break pulls G as early as possible.
    assert ranks["G"] == 2


def test_solver_determinism() -> None:
    pytest.importorskip("ortools")
    first, _ = solve_ranks(toy_graph(), GOLDEN_BACKBONE, LayoutOptions())
    second, _ = solve_ranks(toy_graph(), GOLDEN_BACKBONE, LayoutOptions())
    assert first == second


def test_time_limit_returns_usable_ranks() -> None:
    pytest.importorskip("ortools")
    opts = LayoutOptions(time_limit_s=0.001)
    ranks, info = solve_ranks(toy_graph(), GOLDEN_BACKBONE, opts)
    assert info.status in {"optimal", "feasible_timeout", "fallback_longest_path"}
    assert set(ranks) == {n.id for n in toy_graph().nodes}
    for previous, current in pairwise(GOLDEN_BACKBONE):
        assert ranks[previous] < ranks[current]
