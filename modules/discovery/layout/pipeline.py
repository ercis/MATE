"""Pipeline orchestrator — the pickle-friendly façade the route worker calls.

Everything in and out is plain dicts/lists/tuples so a `ctx.run_in_process`
worker can hand the result straight back across the process boundary.
"""

from __future__ import annotations

import time
from typing import Any

from .backbone import Variant, extract_backbone
from .balance import (
    assign_sides_greedy,
    assign_sides_subset_sum,
    balance_summary,
    components,
    initial_orders,
)
from .channels import inflate_channels
from .crossmin import minimize_crossings
from .mennens import mennens_ranks
from .metrics import quality_metrics, route_metrics
from .model import (
    LAYOUT_VERSION,
    EdgeRoute,
    LayoutGraph,
    LayoutNode,
    LayoutOptions,
    PortRef,
    SolverInfo,
    normalize_graph,
)
from .obstacles import ObstacleField, build_field
from .ports import Port
from .position import place
from .rank_ip import solve_ranks
from .router import RouteResult, route_edges
from .virtual import Expanded, insert_virtual_nodes

# A single channel may grow to this multiple of the uniform pitch before the
# router has to make do — past it the drawing gets taller than it is readable.
_MAX_CHANNEL_PITCH = 3.0


def _empty_response(algorithm: str, status: str) -> dict[str, Any]:
    return {
        "kind": "dfg_layout",
        "version": LAYOUT_VERSION,
        "algorithm": algorithm,
        "x": {},
        "y": {},
        "rank": {},
        "order": {},
        "edges": [],
        "metrics": {},
        "solver": {"status": status, "wall_ms": 0.0, "objective": None},
        "wall_ms": 0.0,
    }


def _rank_stage(
    graph: LayoutGraph,
    backbone: list[str] | None,
    variants: list[Variant],
    opts: LayoutOptions,
) -> tuple[dict[str, int], SolverInfo, bool]:
    """Ranks + solver status + whether the backbone treatment stays on.

    The backbone algorithm degrades explicitly instead of failing: no usable
    backbone → the whole layout falls back to the heuristic pipeline; too many
    nodes for an interactive IP solve → heuristic ranks under a still-straight
    backbone.
    """
    if opts.algorithm == "sugiyama":
        started = time.perf_counter()
        ranks = mennens_ranks(graph, variants)
        wall = (time.perf_counter() - started) * 1000.0
        return ranks, SolverInfo("heuristic", wall, None), False

    if backbone is None:
        started = time.perf_counter()
        ranks = mennens_ranks(graph, variants)
        wall = (time.perf_counter() - started) * 1000.0
        return ranks, SolverInfo("fallback_no_backbone", wall, None), False

    if len(graph.nodes) > opts.max_ip_nodes:
        started = time.perf_counter()
        ranks = mennens_ranks(graph, variants)
        wall = (time.perf_counter() - started) * 1000.0
        return ranks, SolverInfo("fallback_size_cap", wall, None), True

    ranks, info = solve_ranks(graph, backbone, opts)
    return ranks, info, True


def _routing_field(
    expanded: Expanded,
    graph: LayoutGraph,
    centers_x: dict[str, float],
    centers_y: dict[str, float],
    sizes: dict[str, tuple[float, float]],
    opts: LayoutOptions,
) -> ObstacleField:
    return build_field(
        graph.node_ids(),
        expanded.ranks,
        centers_x,
        centers_y,
        sizes,
        clearance=opts.route_clearance,
        fallback_channel_h=opts.v_gap,
    )


def _widen_channels(
    expanded: Expanded,
    graph: LayoutGraph,
    centers_x: dict[str, float],
    centers_y: dict[str, float],
    sizes: dict[str, tuple[float, float]],
    opts: LayoutOptions,
) -> dict[str, float]:
    """Route once to learn how many tracks each channel wants, then buy the room.

    Only y moves, only ever upward, and only in channels that are actually
    congested — so the drawing keeps the v1 pitch wherever it can and the
    layout-mode morph stays intact.
    """
    field = _routing_field(expanded, graph, centers_x, centers_y, sizes, opts)
    probe = route_edges(
        expanded,
        list(graph.edges),
        graph.edge_set(),
        centers_x,
        centers_y,
        sizes,
        field,
        opts,
        fillet=False,
    )
    if not probe.channel_needs:
        return centers_y

    heights = [height for _width, height in sizes.values()]
    y_pitch = (max(heights) if heights else 59.0) + opts.v_gap
    row_heights: dict[int, float] = {}
    for node, (_width, height) in sizes.items():
        rank = expanded.ranks[node]
        row_heights[rank] = max(row_heights.get(rank, 0.0), height)

    rank_y = inflate_channels(
        sorted(set(expanded.ranks.values())),
        row_heights,
        probe.channel_needs,
        y_pitch=y_pitch,
        max_channel_h=_MAX_CHANNEL_PITCH * y_pitch,
    )
    return {node: rank_y[rank] for node, rank in expanded.ranks.items()}


