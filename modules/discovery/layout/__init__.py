"""Server-side DFG layout algorithms.

Pure algorithm package — no platform imports, no pandas. Everything crosses the
`ctx.run_in_process` pickle boundary as plain dicts/lists via
`pipeline.compute_layout`, and the whole package runs in bare pytest on the
root venv (CP-SAT tests skip when `ortools` is absent).

Algorithms:
- ``backbone`` — Lee/Song/van der Aalst, *Optimized Backbone-Based Process
  Layout* (DKE 164:102601): CP-SAT rank IP, virtual nodes, subset-sum component
  balancing, backbone-pinned cross-minimization, packcut/packcutBN positioning.
- ``backbone-v2`` — the same pipeline up to and including positioning, then a
  real edge router (`obstacles` → `ports` → `router` → `channels` → `fillet`):
  ports anywhere on a node's border, no edge crossing a node, as few bends as
  the geometry allows, and corners rounded to the widest radius the free space
  permits. Node placement is v1's, with individual channels widened only where
  the routes actually need the tracks.
- ``sugiyama`` — the paper's Mennens-2019 benchmark pipeline (heuristic
  variant-by-variant ranking, greedy balancing, unpinned Gansner
  cross-minimization/positioning), exposed as a user-selectable layout.

This ``__init__`` deliberately re-exports only the light data model: the main
API process imports `LAYOUT_VERSION` for cache keys, while the compute chain
(`pipeline` → `balance` → networkx, `rank_ip` → ortools) is imported lazily
inside the offload worker so the event-loop process never pays for it.
"""

from .model import LAYOUT_VERSION, LayoutGraph, LayoutNode, LayoutOptions

__all__ = ["LAYOUT_VERSION", "LayoutGraph", "LayoutNode", "LayoutOptions"]
