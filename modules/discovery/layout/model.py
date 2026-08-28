"""Data model shared by every layout stage."""

from __future__ import annotations

from dataclasses import dataclass, field

# Bump on ANY behavioral change to the layout package: the route folds this
# into its cache digest, so stale cached layouts rotate out without a key
# rename or a manual invalidation.
LAYOUT_VERSION = 2

ALGORITHMS = ("backbone", "backbone-v2", "sugiyama")


@dataclass(frozen=True)
class LayoutNode:
    id: str
    width: float = 220.0
    height: float = 59.0


@dataclass(frozen=True)
class LayoutGraph:
    """Normalized input graph: ids validated, edges deduped, self-loop-free."""

    nodes: tuple[LayoutNode, ...]
    edges: tuple[tuple[str, str], ...]
    start_id: str | None = None
    end_id: str | None = None

    def node_ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def edge_set(self) -> set[tuple[str, str]]:
        return set(self.edges)

    def sizes(self) -> dict[str, tuple[float, float]]:
        return {n.id: (n.width, n.height) for n in self.nodes}


@dataclass(frozen=True)
class LayoutOptions:
    algorithm: str = "backbone"  # see ALGORITHMS
    time_limit_s: float = 10.0
    allow_horizontal_edges: bool = True
    lambda_sq: float = 1.0  # weight on Σα² (guide eq. 1)
    lambda_end: float = 1.0  # weight on r_end
    # Pixel pitch matches the Celonis clone so layout-mode switches morph:
    # x pitch = node width + h_gap, y pitch = node height + v_gap.
    h_gap: float = 60.0
    v_gap: float = 90.0
    paper_compat_metrics: bool = False
    seed: int = 0
    # Above this the rank IP is skipped in favor of heuristic ranks — CP-SAT on
    # dense DFGs past this size will not finish inside an interactive budget.
    max_ip_nodes: int = 250
    crossmin_iterations: int = 12
    position_iterations: int = 8

    # -- backbone-v2 routing (ignored by the other algorithms) --------------
    # Safety envelope grown around every node before any collision test.
    route_clearance: float = 8.0
    # Minimum spacing between two ports on the same face, and between two
    # parallel horizontal runs sharing a channel.
    port_gap: float = 12.0
    min_track_gap: float = 22.0
    # Corner rounding. `merge_len = 2 * max_fillet_radius` makes the "one large
    # arc" merge fire exactly when two arcs would have had to shrink below the
    # maximum radius, so the two rules compose without a seam.
    max_fillet_radius: float = 40.0
    merge_len: float = 80.0
    # The visible path stops this far short of the port; the arrowhead fills it.
    arrow_gap: float = 10.0
    # Lateral split between the two directions of a bidirectional pair.
    pair_bow: float = 14.0
    # Soft budget: past this the router stops rounding and emits square
    # corners rather than blowing an interactive request.
    route_budget_ms: float = 1500.0

    @staticmethod
    def from_dict(raw: dict[str, object] | None) -> LayoutOptions:
        """Build options from an untrusted plain dict, ignoring unknown keys."""
        raw = raw or {}
        defaults = LayoutOptions()

        def _f(key: str, lo: float, hi: float, fallback: float) -> float:
            try:
                value = float(raw.get(key, fallback))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return fallback
            return min(max(value, lo), hi)

        def _i(key: str, lo: int, hi: int, fallback: int) -> int:
            try:
                value = int(raw.get(key, fallback))  # type: ignore[call-overload]
            except (TypeError, ValueError):
                return fallback
            return min(max(value, lo), hi)

        algorithm = str(raw.get("algorithm", defaults.algorithm))
        if algorithm not in ALGORITHMS:
            algorithm = defaults.algorithm
        return LayoutOptions(
            algorithm=algorithm,
            time_limit_s=_f("time_limit_s", 0.001, 600.0, defaults.time_limit_s),
            allow_horizontal_edges=bool(
                raw.get("allow_horizontal_edges", defaults.allow_horizontal_edges)
            ),
            lambda_sq=_f("lambda_sq", 0.1, 10.0, defaults.lambda_sq),
            lambda_end=_f("lambda_end", 0.1, 10.0, defaults.lambda_end),
            h_gap=_f("h_gap", 10.0, 500.0, defaults.h_gap),
            v_gap=_f("v_gap", 10.0, 500.0, defaults.v_gap),
            paper_compat_metrics=bool(
                raw.get("paper_compat_metrics", defaults.paper_compat_metrics)
            ),
            seed=_i("seed", 0, 2**31 - 1, defaults.seed),
            max_ip_nodes=_i("max_ip_nodes", 2, 2000, defaults.max_ip_nodes),
            crossmin_iterations=_i("crossmin_iterations", 1, 64, defaults.crossmin_iterations),
            position_iterations=_i("position_iterations", 1, 64, defaults.position_iterations),
            route_clearance=_f("route_clearance", 0.0, 40.0, defaults.route_clearance),
            port_gap=_f("port_gap", 2.0, 60.0, defaults.port_gap),
            min_track_gap=_f("min_track_gap", 4.0, 120.0, defaults.min_track_gap),
            max_fillet_radius=_f("max_fillet_radius", 0.0, 200.0, defaults.max_fillet_radius),
            merge_len=_f("merge_len", 0.0, 400.0, defaults.merge_len),
            arrow_gap=_f("arrow_gap", 0.0, 40.0, defaults.arrow_gap),
            pair_bow=_f("pair_bow", 0.0, 80.0, defaults.pair_bow),
            route_budget_ms=_f("route_budget_ms", 50.0, 60_000.0, defaults.route_budget_ms),
        )