def _port_ref(port: Port | None) -> PortRef | None:
    if port is None:
        return None
    return PortRef(face=port.face, u=port.u, x=port.x, y=port.y)


def _port_payload(port: PortRef | None) -> dict[str, Any] | None:
    if port is None:
        return None
    return {
        "face": port.face,
        "u": round(port.u, 4),
        "x": round(port.x, 2),
        "y": round(port.y, 2),
    }


def _serialize_route(route: EdgeRoute) -> dict[str, Any]:
    """The v1 shape, plus v2's geometry only when the router produced it."""
    payload: dict[str, Any] = {
        "source": route.source,
        "target": route.target,
        "waypoints": [[round(px, 2), round(py, 2)] for px, py in route.waypoints],
        "self_loop": route.self_loop,
        "back_edge": route.back_edge,
        "bidirectional": route.bidirectional,
    }
    if route.path is None:
        return payload
    payload.update(
        {
            "path": route.path,
            "polyline": [[round(px, 2), round(py, 2)] for px, py in (route.polyline or [])],
            "source_port": _port_payload(route.source_port),
            "target_port": _port_payload(route.target_port),
            "arrow": [round(value, 2) for value in route.arrow] if route.arrow else None,
            "label_at": [round(value, 2) for value in route.label_at] if route.label_at else None,
            "bends": route.bends,
            "min_radius": None if route.min_radius is None else round(route.min_radius, 2),
        }
    )
    return payload


