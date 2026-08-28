"""Obstacle-aware edge routing for backbone-v2.

Runs after `position.place()` and replaces v1's "waypoints are the virtual-node
centres" step. Three requirements drive it: edges attach anywhere on a node's
border, edges never pass under a node, and edges bend as little as possible.

The layered geometry makes all three cheap. `place()` puts every node of a rank
on one shared centre y, so:

* the band between two consecutive rank rows is entirely node-free, and
* the column at a virtual node's x is clear by ~``h_gap`` inside its row.

A route that only runs vertically in virtual/own-node columns and horizontally
inside channels is therefore **safe by construction** — no collision test at
all. Obstacle queries exist for the optimistic shortcuts (which can be better
than safe) and for the repair path.

Bends are minimized by choosing *where on the border* an edge attaches:

* If the source, the target and every virtual on the chain share a free x, both
  ports sit on that x and the whole edge is one straight vertical run — **0
  bends**. This is also why the backbone spine stays straight: `_packcut_backbone`
  gives every spine node one shared centre x, so the intersection is the whole
  spine column. No special case needed.
* A unit edge with a large horizontal offset leaves through a *side* face and
  turns once (**1 bend**) instead of stepping through the channel (2 bends).
* Same-rank edges — which v1 drew straight through every node in between — go
  side-to-side when adjacent (**0 bends**) or dip through the channel below
  (**2 bends**) when not.

Ordering of the stages below matters: ports must be placed before skeletons
(the skeleton starts at a port), and skeletons before track assignment (tracks
colour the horizontal runs the skeletons produced).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from heapq import heappop, heappush
from statistics import median

from .fillet import FilletResult, fillet_path
from .geom import EPS, Face, Point, Rect, clamp, collapse
from .model import LayoutOptions
from .obstacles import ObstacleField
from .ports import (
    PRIORITY_BACKBONE,
    PRIORITY_OTHER,
    PRIORITY_STRAIGHT,
    PRIORITY_UNIT,
    Port,
    PortRequest,
    assign_ports,
    face_capacity,
    face_span,
)
from .virtual import Expanded

# Sentinel ranks for the half-open channels above the first row and below the
# last one. Real ranks never come close to these magnitudes.
_SYNTH_ABOVE = -(10**9)
_SYNTH_BELOW = 10**9

ChannelKey = tuple[int, int]

# Horizontal clearance a side port takes before it may turn. Sized under the
# `h_gap` (60) a row keeps between neighbours, so the stub is always free.
_SIDE_STUB = 24.0

# Cross-channel breathing room reserved above and below the track band.
_CHANNEL_MARGIN = 10.0

# Above this many expanded nodes the optimistic shortcuts and the fillet's
# curvature work stop paying for themselves; the safe staircase is kept.
_HUGE_NODE_COUNT = 1500


@dataclass
class RoutedEdge:
    """One routed edge: the skeleton, its ports, and (later) its geometry."""

    source: str
    target: str
    points: list[Point]
    port_s: Port | None = None
    port_t: Port | None = None
    kind: str = "stair"
    bends: int = 0
    repairs: int = 0
    geometry: FilletResult | None = None


@dataclass
class RouteResult:
    routes: dict[tuple[str, str], RoutedEdge]
    channel_needs: dict[int, float]
    repairs: int = 0
    fallbacks: int = 0


@dataclass
class _Draft:
    """An edge's routing decision, before ports are actually placed."""

    key: tuple[str, str]
    chain: list[str]
    mode: str  # straight | stair | side_exit | side_entry | row_direct | row_u
    face_s: Face
    face_t: Face
    ideal_s: float
    ideal_t: float
    rigid: bool
    priority: int
    lane: str  # down | back
    row_channel: ChannelKey | None = None


@dataclass
class _Run:
    """A horizontal segment waiting for a track."""

    key: ChannelKey
    lane: str
    x0: float
    x1: float
    edge: tuple[str, str]
    index: int  # points[index] and points[index + 1] are the run
    order: int = 0


