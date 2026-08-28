"""Obstacle index for the backbone-v2 router.

Only *real* nodes are obstacles — virtual nodes are routing anchors with a 1px
hairline width (`position.py`), so treating them as blockers would forbid the
very columns they exist to reserve.

The index is built per rank row, because `place()` puts every node in a rank on
one shared centre y (``y = rank * y_pitch``). That gives the two guarantees the
router leans on:

* the band between two consecutive occupied rank rows contains no node at all
  (it is at least ``v_gap`` tall), so a horizontal run inside a channel needs no
  collision test;
* within a row, ``_separate_outward`` keeps neighbours ``(w_l + w_r)/2 + h_gap``
  apart, so the column at a virtual node's x is clear by ~``h_gap`` either side.

Everything here bottoms out in one `bisect` over per-row sorted arrays: no
numpy, no scipy (neither is declared in `modules/discovery/manifest.yaml`).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from .geom import EPS, Rect, point_rect_dist, seg_rect_hit


@dataclass(frozen=True)
class Row:
    """One rank's real nodes, indexed for stabbing queries.

    ``lo``/``hi``/``ids``/``rects`` are parallel and sorted by ``lo``; they carry
    the *inflated* spans, so a query answers "is this within clearance of a
    node" rather than "does it touch one".

    ``pmax_hi`` is the running maximum of ``hi``, which makes stabbing correct
    even if two inflated spans were to overlap (they do not at the default
    clearance, but the router must not silently depend on that).

    ``mlo``/``mhi`` are the same spans merged, used for free-interval walks.
    """

    rank: int
    y_top: float
    y_bot: float
    lo: tuple[float, ...]
    hi: tuple[float, ...]
    ids: tuple[str, ...]
    rects: tuple[Rect, ...]
    pmax_hi: tuple[float, ...]
    mlo: tuple[float, ...]
    mhi: tuple[float, ...]


@dataclass
class ObstacleField:
    """Rank rows plus the channels between them."""

    rows: dict[int, Row]
    ranks: list[int]
    clearance: float
    fallback_channel_h: float
    _index: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._index = {rank: i for i, rank in enumerate(self.ranks)}

    # -- channels ----------------------------------------------------------

    def channel_between(self, rank_a: int, rank_b: int) -> tuple[float, float]:
        """The node-free band between two ranks (order-independent).

        Ranks need not be adjacent: `solve_ranks` can leave a rank empty, and a
        band spanning an empty rank is still node-free — just taller.
        """
        if rank_a == rank_b:
            return self.channel_below(rank_a)
        upper, lower = (rank_a, rank_b) if rank_a < rank_b else (rank_b, rank_a)
        return (self.rows[upper].y_bot, self.rows[lower].y_top)

    def channel_below(self, rank: int) -> tuple[float, float]:
        row = self.rows[rank]
        index = self._index[rank]
        if index + 1 < len(self.ranks):
            return (row.y_bot, self.rows[self.ranks[index + 1]].y_top)
        return (row.y_bot, row.y_bot + self.fallback_channel_h)

    def channel_above(self, rank: int) -> tuple[float, float]:
        row = self.rows[rank]
        index = self._index[rank]
        if index > 0:
            return (self.rows[self.ranks[index - 1]].y_bot, row.y_top)
        return (row.y_top - self.fallback_channel_h, row.y_top)

    def has_channel_below(self, rank: int) -> bool:
        return self._index[rank] + 1 < len(self.ranks)

    # -- per-row x queries -------------------------------------------------

    def stab(self, rank: int, x: float, ignore: frozenset[str] = frozenset()) -> list[str]:
        """Ids in ``rank`` whose inflated x-span contains ``x``."""
        row = self.rows.get(rank)
        if row is None:
            return []
        hits: list[str] = []
        index = bisect_right(row.lo, x) - 1
        while index >= 0 and row.pmax_hi[index] > x:
            if row.lo[index] < x < row.hi[index] and row.ids[index] not in ignore:
                hits.append(row.ids[index])
            index -= 1
        return hits

    def x_is_free(self, rank: int, x: float, ignore: frozenset[str] = frozenset()) -> bool:
        return not self.stab(rank, x, ignore)

    def stab_point(self, px: float, py: float, ignore: frozenset[str] = frozenset()) -> str | None:
        """The id of an inflated rect containing the point, if any.

        Used by the fillet verifier and the overlap metric, so it walks rows by
        y first — a curve sample is inside at most one row's band.
        """
        for rank in self.ranks:
            row = self.rows[rank]
            if py <= row.y_top - self.clearance or py >= row.y_bot + self.clearance:
                continue
            index = bisect_right(row.lo, px) - 1
            while index >= 0 and row.pmax_hi[index] > px:
                if row.ids[index] not in ignore and row.rects[index].contains(px, py):
                    return row.ids[index]
                index -= 1
        return None

    def free_interval(self, rank: int, x: float) -> tuple[float, float] | None:
        """The maximal node-free x-interval of ``rank`` containing ``x``.

        Unbounded at the extremes (``±inf``) — that is what makes the router's
        level-2 escape hatch always feasible: a route can always slip past the
        outermost node in a row.
        """
        row = self.rows.get(rank)
        if row is None or not row.mlo:
            return (float("-inf"), float("inf"))
        index = bisect_right(row.mlo, x) - 1
        if index >= 0 and row.mhi[index] > x:
            return None  # x sits inside a node
        left = row.mhi[index] if index >= 0 else float("-inf")
        right = row.mlo[index + 1] if index + 1 < len(row.mlo) else float("inf")
        return (left, right)

    def nearest_free_x(self, rank: int, x: float, width: float = 0.0) -> float:
        """Closest x to ``x`` in ``rank`` with ``width/2`` clear on both sides."""
        row = self.rows.get(rank)
        if row is None or not row.mlo:
            return x
        interval = self.free_interval(rank, x)
        if interval is not None and interval[1] - interval[0] >= width:
            return x
        half = width / 2.0
        best = x
        best_dist = float("inf")
        # Candidate gaps: the two outer half-lines plus every interior gap.
        gaps: list[tuple[float, float]] = [(float("-inf"), row.mlo[0])]
        for index in range(len(row.mlo) - 1):
            gaps.append((row.mhi[index], row.mlo[index + 1]))
        gaps.append((row.mhi[-1], float("inf")))
        for left, right in gaps:
            if right - left < width:
                continue
            lo = left + half if math.isfinite(left) else float("-inf")
            hi = right - half if math.isfinite(right) else float("inf")
            candidate = min(max(x, lo), hi)
            distance = abs(candidate - x)
            if distance < best_dist:
                best_dist = distance
                best = candidate
        return best

    # -- segment queries ---------------------------------------------------

    def vertical_clear(
        self, x: float, y0: float, y1: float, ignore: frozenset[str] = frozenset()
    ) -> bool:
        return self.segment_clear(x, y0, x, y1, ignore)

    def horizontal_clear(
        self, y: float, x0: float, x1: float, ignore: frozenset[str] = frozenset()
    ) -> bool:
        return self.segment_clear(x0, y, x1, y, ignore)

    def segment_clear(
        self,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        ignore: frozenset[str] = frozenset(),
    ) -> bool:
        """No inflated rect's interior is entered by A→B."""
        return not self.segment_hits(ax, ay, bx, by, ignore)

    def segment_hits(
        self,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        ignore: frozenset[str] = frozenset(),
    ) -> list[str]:
        y_lo, y_hi = (ay, by) if ay <= by else (by, ay)
        x_lo, x_hi = (ax, bx) if ax <= bx else (bx, ax)
        hits: list[str] = []
        for rank in self._rows_overlapping(y_lo, y_hi):
            row = self.rows[rank]
            start = max(0, bisect_left(row.lo, x_lo) - 1)
            for index in range(start, len(row.lo)):
                if row.lo[index] >= x_hi:
                    break
                if row.hi[index] <= x_lo:
                    continue
                if row.ids[index] in ignore:
                    continue
                if seg_rect_hit(ax, ay, bx, by, row.rects[index]):
                    hits.append(row.ids[index])
        return hits

    def _rows_overlapping(self, y_lo: float, y_hi: float) -> list[int]:
        return [
            rank
            for rank in self.ranks
            if self.rows[rank].y_bot + self.clearance > y_lo - EPS
            and self.rows[rank].y_top - self.clearance < y_hi + EPS
        ]

    # -- clearance ---------------------------------------------------------

    def clearance_at(self, px: float, py: float) -> float:
        """Distance from a point to the nearest *inflated* rect (0 inside one).

        This is the budget a fillet radius may spend: staying under it keeps the
        arc outside every node's safety envelope (see `fillet.py`).
        """
        best = float("inf")
        for rank in self.ranks:
            row = self.rows[rank]
            band = 0.0
            if py < row.y_top:
                band = row.y_top - py
            elif py > row.y_bot:
                band = py - row.y_bot
            # `y_top`/`y_bot` are the raw row extent, but the rects are
            # inflated: the envelope reaches `clearance` further than the band
            # does, so skipping on the raw distance can discard the true
            # nearest row.
            if max(0.0, band - self.clearance) >= best:
                continue
            index = bisect_right(row.lo, px)
            for candidate in range(max(0, index - 2), min(len(row.lo), index + 2)):
                distance = point_rect_dist(px, py, row.rects[candidate])
                if distance < best:
                    best = distance
                    if best <= 0.0:
                        return 0.0
        return best


