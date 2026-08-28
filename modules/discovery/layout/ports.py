"""Port placement on node borders (backbone-v2).

Every edge endpoint asks for a coordinate on one of the node's four faces. Most
get exactly what they ask for: the router's *ideal* coordinate is the one that
makes the edge straight, so honouring it is what produces zero-bend edges.
Ports only move when several edges crowd the same face.

Two rounds, and the order is the point:

1. **Rigid first.** Endpoints of edges the router proved straight (backbone
   chain first, then any other chain-straight edge, then unit edges) claim their
   exact ideal. A crowd on the same face can no longer bend the spine.
2. **Everyone else fills in.** The remaining endpoints are placed by
   minimum-displacement isotonic regression (PAVA) with a ``port_gap``
   separation, inside the sub-intervals the rigid ports left over. A single
   endpoint on a face reproduces its ideal exactly.

This is the server-side counterpart of the anchor pass in
`panel/layout/celonis-flow.ts` (``pushFace`` + even spread), with the one fix
that matters here: that pass discards the ideal coordinates and always spreads
evenly, so it cannot keep a lone edge straight when the face is busy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geom import Face, Rect, clamp

# Keep ports off the corners: a port at the very edge of a face makes the
# arrowhead overlap the adjacent border. Mirrors celonis-flow's 20/12 insets.
INSET_H = 20.0
INSET_V = 12.0

# Priority bands for round 1. Lower wins.
PRIORITY_BACKBONE = 0
PRIORITY_STRAIGHT = 1
PRIORITY_UNIT = 2
PRIORITY_OTHER = 3


@dataclass(frozen=True)
class Port:
    """A placed endpoint: which face, where along it, and the absolute point."""

    node: str
    face: Face
    u: float
    x: float
    y: float

    @property
    def is_vertical_face(self) -> bool:
        return self.face in ("left", "right")


@dataclass
class PortRequest:
    """One endpoint asking for a spot on ``node``'s ``face``."""

    key: tuple[str, str]
    endpoint: str  # "s" | "t"
    node: str
    face: Face
    ideal: float  # x on top/bottom faces, y on left/right
    rigid: bool = False
    priority: int = PRIORITY_OTHER


def face_span(rect: Rect, face: Face) -> tuple[float, float]:
    """The usable coordinate range along ``face`` (corners excluded)."""
    if face in ("top", "bottom"):
        half = max(0.0, rect.width / 2.0 - INSET_H)
        return (rect.cx - half, rect.cx + half)
    half = max(0.0, rect.height / 2.0 - INSET_V)
    return (rect.cy - half, rect.cy + half)