def route_edges(
    expanded: Expanded,
    edges: list[tuple[str, str]],
    edge_set: set[tuple[str, str]],
    centers_x: dict[str, float],
    centers_y: dict[str, float],
    sizes: dict[str, tuple[float, float]],
    field: ObstacleField,
    opts: LayoutOptions,
    *,
    fillet: bool,
    deadline: float | None = None,
) -> RouteResult:
    """Route every (deduped, self-loop-free) edge. Self-loops are the caller's."""
    rects = {
        node: Rect.from_center(centers_x[node], centers_y[node], width, height)
        for node, (width, height) in sizes.items()
        if node in centers_x
    }
    huge = len(expanded.ranks) > _HUGE_NODE_COUNT
    row_index = {
        rank: {node: position for position, node in enumerate(row.ids)}
        for rank, row in field.rows.items()
    }

    drafts = [
        _classify(
            source,
            target,
            expanded,
            edge_set,
            rects,
            centers_x,
            field,
            row_index,
            opts,
            allow_upgrade=not huge,
        )
        for source, target in edges
        if source in rects and target in rects
    ]

    _limit_side_faces(drafts, rects, opts.port_gap)

    requests: list[PortRequest] = []
    spillable: set[tuple[tuple[str, str], str]] = set()
    for draft in drafts:
        requests.append(
            PortRequest(
                key=draft.key,
                endpoint="s",
                node=draft.key[0],
                face=draft.face_s,
                ideal=draft.ideal_s,
                rigid=draft.rigid,
                priority=draft.priority,
            )
        )
        requests.append(
            PortRequest(
                key=draft.key,
                endpoint="t",
                node=draft.key[1],
                face=draft.face_t,
                ideal=draft.ideal_t,
                rigid=draft.rigid,
                priority=draft.priority,
            )
        )
        if draft.mode == "stair":
            spillable.add((draft.key, "s"))
            spillable.add((draft.key, "t"))

    ports = assign_ports(requests, rects, port_gap=opts.port_gap, spillable=spillable)

    routes: dict[tuple[str, str], RoutedEdge] = {}
    runs: list[_Run] = []
    for draft in drafts:
        port_s = ports[(draft.key, "s")]
        port_t = ports[(draft.key, "t")]
        points, edge_runs = _skeleton(draft, port_s, port_t, expanded, centers_x, field)
        routes[draft.key] = RoutedEdge(
            source=draft.key[0],
            target=draft.key[1],
            points=points,
            port_s=port_s,
            port_t=port_t,
            kind=draft.mode,
        )
        runs.extend(edge_runs)

    channel_needs = _assign_tracks(runs, routes, field, opts)

    result = RouteResult(routes=routes, channel_needs=channel_needs)
    for draft in drafts:
        route = routes[draft.key]
        repaired = _repair(route, draft, expanded, centers_x, field, opts)
        result.repairs += repaired
        route.points = collapse(route.points)
        route.bends = max(0, len(route.points) - 2)

    if fillet:
        for key, route in routes.items():
            # Out of time: emit the skeleton unrounded rather than fail. The
            # route is still correct, it just has square corners.
            spent = deadline is not None and time.perf_counter() > deadline
            if spent:
                result.fallbacks += 1
            route.geometry = fillet_path(
                route.points,
                field,
                r_max=0.0 if (huge or spent) else opts.max_fillet_radius,
                merge_len=opts.merge_len,
                # The field's rects are already inflated by `route_clearance`;
                # one more pixel is enough headroom for the arc.
                clearance_margin=1.0,
                arrow_gap=opts.arrow_gap,
                ignore=frozenset(key),
            )
    return result


# -- classification --------------------------------------------------------


