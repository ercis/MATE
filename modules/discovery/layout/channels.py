"""Per-channel vertical inflation (backbone-v2).

The router can need more room between two rank rows than the uniform
``y_pitch`` gives it — a channel carrying six parallel horizontal runs needs
six tracks. Rather than widening `v_gap` globally (which would make every
sparse graph taller and, more importantly, break the mode-switch morph that
`position.py` deliberately preserves by pinning the same 280x149 grid the
client-side Celonis layout uses), v2 buys the room one channel at a time.

The recomputation is a monotone prefix sum over the *occupied* ranks — y only,
never x, and never below the v1 pitch. It therefore cannot introduce a node
overlap and cannot disturb any column the router relies on.
"""

from __future__ import annotations

from itertools import pairwise


def inflate_channels(
    ranks_present: list[int],
    row_heights: dict[int, float],
    needs: dict[int, float],
    *,
    y_pitch: float,
    max_channel_h: float,
) -> dict[int, float]:
    """Rank → new centre y. ``needs`` is keyed by the rank *above* the channel.

    Ranks holding only virtual nodes have height 0 and simply keep the pitch.
    """
    if not ranks_present:
        return {}

    ordered = sorted(ranks_present)
    out: dict[int, float] = {ordered[0]: float(ordered[0]) * y_pitch}
    for previous, current in pairwise(ordered):
        gap = min(needs.get(previous, 0.0), max_channel_h)
        half_span = (row_heights.get(previous, 0.0) + row_heights.get(current, 0.0)) / 2.0
        # A rank gap is already worth its own pitch; never shrink below that.
        pitch = y_pitch * (current - previous)
        out[current] = out[previous] + max(pitch, half_span + gap)
    return out