def face_capacity(rect: Rect, face: Face, port_gap: float) -> int:
    lo, hi = face_span(rect, face)
    if port_gap <= 0.0:
        return 1_000_000
    return int((hi - lo) // port_gap) + 1


def assign_ports(
    requests: list[PortRequest],
    rects: dict[str, Rect],
    *,
    port_gap: float,
    spillable: set[tuple[tuple[str, str], str]] = frozenset(),  # type: ignore[assignment]
) -> dict[tuple[tuple[str, str], str], Port]:
    """Place every request. Deterministic: ties break on ``(key, endpoint)``."""
    for (node, face), items in sorted(_group(requests).items()):
        spill_overflow(items, rects[node], face, port_gap=port_gap, eligible=spillable)

    placed: dict[tuple[tuple[str, str], str], Port] = {}
    for (node, face), items in sorted(_group(requests).items()):
        rect = rects[node]
        lo, hi = face_span(rect, face)
        for request, coordinate in _place_face(items, lo, hi, port_gap):
            placed[(request.key, request.endpoint)] = _to_port(rect, node, face, coordinate)
    return placed


def _group(requests: list[PortRequest]) -> dict[tuple[str, Face], list[PortRequest]]:
    grouped: dict[tuple[str, Face], list[PortRequest]] = {}
    for request in requests:
        grouped.setdefault((request.node, request.face), []).append(request)
    return grouped


def _place_face(
    items: list[PortRequest], lo: float, hi: float, port_gap: float
) -> list[tuple[PortRequest, float]]:
    """Round 1 (rigid at their ideal) then round 2 (PAVA into the gaps)."""
    ordered = sorted(items, key=lambda r: (r.priority, r.key, r.endpoint))

    if port_gap > 0.0 and len(ordered) > int((hi - lo) // port_gap) + 1:
        # Over-subscribed. Reserving room around the anchors would collapse the
        # sub-intervals between them, and a collapsed interval stacks every one
        # of its ports on a single coordinate — which is how two edges end up
        # drawn exactly on top of each other. Spread the whole face instead.
        by_ideal = sorted(ordered, key=lambda r: (r.ideal, r.key, r.endpoint))
        coords = distribute([request.ideal for request in by_ideal], lo, hi, port_gap)
        return list(zip(by_ideal, coords, strict=True))

    anchors: list[tuple[float, PortRequest]] = []
    for request in ordered:
        if not request.rigid:
            continue
        coordinate = clamp(request.ideal, lo, hi)
        if any(abs(coordinate - other) < port_gap for other, _ in anchors):
            continue  # a higher-priority rigid port already owns this spot
        anchors.append((coordinate, request))
    anchors.sort(key=lambda pair: (pair[0], pair[1].key))

    anchored = {id(request) for _coord, request in anchors}
    rest = [request for request in ordered if id(request) not in anchored]

    # The anchors cut the face into sub-intervals; each loose endpoint lands in
    # the one its ideal points at, then the sub-interval is packed on its own.
    bounds = [lo] + [coordinate for coordinate, _ in anchors] + [hi]
    buckets: list[list[PortRequest]] = [[] for _ in range(len(bounds) - 1)]
    for request in sorted(rest, key=lambda r: (r.ideal, r.key, r.endpoint)):
        coordinate = clamp(request.ideal, lo, hi)
        index = 0
        while index < len(buckets) - 1 and coordinate > bounds[index + 1]:
            index += 1
        buckets[index].append(request)

    out: list[tuple[PortRequest, float]] = [(request, coord) for coord, request in anchors]

    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        # Never let the reserved margin swallow the whole sub-interval: a
        # zero-width interval places every one of its ports on one coordinate.
        margin = min(port_gap, max(0.0, (bounds[index + 1] - bounds[index]) / 3.0))
        left = bounds[index] + (margin if index > 0 else 0.0)
        right = bounds[index + 1] - (margin if index + 1 < len(bounds) - 1 else 0.0)
        if right < left:
            left = right = (bounds[index] + bounds[index + 1]) / 2.0
        coords = distribute([request.ideal for request in bucket], left, right, port_gap)
        out.extend(zip(bucket, coords, strict=True))

    return out


def distribute(ideals: list[float], lo: float, hi: float, gap: float) -> list[float]:
    """Minimum-displacement placement of sorted-by-ideal points in ``[lo, hi]``.

    Pool-adjacent-violators: each point wants its ideal, consecutive points must
    stay ``gap`` apart. A single point therefore keeps its ideal exactly, which
    is what preserves a lone edge's straightness.
    """
    count = len(ideals)
    if count == 0:
        return []
    if count == 1:
        return [clamp(ideals[0], lo, hi)]

    span = hi - lo
    step = gap if span >= gap * (count - 1) else (span / (count - 1) if count > 1 else 0.0)

    # Shift to a "same position" problem: point i wants ideal_i - i*step.
    blocks: list[tuple[float, int]] = []  # (sum of shifted ideals, size)
    for index, ideal in enumerate(ideals):
        blocks.append((ideal - index * step, 1))
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, size = blocks.pop()
            prev_total, prev_size = blocks.pop()
            blocks.append((prev_total + total, prev_size + size))

    positions: list[float] = []
    for total, size in blocks:
        base = total / size
        positions.extend(base + offset * step for offset in range(size))

    if positions[0] < lo:
        shift = lo - positions[0]
        positions = [value + shift for value in positions]
    if positions[-1] > hi:
        shift = positions[-1] - hi
        positions = [value - shift for value in positions]
    return [clamp(value, lo, hi) for value in positions]


def _to_port(rect: Rect, node: str, face: Face, coordinate: float) -> Port:
    u = rect.face_u(face, coordinate)
    x, y = rect.face_point(face, u)
    return Port(node=node, face=face, u=u, x=x, y=y)


def spill_overflow(
    items: list[PortRequest],
    rect: Rect,
    face: Face,
    *,
    port_gap: float,
    eligible: set[tuple[tuple[str, str], str]],
) -> None:
    """Move the extreme-ideal endpoints of an over-subscribed face to the sides.

    Mutates the requests in place (the caller regroups afterwards). This is
    where four-sided attachment earns its keep: a hub with nine out-edges fans
    across bottom + left + right instead of cramming nine ports into 180px.
    Only ``eligible`` endpoints move — the router restricts spill to edges whose
    skeleton can start (or end) with a horizontal run.
    """
    if face not in ("top", "bottom"):
        return
    excess = len(items) - face_capacity(rect, face, port_gap)
    if excess <= 0:
        return

    movable = sorted(
        (item for item in items if (item.key, item.endpoint) in eligible and not item.rigid),
        key=lambda r: (r.ideal, r.key, r.endpoint),
    )
    take = min(excess, len(movable))
    if take <= 0:
        return

    # A spilled endpoint keeps heading the way it was: bottom-face traffic sits
    # low on the side face, top-face traffic sits high.
    side_ideal = rect.cy + (rect.height / 2.0 - INSET_V) * (1.0 if face == "bottom" else -1.0)
    for item in movable[: take // 2]:
        item.face = "left"
        item.ideal = side_ideal
    for item in movable[len(movable) - (take - take // 2) :]:
        item.face = "right"
        item.ideal = side_ideal