def _classify(
    source: str,
    target: str,
    expanded: Expanded,
    edge_set: set[tuple[str, str]],
    rects: dict[str, Rect],
    centers_x: dict[str, float],
    field: ObstacleField,
    row_index: dict[int, dict[str, int]],
    opts: LayoutOptions,
    *,
    allow_upgrade: bool,
) -> _Draft:
    key = (source, target)
    chain = expanded.chains.get(key, [source, target])
    rank_s = expanded.ranks[source]
    rank_t = expanded.ranks[target]
    src = rects[source]
    tgt = rects[target]

    # A reverse edge exists: split the pair so the two directions draw as
    # parallel lines instead of one on top of the other.
    bow = 0.0
    if (target, source) in edge_set:
        bow = (opts.pair_bow / 2.0) * (1.0 if source < target else -1.0)

    if rank_s == rank_t:
        return _classify_same_rank(key, chain, src, tgt, rank_s, field, row_index, bow)

    down = rank_t > rank_s
    face_s: Face = "bottom" if down else "top"
    face_t: Face = "top" if down else "bottom"
    lane = "down" if down else "back"

    interval = _straight_interval(chain, src, tgt, face_s, face_t, field, expanded, centers_x)
    if interval is not None:
        centre = median([centers_x[node] for node in chain])
        x_star = clamp(centre + bow, interval[0], interval[1])
        on_backbone = source in expanded.backbone_set and target in expanded.backbone_set
        return _Draft(
            key=key,
            chain=chain,
            mode="straight",
            face_s=face_s,
            face_t=face_t,
            ideal_s=x_star,
            ideal_t=x_star,
            rigid=True,
            priority=PRIORITY_BACKBONE if on_backbone else PRIORITY_STRAIGHT,
            lane=lane,
        )

    unit = abs(rank_t - rank_s) == 1
    if allow_upgrade and unit:
        upgrade = _corner_upgrade(key, src, tgt, down, field, bow)
        if upgrade is not None:
            return upgrade

    # Aim each port at the guide x the edge heads for: the shorter the first
    # and last jog, the less the staircase has to travel sideways.
    ideal_s = centers_x[chain[1]] if len(chain) > 2 else tgt.cx
    ideal_t = centers_x[chain[-2]] if len(chain) > 2 else src.cx
    return _Draft(
        key=key,
        chain=chain,
        mode="stair",
        face_s=face_s,
        face_t=face_t,
        ideal_s=ideal_s + bow,
        ideal_t=ideal_t + bow,
        rigid=False,
        priority=PRIORITY_UNIT if unit else PRIORITY_OTHER,
        lane=lane,
    )


def _classify_same_rank(
    key: tuple[str, str],
    chain: list[str],
    src: Rect,
    tgt: Rect,
    rank: int,
    field: ObstacleField,
    row_index: dict[int, dict[str, int]],
    bow: float,
) -> _Draft:
    """Same-rank edges — v1 drew these straight through everything between."""
    positions = row_index.get(rank, {})
    left = positions.get(key[0])
    right = positions.get(key[1])
    adjacent = left is not None and right is not None and abs(left - right) == 1

    if adjacent:
        # Nothing in between: a plain side-to-side horizontal, zero bends.
        face_s: Face = "right" if tgt.cx > src.cx else "left"
        face_t: Face = "left" if tgt.cx > src.cx else "right"
        return _Draft(
            key=key,
            chain=chain,
            mode="row_direct",
            face_s=face_s,
            face_t=face_t,
            ideal_s=src.cy + bow,
            ideal_t=tgt.cy + bow,
            rigid=True,
            priority=PRIORITY_STRAIGHT,
            lane="down",
        )

    # Otherwise dip through a node-free channel and come back up. Side faces
    # would cost four corners; this costs two and reads as one wide sweep.
    below = field.has_channel_below(rank)
    face: Face = "bottom" if below else "top"
    channel: ChannelKey = (
        _channel_key(rank, _next_rank(field, rank)) if below else (_SYNTH_ABOVE, rank)
    )
    return _Draft(
        key=key,
        chain=chain,
        mode="row_u",
        face_s=face,
        face_t=face,
        ideal_s=src.cx + bow,
        ideal_t=tgt.cx + bow,
        rigid=False,
        priority=PRIORITY_OTHER,
        lane="down",
        row_channel=channel,
    )