def compute_layout(
    nodes: list[dict[str, Any]],
    edges: list[Any],
    variants: list[Any],
    start_id: str | None,
    end_id: str | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    opts = LayoutOptions.from_dict(options)

    layout_nodes = [
        LayoutNode(
            id=str(node["id"]),
            width=float(node.get("width", 220.0)),
            height=float(node.get("height", 59.0)),
        )
        for node in nodes
    ]
    request_edges = [(str(edge[0]), str(edge[1])) for edge in edges]
    graph, _self_loops = normalize_graph(layout_nodes, request_edges, start_id, end_id)
    clean_variants: list[Variant] = [
        ([str(activity) for activity in sequence], int(count)) for sequence, count in variants
    ]

    if not graph.nodes:
        return _empty_response(opts.algorithm, "empty")
    if len(graph.nodes) == 1:
        only = graph.nodes[0]
        response = _empty_response(opts.algorithm, "trivial")
        response["x"] = {only.id: 0.0}
        response["y"] = {only.id: 0.0}
        response["rank"] = {only.id: 1}
        response["order"] = {only.id: 0}
        response["edges"] = [
            {
                "source": source,
                "target": target,
                "waypoints": [],
                "self_loop": True,
                "back_edge": False,
                "bidirectional": False,
            }
            for source, target in request_edges
        ]
        response["wall_ms"] = (time.perf_counter() - started) * 1000.0
        return response

    backbone = extract_backbone(graph, clean_variants)
    ranks, solver_info, backbone_mode = _rank_stage(graph, backbone, clean_variants, opts)

    expanded = insert_virtual_nodes(graph, ranks, backbone if backbone_mode else [])
    component_list = components(expanded)
    if backbone_mode:
        sides = assign_sides_subset_sum(component_list)
    else:
        sides = assign_sides_greedy(component_list)
    orders = initial_orders(expanded, component_list, sides)
    orders = minimize_crossings(
        expanded, orders, pinned=backbone_mode, iterations=opts.crossmin_iterations
    )

    sizes = graph.sizes()
    centers_x, centers_y = place(expanded, orders, sizes, opts, straight_backbone=backbone_mode)

    v2 = opts.algorithm == "backbone-v2"
    if v2:
        centers_y = _widen_channels(expanded, graph, centers_x, centers_y, sizes, opts)

    # Normalize to a (0,0) top-left origin; virtual waypoints shift alongside.
    def _half(node: str, axis: int) -> float:
        if node in sizes:
            return sizes[node][axis] / 2.0
        return 0.5

    shift_x = min(centers_x[node] - _half(node, 0) for node in centers_x)
    shift_y = min(centers_y[node] - _half(node, 1) for node in centers_y)

    # v2 routes in FINAL coordinates: an emitted SVG path cannot be translated
    # after the fact without re-parsing it.
    routed: RouteResult | None = None
    routing_field: ObstacleField | None = None
    route_ms = 0.0
    if v2:
        route_started = time.perf_counter()
        final_x = {node: value - shift_x for node, value in centers_x.items()}
        final_y = {node: value - shift_y for node, value in centers_y.items()}
        routing_field = _routing_field(expanded, graph, final_x, final_y, sizes, opts)
        routed = route_edges(
            expanded,
            list(graph.edges),
            graph.edge_set(),
            final_x,
            final_y,
            sizes,
            routing_field,
            opts,
            fillet=True,
            # The budget bounds the ROUTER, not the request: the rank IP has its
            # own `time_limit_s` and would otherwise eat this one.
            deadline=route_started + opts.route_budget_ms / 1000.0,
        )
        route_ms = (time.perf_counter() - route_started) * 1000.0

    left_count, right_count = balance_summary(component_list, sides)
    drawn: dict[tuple[str, str], list[tuple[float, float]]] | None = None
    if routed is not None:
        drawn = {key: list(route.points) for key, route in routed.routes.items()}
    metric_values = quality_metrics(
        expanded,
        orders,
        centers_x,
        centers_y,
        list(graph.edges),
        left_count,
        right_count,
        paper_compat=opts.paper_compat_metrics,
        segments=drawn,
    )
    if routed is not None and routing_field is not None:
        metric_values.update(route_metrics(routed.routes, routing_field, repairs=routed.repairs))

    edge_set = graph.edge_set()
    routes: list[EdgeRoute] = []
    for source, target in request_edges:
        if source == target:
            routes.append(EdgeRoute(source=source, target=target, self_loop=True))
            continue
        back_edge = ranks[source] > ranks[target]
        bidirectional = (target, source) in edge_set
        routed_edge = routed.routes.get((source, target)) if routed is not None else None
        if routed_edge is not None and routed_edge.geometry is not None:
            geometry = routed_edge.geometry
            routes.append(
                EdgeRoute(
                    source=source,
                    target=target,
                    waypoints=list(geometry.polyline[1:-1]),
                    back_edge=back_edge,
                    bidirectional=bidirectional,
                    path=geometry.path,
                    polyline=list(geometry.polyline),
                    source_port=_port_ref(routed_edge.port_s),
                    target_port=_port_ref(routed_edge.port_t),
                    arrow=geometry.arrow,
                    label_at=geometry.label_at,
                    bends=routed_edge.bends,
                    min_radius=geometry.min_radius,
                )
            )
            continue
        chain = expanded.chains.get((source, target), [source, target])
        waypoints = [(centers_x[node] - shift_x, centers_y[node] - shift_y) for node in chain[1:-1]]
        routes.append(
            EdgeRoute(
                source=source,
                target=target,
                waypoints=waypoints,
                back_edge=back_edge,
                bidirectional=bidirectional,
            )
        )

    real_ids = graph.node_ids()
    response: dict[str, Any] = {
        "kind": "dfg_layout",
        "version": LAYOUT_VERSION,
        "algorithm": opts.algorithm,
        "x": {n: centers_x[n] - sizes[n][0] / 2.0 - shift_x for n in real_ids},
        "y": {n: centers_y[n] - sizes[n][1] / 2.0 - shift_y for n in real_ids},
        "rank": {n: int(ranks[n]) for n in real_ids},
        "order": {n: int(orders[n]) for n in real_ids},
        "edges": [_serialize_route(route) for route in routes],
        "metrics": {key: round(value, 6) for key, value in metric_values.items()},
        "solver": {
            "status": solver_info.status,
            "wall_ms": round(solver_info.wall_ms, 2),
            "objective": solver_info.objective,
        },
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }
    if v2:
        response["route_ms"] = round(route_ms, 2)
    return response
