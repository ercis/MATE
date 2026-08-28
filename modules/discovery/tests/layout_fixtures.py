"""Toy example from the backbone-layout paper (Table 4 / Fig. 10).

The paper does not print its toy DFG edge list; this reconstruction is derived
from every published golden simultaneously and reproduces all of them:

- IP ranks: Start=1, A=2, B=G=3, C=H=4, F=I=J=5, K=6, D=7, L=8, E=9, End=10
  (B/G bidirectional -> shared rank / horizontal edge), objective Σα²+r_end=12.
- Virtual chains: 2 on (C,D), 1 on (D,E), 3 on (F,E), 1 on (I,D) — 7 total.
- Components after backbone removal, sizes {4, 1, 3, 2, 1}:
  {F,+3 virtuals}, {G}, {H,I,+1 virtual}, {J,K}, {L}.
- Balance: left 6 / right 5, y = 1.
- Cross-minimization: initial orders put (v_F8, L) left at rank 8 and (I, J) /
  (v_ID, K) right at ranks 5/6 with exactly the guide's swaps -> 0 crossings.
"""

from __future__ import annotations

from modules.discovery.layout.model import LayoutGraph, LayoutNode, normalize_graph

START = "__START__"
END = "__END__"

# (trace, case count) — most frequent first; DFG edges below are exactly the
# directly-follows pairs of these traces plus the artificial terminals.
TOY_VARIANTS: list[tuple[list[str], int]] = [
    (["A", "B", "C", "D", "E"], 10),
    (["A", "B", "G", "B", "C", "D", "E"], 5),
    (["A", "B", "H", "I", "D", "L", "E"], 4),
    (["A", "B", "C", "J", "K", "D", "E"], 3),
    (["A", "B", "C", "F", "E"], 2),
]

TOY_ACTIVITIES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]

TOY_EDGES: list[tuple[str, str]] = [
    (START, "A"),
    ("A", "B"),
    ("B", "C"),
    ("C", "D"),
    ("D", "E"),
    ("E", END),
    ("B", "G"),
    ("G", "B"),
    ("B", "H"),
    ("H", "I"),
    ("I", "D"),
    ("C", "J"),
    ("J", "K"),
    ("K", "D"),
    ("C", "F"),
    ("F", "E"),
    ("D", "L"),
    ("L", "E"),
]

GOLDEN_RANKS: dict[str, int] = {
    START: 1,
    "A": 2,
    "B": 3,
    "G": 3,
    "C": 4,
    "H": 4,
    "F": 5,
    "I": 5,
    "J": 5,
    "K": 6,
    "D": 7,
    "L": 8,
    "E": 9,
    END: 10,
}

GOLDEN_BACKBONE = [START, "A", "B", "C", "D", "E", END]
GOLDEN_OBJECTIVE = 12.0  # Σα² (B<->G horizontal: 1²+1²) + r_end (10)
GOLDEN_COMPONENT_SIZES = [4, 3, 2, 1, 1]  # sorted (size desc, min id)


def toy_nodes() -> list[LayoutNode]:
    nodes = [LayoutNode(id=activity, width=220.0, height=59.0) for activity in TOY_ACTIVITIES]
    nodes.append(LayoutNode(id=START, width=112.0, height=43.0))
    nodes.append(LayoutNode(id=END, width=112.0, height=43.0))
    return nodes


def toy_graph() -> LayoutGraph:
    graph, _ = normalize_graph(toy_nodes(), TOY_EDGES, START, END)
    return graph


def toy_request() -> dict[str, object]:
    """Plain-dict form of the toy, as the route worker would receive it."""
    return {
        "nodes": [{"id": n.id, "width": n.width, "height": n.height} for n in toy_nodes()],
        "edges": [list(edge) for edge in TOY_EDGES],
        "variants": [(list(seq), count) for seq, count in TOY_VARIANTS],
        "start_id": START,
        "end_id": END,
    }


# -- backbone-v2 routing fixtures ------------------------------------------
# Each isolates one thing the router has to get right. Sizes match the toy so
# the pitch (280 x 149) and therefore the channel/column clearances are the
# same ones production sees.