def _limit_side_faces(drafts: list[_Draft], rects: dict[str, Rect], port_gap: float) -> None:
    """Undo corner upgrades that would over-subscribe a flank.

    A side route's long horizontal run sits inside a *row band*, not a channel,
    so it gets no track — two of them at the same port y would draw on top of
    each other. A terminal pill offers ~19px of usable flank, which is room for
    one or two ports; anything past that is better off as a staircase, where
    the run lands in a channel and the track colouring separates it.
    """
    demand: dict[tuple[str, Face], list[_Draft]] = {}
    for draft in drafts:
        if draft.mode == "side_exit":
            demand.setdefault((draft.key[0], draft.face_s), []).append(draft)
        elif draft.mode == "side_entry":
            demand.setdefault((draft.key[1], draft.face_t), []).append(draft)

    for (node, face), group in demand.items():
        capacity = face_capacity(rects[node], face, port_gap)
        if len(group) <= capacity:
            continue
        group.sort(key=lambda draft: draft.key)
        for draft in group[capacity:]:
            down = draft.lane == "down"
            source, target = draft.key
            draft.mode = "stair"
            draft.face_s = "bottom" if down else "top"
            draft.face_t = "top" if down else "bottom"
            draft.ideal_s = rects[target].cx
            draft.ideal_t = rects[source].cx
            draft.rigid = False
            draft.priority = PRIORITY_UNIT


def _straight_interval(
    chain: list[str],
    src: Rect,
    tgt: Rect,
    face_s: Face,
    face_t: Face,
    field: ObstacleField,
    expanded: Expanded,
    centers_x: dict[str, float],
) -> tuple[float, float] | None:
    """The x values at which this edge draws as one straight vertical line.

    Both node faces must reach the x, and every rank the edge crosses must be
    free there. Empty result → the edge needs at least one bend.
    """
    lo_s, hi_s = face_span(src, face_s)
    lo_t, hi_t = face_span(tgt, face_t)
    lo = max(lo_s, lo_t)
    hi = min(hi_s, hi_t)
    if hi < lo:
        return None
    for node in chain[1:-1]:
        interval = field.free_interval(expanded.ranks[node], centers_x[node])
        if interval is None:
            return None
        lo = max(lo, interval[0])
        hi = min(hi, interval[1])
        if hi < lo:
            return None
    return (lo, hi)


def _corner_upgrade(
    key: tuple[str, str],
    src: Rect,
    tgt: Rect,
    down: bool,
    field: ObstacleField,
    bow: float,
) -> _Draft | None:
    """Trade the 2-corner staircase for a 1-corner side route where it fits.

    Only for adjacent ranks, and only when the two nodes do not overlap in x
    (they would already be straight if they did). No explicit cap is needed on
    how many of these a row may take: the horizontal run is tested against the
    row itself, so only a node with a clear path that way can have one.
    """
    right = tgt.cx > src.cx
    if right and tgt.x0 - src.x1 < EPS:
        return None
    if not right and src.x0 - tgt.x1 < EPS:
        return None

    ignore = frozenset(key)
    entry_y = tgt.y0 if down else tgt.y1
    exit_x = src.x1 if right else src.x0
    face_s: Face = "right" if right else "left"

    # Side exit: leave through the source's flank, then a single turn down.
    if field.horizontal_clear(src.cy, exit_x, tgt.cx, ignore) and field.vertical_clear(
        tgt.cx, src.cy, entry_y, ignore
    ):
        return _Draft(
            key=key,
            chain=[key[0], key[1]],
            mode="side_exit",
            face_s=face_s,
            face_t="top" if down else "bottom",
            ideal_s=src.cy + bow,
            ideal_t=tgt.cx + bow,
            rigid=True,
            priority=PRIORITY_UNIT,
            lane="down" if down else "back",
        )

    # Side entry: drop straight out of the source, then turn into the flank.
    face_t: Face = "left" if right else "right"
    entry_x = tgt.x0 if right else tgt.x1
    leave_y = src.y1 if down else src.y0
    if field.vertical_clear(src.cx, leave_y, tgt.cy, ignore) and field.horizontal_clear(
        tgt.cy, src.cx, entry_x, ignore
    ):
        return _Draft(
            key=key,
            chain=[key[0], key[1]],
            mode="side_entry",
            face_s="bottom" if down else "top",
            face_t=face_t,
            ideal_s=src.cx + bow,
            ideal_t=tgt.cy + bow,
            rigid=True,
            priority=PRIORITY_UNIT,
            lane="down" if down else "back",
        )
    return None


# -- skeletons -------------------------------------------------------------


