"""Dev-only SVG renderer for eyeballing layouts in tests and the benchmark CLI.

Not wired into the product: the DFG canvas renders React-Flow nodes with the
waypoint edge component. Colors follow the paper's Fig. 20 convention —
blue downward, red upward (back edges).

backbone-v2 responses carry a ready-made ``path`` per edge; those are emitted
verbatim so what you look at here is exactly what the browser draws. Pass
``overlay=True`` to also show the router's safety envelopes and to put a red
dot on every point where a curve entered one — a routing bug then arrives
circled instead of hunted.
"""

from __future__ import annotations

from typing import Any

_STYLE = (
    "font-family:system-ui,sans-serif;font-size:11px;"
    "fill:#111;stroke:none;dominant-baseline:middle;text-anchor:middle"
)


def render_svg(
    response: dict[str, Any],
    sizes: dict[str, tuple[float, float]] | None = None,
    *,
    overlay: bool = False,
    clearance: float = 8.0,
) -> str:
    sizes = sizes or {}
    xs: dict[str, float] = response.get("x", {})
    ys: dict[str, float] = response.get("y", {})

    def _size(node: str) -> tuple[float, float]:
        return sizes.get(node, (220.0, 59.0))

    def _center(node: str) -> tuple[float, float]:
        width, height = _size(node)
        return xs[node] + width / 2.0, ys[node] + height / 2.0

    parts: list[str] = []
    overlaps: list[tuple[float, float]] = []
    max_x = max_y = 0.0

    if overlay:
        for node in xs:
            width, height = _size(node)
            parts.append(
                f'<rect x="{xs[node] - clearance:.1f}" y="{ys[node] - clearance:.1f}" '
                f'width="{width + 2 * clearance:.1f}" height="{height + 2 * clearance:.1f}" '
                'fill="none" stroke="#e67e22" stroke-width="0.75" stroke-dasharray="3 3"/>'
            )

    for edge in response.get("edges", []):
        if edge.get("self_loop"):
            continue
        source, target = edge["source"], edge["target"]
        if source not in xs or target not in xs:
            continue
        color = "#c0392b" if edge.get("back_edge") else "#2471a3"
        path = edge.get("path")
        if path is None:
            sx, sy = _center(source)
            tx, ty = _center(target)
            points = [(sx, sy), *[(px, py) for px, py in edge.get("waypoints", [])], (tx, ty)]
            path = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in points)
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.5"/>')
        if overlay:
            overlaps.extend(_overlaps(edge, xs, ys, _size, clearance, skip={source, target}))

    for node in xs:
        width, height = _size(node)
        left, top = xs[node], ys[node]
        max_x = max(max_x, left + width)
        max_y = max(max_y, top + height)
        parts.append(
            f'<rect x="{left:.1f}" y="{top:.1f}" width="{width:.1f}" height="{height:.1f}" '
            'rx="6" fill="#ecf0f1" stroke="#7f8c8d"/>'
        )
        cx, cy = left + width / 2.0, top + height / 2.0
        parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}" style="{_STYLE}">{node}</text>')

    for px, py in overlaps:
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#e74c3c"/>')

    body = "\n".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{max_x + 40:.0f}" '
        f'height="{max_y + 40:.0f}" viewBox="-20 -20 {max_x + 40:.0f} {max_y + 40:.0f}">\n'
        f"{body}\n</svg>"
    )


def _overlaps(
    edge: dict[str, Any],
    xs: dict[str, float],
    ys: dict[str, float],
    size: Any,
    clearance: float,
    *,
    skip: set[str],
) -> list[tuple[float, float]]:
    """Polyline vertices that landed inside a node's envelope.

    A coarse check on the vertices only — `metrics.route_metrics` does the
    exact one over the emitted curve samples. This is the visual aid.
    """
    points = edge.get("polyline") or edge.get("waypoints") or []
    hits: list[tuple[float, float]] = []
    for px, py in points:
        for node, left in xs.items():
            if node in skip:
                continue
            width, height = size(node)
            top = ys[node]
            if (
                left - clearance < px < left + width + clearance
                and top - clearance < py < top + height + clearance
            ):
                hits.append((px, py))
                break
    return hits