@dataclass
class SolverInfo:
    status: str
    wall_ms: float = 0.0
    objective: float | None = None


@dataclass(frozen=True)
class PortRef:
    """Where an edge meets a node's border.

    ``face``/``u`` are node-relative so the client can rebuild the point from
    the live rect and meet the border pixel-exactly (what `waypoint-edge.tsx`
    does today with its hard-coded bottom-centre); ``x``/``y`` are the absolute
    layout-time answer for the common case where nothing moved.
    """

    face: str  # "top" | "bottom" | "left" | "right"
    u: float  # 0..1 along that face, left→right / top→bottom
    x: float
    y: float


@dataclass
class EdgeRoute:
    """One request edge, in request order, with its routed polyline.

    ``waypoints`` means different things per algorithm, deliberately:
    ``backbone``/``sugiyama`` put one waypoint per crossed rank (virtual-node
    centres), while ``backbone-v2`` puts the interior vertices of its routed
    polyline there. v2 keeps the field populated only so a panel bundle built
    against an older API still draws something sane — the panel and the API
    deploy independently.

    Everything from ``path`` down is emitted by ``backbone-v2`` only; the other
    algorithms leave them ``None`` and their response stays byte-identical.
    """

    source: str
    target: str
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    self_loop: bool = False
    back_edge: bool = False
    bidirectional: bool = False
    path: str | None = None
    polyline: list[tuple[float, float]] | None = None
    source_port: PortRef | None = None
    target_port: PortRef | None = None
    arrow: tuple[float, float, float] | None = None  # x, y, angle in degrees
    label_at: tuple[float, float] | None = None
    bends: int = 0
    min_radius: float | None = None


@dataclass
class LayoutResult:
    x: dict[str, float]  # top-left px, React-Flow convention
    y: dict[str, float]
    rank: dict[str, int]
    order: dict[str, float]
    edges: list[EdgeRoute]
    metrics: dict[str, float]
    solver: SolverInfo


def normalize_graph(
    nodes: list[LayoutNode],
    edges: list[tuple[str, str]],
    start_id: str | None,
    end_id: str | None,
) -> tuple[LayoutGraph, list[tuple[str, str]]]:
    """Dedupe nodes/edges, strip self-loops (returned separately), validate ids.

    Self-loops are infeasible under the rank constraints and are rendered
    client-side from the node rect, so they never enter the pipeline.
    """
    seen_nodes: dict[str, LayoutNode] = {}
    for node in nodes:
        seen_nodes.setdefault(node.id, node)
    known = set(seen_nodes)

    kept: list[tuple[str, str]] = []
    self_loops: list[tuple[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for source, target in edges:
        if source not in known or target not in known:
            raise ValueError(f"edge ({source!r}, {target!r}) references an unknown node id")
        if (source, target) in seen_edges:
            continue
        seen_edges.add((source, target))
        if source == target:
            self_loops.append((source, target))
        else:
            kept.append((source, target))

    for terminal in (start_id, end_id):
        if terminal is not None and terminal not in known:
            raise ValueError(f"terminal id {terminal!r} is not in the node list")

    graph = LayoutGraph(
        nodes=tuple(seen_nodes.values()),
        edges=tuple(kept),
        start_id=start_id,
        end_id=end_id,
    )
    return graph, self_loops