def _skeleton(
    draft: _Draft,
    port_s: Port,
    port_t: Port,
    expanded: Expanded,
    centers_x: dict[str, float],
    field: ObstacleField,
    *,
    guide_override: list[float] | None = None,
) -> tuple[list[Point], list[_Run]]:
    """Points from source port to target port, with channel ys still placeholder."""
    start: Point = (port_s.x, port_s.y)
    end: Point = (port_t.x, port_t.y)

    if draft.mode in ("straight", "row_direct"):
        # Both ports were proven to share a free column (or a free row), so the
        # guide xs of the chain are irrelevant — following them would only add
        # a pair of corners around each virtual node.
        return [start, end], []

    if draft.mode == "row_u":
        key = draft.row_channel or (_SYNTH_ABOVE, expanded.ranks[draft.key[0]])
        y_lo, y_hi = _channel_bounds(field, key)
        y = (y_lo + y_hi) / 2.0
        points = [start, (start[0], y), (end[0], y), end]
        return points, [_Run(key, draft.lane, start[0], end[0], draft.key, 1)]

    chain = draft.chain
    guide = guide_override or ([port_s.x] + [centers_x[node] for node in chain[1:-1]] + [port_t.x])
    last = len(chain) - 1

    points: list[Point] = [start]
    runs: list[_Run] = []
    current_x = port_s.x

    if port_s.is_vertical_face:
        # A side port must clear the node horizontally before it may turn.
        direction = 1.0 if port_s.face == "right" else -1.0
        wanted = guide[1] if len(guide) > 1 else port_s.x + direction * _SIDE_STUB
        if (wanted - port_s.x) * direction < _SIDE_STUB:
            wanted = port_s.x + direction * _SIDE_STUB
        points.append((wanted, port_s.y))
        current_x = wanted

    stop = last - 1 if port_t.is_vertical_face else last
    for level in range(1, stop + 1):
        target_x = guide[level]
        if abs(target_x - current_x) <= EPS:
            continue
        key = _channel_key(expanded.ranks[chain[level - 1]], expanded.ranks[chain[level]])
        y_lo, y_hi = _channel_bounds(field, key)
        y = (y_lo + y_hi) / 2.0
        runs.append(_Run(key, draft.lane, current_x, target_x, draft.key, len(points)))
        points.append((current_x, y))
        points.append((target_x, y))
        current_x = target_x

    if port_t.is_vertical_face:
        points.append((current_x, port_t.y))
    points.append(end)
    return points, runs


def _channel_key(rank_a: int, rank_b: int) -> ChannelKey:
    if rank_a == rank_b:
        return (rank_a, _SYNTH_BELOW)
    return (rank_a, rank_b) if rank_a < rank_b else (rank_b, rank_a)


def _channel_bounds(field: ObstacleField, key: ChannelKey) -> tuple[float, float]:
    upper, lower = key
    if upper == _SYNTH_ABOVE:
        return field.channel_above(lower)
    if lower == _SYNTH_BELOW:
        return field.channel_below(upper)
    return field.channel_between(upper, lower)


def _next_rank(field: ObstacleField, rank: int) -> int:
    index = field.ranks.index(rank)
    return field.ranks[index + 1] if index + 1 < len(field.ranks) else rank


# -- track assignment ------------------------------------------------------


def _assign_tracks(
    runs: list[_Run],
    routes: dict[tuple[str, str], RoutedEdge],
    field: ObstacleField,
    opts: LayoutOptions,
) -> dict[int, float]:
    """Give every horizontal run its own track where runs would overlap.

    Optimal for an interval graph (greedy by left endpoint), and back edges get
    the upper half of each channel so the red bundle separates for free.
    """
    by_channel: dict[ChannelKey, list[_Run]] = {}
    for run in runs:
        by_channel.setdefault(run.key, []).append(run)

    needs: dict[int, float] = {}
    for key, channel_runs in by_channel.items():
        back = [run for run in channel_runs if run.lane == "back"]
        down = [run for run in channel_runs if run.lane != "back"]
        back_tracks = _colour(back)
        down_tracks = _colour(down)
        total = back_tracks + down_tracks
        if total <= 0:
            continue

        y_lo, y_hi = _channel_bounds(field, key)
        usable = max(0.0, (y_hi - y_lo) - 2.0 * _CHANNEL_MARGIN)
        step = usable / (total + 1)
        for run in channel_runs:
            index = run.order if run.lane == "back" else back_tracks + run.order
            y = y_lo + _CHANNEL_MARGIN + (index + 1) * step
            route = routes[run.edge]
            route.points[run.index] = (route.points[run.index][0], y)
            route.points[run.index + 1] = (route.points[run.index + 1][0], y)

        upper, lower = key
        if upper != _SYNTH_ABOVE and lower != _SYNTH_BELOW:
            needs[upper] = max(
                needs.get(upper, 0.0),
                2.0 * _CHANNEL_MARGIN + (total + 1) * opts.min_track_gap,
            )
    return needs


