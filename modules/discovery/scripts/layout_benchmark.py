"""Benchmark the server-side DFG layout algorithms on XES logs.

Reproduces the paper's experiment grid (guide §11 stage 2): for each log and
variant-coverage ratio, both algorithms run on the full DFG (artificial
start/end included) and the six quality metrics plus wall times land in a CSV.
BPI logs are external downloads (4TU.ResearchData) — nothing is bundled.

Usage (repo root):
    uv run --extra dev --with "ortools>=9.11,<10" python \\
        modules/discovery/scripts/layout_benchmark.py \\
        path/to/Sepsis.xes.gz [more.xes ...] \\
        -o layout_metrics.csv --ratios 1.0 0.8 0.6 0.4 0.2 --svg-dir svgs/

Without ortools the backbone algorithm reports its heuristic fallback status —
the grid still runs, but rank quality won't match the paper.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from itertools import pairwise
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.discovery.layout.debug_svg import render_svg  # noqa: E402
from modules.discovery.layout.pipeline import compute_layout  # noqa: E402

START = "__start__"
END = "__end__"

# Emitted by backbone-v2 only; blank for the other algorithms.
ROUTE_METRICS = (
    "qm_no_overlap",
    "qm_bends",
    "qm_bends_max",
    "qm_straight_frac",
    "qm_min_radius",
    "qm_curv_p05",
    "qm_repairs",
)

NODE_WIDTH = 220.0
NODE_HEIGHT = 59.0
TERMINAL_WIDTH = 112.0
TERMINAL_HEIGHT = 43.0


def load_variants(xes_path: Path) -> list[tuple[list[str], int]]:
    """(activity sequence, case count) per variant, most frequent first."""
    import pm4py

    frame = pm4py.read_xes(str(xes_path))
    frame = frame.sort_values(["case:concept:name", "time:timestamp"], kind="mergesort")
    per_case = frame.groupby("case:concept:name", sort=False)["concept:name"].agg(tuple)
    counts: Counter[tuple[str, ...]] = Counter(per_case.tolist())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [([str(a) for a in sequence], int(count)) for sequence, count in ordered]


def coverage_filter(
    variants: list[tuple[list[str], int]], ratio: float
) -> list[tuple[list[str], int]]:
    """Keep the most frequent variants cumulatively covering ``ratio`` of cases
    (mirrors the discovery module's `_filter_variants_coverage`)."""
    total = sum(count for _seq, count in variants)
    if total == 0 or ratio >= 1.0:
        return variants
    kept: list[tuple[list[str], int]] = []
    covered = 0
    for sequence, count in variants:
        if covered / total >= ratio:
            break
        kept.append((sequence, count))
        covered += count
    return kept or variants[:1]


def build_graph(
    variants: list[tuple[list[str], int]],
) -> tuple[list[dict[str, float | str]], list[list[str]]]:
    """Full DFG with artificial start/end (paper Def. 4)."""
    activities: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for sequence, _count in variants:
        wrapped = [START, *sequence, END]
        activities.update(sequence)
        edges.update(pairwise(wrapped))
    nodes: list[dict[str, float | str]] = [
        {"id": activity, "width": NODE_WIDTH, "height": NODE_HEIGHT}
        for activity in sorted(activities)
    ]
    nodes.append({"id": START, "width": TERMINAL_WIDTH, "height": TERMINAL_HEIGHT})
    nodes.append({"id": END, "width": TERMINAL_WIDTH, "height": TERMINAL_HEIGHT})
    return nodes, [list(edge) for edge in sorted(edges)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="XES / XES.GZ files")
    parser.add_argument("-o", "--output", type=Path, default=Path("layout_metrics.csv"))
    parser.add_argument(
        "--ratios",
        nargs="+",
        type=float,
        default=[1.0, 0.8, 0.6, 0.4, 0.2],
        help="Variant-coverage filter levels (paper: 1.0 0.8 0.6 0.4 0.2)",
    )
    parser.add_argument("--algorithms", nargs="+", default=["backbone", "backbone-v2", "sugiyama"])
    parser.add_argument("--time-limit", type=float, default=600.0, help="CP-SAT budget (s)")
    parser.add_argument("--paper-compat", action="store_true", help="Literal Def. 23 formulas")
    parser.add_argument("--svg-dir", type=Path, default=None, help="Dump a debug SVG per run")
    parser.add_argument(
        "--overlay",
        action="store_true",
        help="Draw node envelopes and mark overlaps red in the SVGs",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Print a backbone vs backbone-v2 routing-metric delta per run",
    )
    args = parser.parse_args()

    fieldnames = [
        "log",
        "ratio",
        "algorithm",
        "nodes",
        "edges",
        "variants",
        "qm_be",
        "qm_bal",
        "qm_ec",
        "qm_el",
        "qm_eo",
        "qm_no",
        *ROUTE_METRICS,
        "solver_status",
        "solver_objective",
        "solver_ms",
        "route_ms",
        "total_ms",
    ]
    rows: list[dict[str, object]] = []

    for log_path in args.logs:
        print(f"[{log_path.name}] reading …", flush=True)
        all_variants = load_variants(log_path)
        for ratio in args.ratios:
            variants = coverage_filter(all_variants, ratio)
            nodes, edges = build_graph(variants)
            for algorithm in args.algorithms:
                started = time.perf_counter()
                response = compute_layout(
                    nodes,  # type: ignore[arg-type]
                    edges,
                    variants,
                    START,
                    END,
                    {
                        "algorithm": algorithm,
                        "time_limit_s": args.time_limit,
                        "paper_compat_metrics": args.paper_compat,
                    },
                )
                total_ms = (time.perf_counter() - started) * 1000.0
                metrics = response["metrics"]
                solver = response["solver"]
                rows.append(
                    {
                        "log": log_path.name,
                        "ratio": ratio,
                        "algorithm": algorithm,
                        "nodes": len(nodes),
                        "edges": len(edges),
                        "variants": len(variants),
                        **{
                            key: metrics.get(key, "")
                            for key in (
                                "qm_be",
                                "qm_bal",
                                "qm_ec",
                                "qm_el",
                                "qm_eo",
                                "qm_no",
                                *ROUTE_METRICS,
                            )
                        },
                        "solver_status": solver["status"],
                        "solver_objective": solver["objective"],
                        "solver_ms": solver["wall_ms"],
                        "route_ms": response.get("route_ms", ""),
                        "total_ms": round(total_ms, 1),
                    }
                )
                print(
                    f"[{log_path.name}] ratio={ratio} {algorithm}: "
                    f"|N|={len(nodes)} |E|={len(edges)} "
                    f"ec={metrics.get('qm_ec')} be={metrics.get('qm_be')} "
                    f"{solver['status']} in {total_ms:.0f} ms",
                    flush=True,
                )
                if args.svg_dir is not None:
                    args.svg_dir.mkdir(parents=True, exist_ok=True)
                    sizes = {
                        str(node["id"]): (float(node["width"]), float(node["height"]))
                        for node in nodes
                    }
                    name = f"{log_path.stem}_{ratio}_{algorithm}.svg"
                    (args.svg_dir / name).write_text(
                        render_svg(response, sizes, overlay=args.overlay)
                    )

    with args.output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    if args.compare:
        _print_comparison(rows)
    return 0


def _print_comparison(rows: list[dict[str, object]]) -> None:
    """v1 vs v2 on the metrics routing actually moves.

    `qm_eo` is deliberately absent: an orthogonal skeleton is axis-aligned by
    construction, so v2 wins it by definition and the number says nothing.
    """
    by_run: dict[tuple[object, object], dict[object, dict[str, object]]] = {}
    for row in rows:
        by_run.setdefault((row["log"], row["ratio"]), {})[row["algorithm"]] = row

    header = f"{'log':<24} {'ratio':>5}  {'qm_el':>10} {'bends':>7} {'straight':>8} {'minR':>6}"
    printed = False
    for (log, ratio), variants in sorted(by_run.items(), key=lambda item: str(item[0])):
        v1 = variants.get("backbone")
        v2 = variants.get("backbone-v2")
        if v1 is None or v2 is None:
            continue
        if not printed:
            print(f"\n{header}")
            printed = True
        length_delta = _as_float(v2["qm_el"]) - _as_float(v1["qm_el"])
        print(
            f"{log!s:<24} {ratio!s:>5}  {length_delta:>+10.0f} "
            f"{_as_float(v2['qm_bends']):>7.0f} {_as_float(v2['qm_straight_frac']):>8.2f} "
            f"{_as_float(v2['qm_min_radius']):>6.1f}"
        )
        overlaps = _as_float(v2["qm_no_overlap"])
        if overlaps:
            print(f"  !! {overlaps:.0f} route/node overlaps — the router let something through")


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