def _request(
    activities: list[str],
    edges: list[tuple[str, str]],
    variants: list[tuple[list[str], int]],
) -> dict[str, object]:
    nodes = [{"id": a, "width": 220.0, "height": 59.0} for a in activities]
    nodes.append({"id": START, "width": 112.0, "height": 43.0})
    nodes.append({"id": END, "width": 112.0, "height": 43.0})
    return {
        "nodes": nodes,
        "edges": [list(edge) for edge in edges],
        "variants": [(list(seq), count) for seq, count in variants],
        "start_id": START,
        "end_id": END,
    }


def hub_request() -> dict[str, object]:
    """One node with ten out-edges — more than a 220px face can hold, so the
    port distributor must spill onto the side faces."""
    leaves = [f"L{index}" for index in range(10)]
    edges: list[tuple[str, str]] = [(START, "HUB"), ("HUB", END)]
    variants: list[tuple[list[str], int]] = [(["HUB"], 100)]
    for index, leaf in enumerate(leaves):
        edges.append(("HUB", leaf))
        edges.append((leaf, END))
        variants.append((["HUB", leaf], 10 - index))
    return _request(["HUB", *leaves], edges, variants)


def crowded_row_request() -> dict[str, object]:
    """Six activities fanned onto ONE rank, plus an edge between two of them.

    A fan (rather than a chain) is what keeps them level: every R is one step
    from START, so the rank IP has no reason to separate them. The R0->R5 edge
    then has to cross the row — which v1 drew as a straight line through
    whatever happened to sit in between.
    """
    row = [f"R{index}" for index in range(6)]
    edges: list[tuple[str, str]] = [("R0", "R5")]
    variants: list[tuple[list[str], int]] = []
    for index, node in enumerate(row):
        edges.append((START, node))
        edges.append((node, END))
        variants.append(([node], 10 - index))
    return _request(row, edges, variants)


def long_jump_request() -> dict[str, object]:
    """An adjacent-rank edge whose endpoints sit three columns apart, which is
    where the single-corner side route beats the two-corner staircase."""
    activities = ["A", "B", "C", "D", "E"]
    edges: list[tuple[str, str]] = [
        (START, "A"),
        ("A", "B"),
        ("B", "C"),
        ("C", END),
        (START, "D"),
        ("D", "E"),
        ("E", END),
        ("A", "E"),
    ]
    variants: list[tuple[list[str], int]] = [
        (["A", "B", "C"], 40),
        (["D", "E"], 20),
        (["A", "E"], 10),
    ]
    return _request(activities, edges, variants)


_SKIP_WINDOW = 6


def random_request(seed: int, node_count: int, edge_count: int) -> dict[str, object]:
    """A seeded pseudo-random DFG — the broad net under `qm_no_overlap == 0`.

    Deterministic by construction (a linear congruential walk, no `random`
    module) so a failure is always reproducible from the seed alone.

    Edges stay inside a +/-`_SKIP_WINDOW` index window, which is what makes the
    graph shaped like a real process map rather than a worst case: a directly-
    follows relation between activity 3 and activity 300 is not something logs
    produce, and allowing it here would generate a virtual node per crossed
    rank (hundreds of thousands of them) and measure `insert_virtual_nodes`
    instead of the router.
    """
    activities = [f"N{index}" for index in range(node_count)]
    state = seed * 2 + 1
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    for index in range(node_count - 1):  # a spine keeps the graph connected
        edges.append((activities[index], activities[index + 1]))
        seen.add((activities[index], activities[index + 1]))

    attempts = 0
    while len(edges) < edge_count and attempts < edge_count * 50:
        attempts += 1
        state = (state * 1103515245 + 12345) % (2**31)
        source_index = state % node_count
        state = (state * 1103515245 + 12345) % (2**31)
        offset = (state % (2 * _SKIP_WINDOW + 1)) - _SKIP_WINDOW
        target_index = source_index + offset
        if not 0 <= target_index < node_count or source_index == target_index:
            continue
        pair = (activities[source_index], activities[target_index])
        if pair in seen:
            continue
        seen.add(pair)
        edges.append(pair)

    edges.append((START, activities[0]))
    edges.append((activities[-1], END))
    variants: list[tuple[list[str], int]] = [(activities[:], node_count)]
    return _request(activities, edges, variants)