def _colour(runs: list[_Run]) -> int:
    """Assign ``run.order`` a track index; returns how many tracks were used.

    Wider runs are laid down first so nested ones sit inside them — an onion
    rather than a braid, which keeps the vertical stubs from crossing.
    """
    if not runs:
        return 0
    ordered = sorted(runs, key=lambda run: (min(run.x0, run.x1), -abs(run.x1 - run.x0), run.edge))
    free: list[tuple[float, int]] = []
    used = 0
    for run in ordered:
        left = min(run.x0, run.x1)
        right = max(run.x0, run.x1)
        if free and free[0][0] <= left:
            _end, index = heappop(free)
            run.order = index
        else:
            run.order = used
            used += 1
        heappush(free, (right, run.order))
    return used


# -- validation and repair -------------------------------------------------


def _repair(
    route: RoutedEdge,
    draft: _Draft,
    expanded: Expanded,
    centers_x: dict[str, float],
    field: ObstacleField,
    opts: LayoutOptions,
) -> int:
    """Descend the safety lattice until the skeleton is clear.

    Level 0 (straight / side / direct) is optimistic and validated here; level 1
    (staircase, U) is safe by construction; level 2 shifts blocked guide columns
    to the nearest free one. Each step strictly descends, so this terminates —
    and level 2 always exists because every row's free-interval list is
    unbounded at both ends.
    """
    ignore = frozenset(draft.key)
    if _clear(route.points, field, ignore):
        return 0

    assert route.port_s is not None and route.port_t is not None
    repairs = 0
    if draft.mode != "stair":
        # Level 1: the generic staircase. Every vertical run sits in a virtual
        # column or the node's own x-span, every horizontal one in a channel,
        # and a side port gets a stub before it turns — all free by
        # construction, so this only fails if an invariant broke upstream.
        fallback = _Draft(
            key=draft.key,
            chain=draft.chain,
            mode="stair",
            face_s=draft.face_s,
            face_t=draft.face_t,
            ideal_s=draft.ideal_s,
            ideal_t=draft.ideal_t,
            rigid=False,
            priority=draft.priority,
            lane=draft.lane,
            row_channel=draft.row_channel,
        )
        points, _runs = _skeleton(fallback, route.port_s, route.port_t, expanded, centers_x, field)
        route.points = points
        route.kind = fallback.mode
        repairs = 1
        if _clear(points, field, ignore):
            return repairs

    # Level 2: push every intermediate guide column into free space.
    chain = draft.chain
    guide = [route.port_s.x]
    for node in chain[1:-1]:
        rank = expanded.ranks[node]
        guide.append(field.nearest_free_x(rank, centers_x[node], width=opts.port_gap))
    guide.append(route.port_t.x)
    points, _runs = _skeleton(
        draft, route.port_s, route.port_t, expanded, centers_x, field, guide_override=guide
    )
    route.points = points
    route.kind = "stair"
    return repairs + 1


def _clear(points: list[Point], field: ObstacleField, ignore: frozenset[str]) -> bool:
    for index in range(len(points) - 1):
        ax, ay = points[index]
        bx, by = points[index + 1]
        if not field.segment_clear(ax, ay, bx, by, ignore):
            return False
    return True


def overlap_samples(route: RoutedEdge, field: ObstacleField, ignore: frozenset[str]) -> int:
    """How many emitted curve samples sit inside a node. The hard target is 0."""
    geometry = route.geometry
    samples = geometry.samples if geometry is not None else route.points
    return sum(1 for x, y in samples if field.stab_point(x, y, ignore) is not None)