def build_field(
    real_ids: list[str],
    ranks: dict[str, int],
    x: dict[str, float],
    y: dict[str, float],
    sizes: dict[str, tuple[float, float]],
    *,
    clearance: float,
    fallback_channel_h: float,
) -> ObstacleField:
    """Index the real nodes by rank. ``x``/``y`` are node *centres*."""
    by_rank: dict[int, list[tuple[float, str, Rect]]] = {}
    for node in real_ids:
        width, height = sizes.get(node, (0.0, 0.0))
        if width <= 0.0 or height <= 0.0:
            continue
        rect = Rect.from_center(x[node], y[node], width, height).inflate(clearance)
        by_rank.setdefault(ranks[node], []).append((rect.x0, node, rect))

    rows: dict[int, Row] = {}
    for rank, entries in by_rank.items():
        entries.sort()
        lo = tuple(entry[2].x0 for entry in entries)
        hi = tuple(entry[2].x1 for entry in entries)
        ids = tuple(entry[1] for entry in entries)
        rects = tuple(entry[2] for entry in entries)

        pmax: list[float] = []
        running = float("-inf")
        for value in hi:
            running = max(running, value)
            pmax.append(running)

        mlo: list[float] = []
        mhi: list[float] = []
        for left, right in zip(lo, hi, strict=True):
            if mhi and left <= mhi[-1]:
                mhi[-1] = max(mhi[-1], right)
            else:
                mlo.append(left)
                mhi.append(right)

        rows[rank] = Row(
            rank=rank,
            y_top=min(rect.y0 for rect in rects) + clearance,
            y_bot=max(rect.y1 for rect in rects) - clearance,
            lo=lo,
            hi=hi,
            ids=ids,
            rects=rects,
            pmax_hi=tuple(pmax),
            mlo=tuple(mlo),
            mhi=tuple(mhi),
        )

    return ObstacleField(
        rows=rows,
        ranks=sorted(rows),
        clearance=clearance,
        fallback_channel_h=fallback_channel_h,
    )
