"""Serialise pm4py conformance diagnostics into the JSON shape the panel reads.

The heavy lifting (`read_bpmn` → `convert_to_petri_net` → replay/alignments)
happens in ``conformance.py``'s pool worker; this module turns pm4py's per-trace
diagnostics into a compact, picklable dict. It is imported *inside* the worker
process, so it must stay free of any platform imports.

Two techniques feed the same output contract:

* **token-based replay** - per trace: ``trace_fitness``, ``trace_is_fit``,
  ``missing/remaining/produced/consumed_tokens`` and ``transitions_with_problems``
  (transition *names*, mapped to labels via ``name_to_label``).
* **alignments** - per trace: ``fitness``, ``cost`` and ``alignment`` (a list of
  ``(log_step, model_step)`` moves where ``">>"`` is a skip).
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any


def _finite(x: float | None) -> float | None:
    """Guard against non-finite KPI floats. pm4py can hand back ``nan``/``inf``
    (degenerate precision on some nets); the API renders responses with
    ``allow_nan=False`` and would 500 on them - blanking the whole panel. Map
    them to ``None`` so the tile shows a dash instead."""
    return x if (x is not None and math.isfinite(x)) else None


# Cap the per-case list so FastAPI's encoder never serialises a million rows;
# everything else is aggregated into per_activity / per_variant.
_PER_CASE_CAP = 500


def _log_fitness(fit: Any) -> float:
    """pm4py renamed this key across versions - read whichever is present."""
    if isinstance(fit, dict):
        for k in ("log_fitness", "average_trace_fitness", "averageFitness"):
            if k in fit and fit[k] is not None:
                return float(fit[k])
    return 0.0


def _perc_fit(fit: Any) -> float | None:
    if isinstance(fit, dict):
        for k in ("perc_fit_traces", "percentage_of_fitting_traces", "percFitTraces"):
            if k in fit and fit[k] is not None:
                return float(fit[k])
    return None


def _trace_acts(trace: Any) -> list[str]:
    return [str(ev["concept:name"]) for ev in trace]


def _token_problem_labels(diag: dict[str, Any], name_to_label: dict[Any, Any]) -> list[str]:
    """Map token-replay ``transitions_with_problems`` to visible activity labels.

    The entries are transition *names* (or Transition objects, defensively).
    Invisible (tau) transitions map to ``None`` and are dropped.
    """
    out: list[str] = []
    for p in diag.get("transitions_with_problems") or []:
        name = getattr(p, "name", p)
        label = name_to_label.get(name)
        if label is None and getattr(p, "label", None) is not None:
            label = p.label
        if label is None:
            continue
        out.append(str(label))
    return out


def _alignment_moves(diag: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split an alignment into (log-move labels, model-move labels).

    ``(log_step, model_step)``: ``model == ">>"`` → observed but not in the model
    (log move); ``log == ">>"`` and a real model step → expected but skipped
    (model move). Invisible model steps (``None``) are ignored.
    """
    log_moves: list[str] = []
    model_moves: list[str] = []
    for move in diag.get("alignment") or []:
        try:
            log_step, model_step = move
        except (TypeError, ValueError):
            continue
        if model_step == ">>" and log_step not in (">>", None):
            log_moves.append(str(log_step))
        elif log_step == ">>" and model_step not in (">>", None):
            model_moves.append(str(model_step))
    return log_moves, model_moves


def serialize_conformance(
    log: Any,
    diagnostics: list[dict[str, Any]],
    fit: Any,
    prec: float | None,
    technique: str,
    name_to_label: dict[Any, Any],
    label_report: dict[str, Any],
    conforming_threshold: float,
    bpmn_xml: str,
    precision_skipped: str | None = None,
) -> dict[str, Any]:
    matched_set = set(label_report.get("matched") or [])
    model_labels = matched_set | set(label_report.get("in_model_not_log") or [])

    deviations: dict[str, int] = defaultdict(int)
    log_move_counts: dict[str, int] = defaultdict(int)
    model_move_counts: dict[str, int] = defaultdict(int)
    cases_affected: dict[str, int] = defaultdict(int)

    variant_cases: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    all_cases: list[dict[str, Any]] = []
    n_conforming = 0

    traces = list(log)
    for trace, diag in zip(traces, diagnostics, strict=False):
        case_id = (
            str(trace.attributes.get("concept:name", "")) if hasattr(trace, "attributes") else ""
        )
        acts = _trace_acts(trace)

        if technique == "alignments":
            log_moves, model_moves = _alignment_moves(diag)
            fitness = float(diag.get("fitness", diag.get("trace_fitness", 0.0)) or 0.0)
            is_fit = fitness >= 1.0
            detail = {"cost": diag.get("cost"), "log_moves": log_moves, "model_moves": model_moves}
        else:
            problems = _token_problem_labels(diag, name_to_label)
            log_moves = []
            model_moves = problems  # token replay attributes problems to model transitions
            fitness = float(diag.get("trace_fitness", 0.0) or 0.0)
            is_fit = bool(diag.get("trace_is_fit", fitness >= 1.0))
            detail = {
                "missing_tokens": int(diag.get("missing_tokens", 0) or 0),
                "remaining_tokens": int(diag.get("remaining_tokens", 0) or 0),
                "produced_tokens": int(diag.get("produced_tokens", 0) or 0),
                "consumed_tokens": int(diag.get("consumed_tokens", 0) or 0),
            }

        case_dev_labels: set[str] = set()
        for lbl in log_moves:
            deviations[lbl] += 1
            log_move_counts[lbl] += 1
            case_dev_labels.add(lbl)
        for lbl in model_moves:
            deviations[lbl] += 1
            model_move_counts[lbl] += 1
            case_dev_labels.add(lbl)
        for lbl in case_dev_labels:
            cases_affected[lbl] += 1

        if fitness >= conforming_threshold:
            n_conforming += 1

        case_row = {
            "case_id": case_id,
            "fitness": round(fitness, 4),
            "is_fit": is_fit,
            "n_deviations": len(log_moves) + len(model_moves),
            "deviations": sorted(case_dev_labels),
            "detail": detail,
        }
        all_cases.append(case_row)
        variant_cases[tuple(acts)].append(case_row)

    n_cases = len(all_cases)

    # ── per-activity (drives heatmap + bar chart) ────────────────────────────
    all_labels = set(model_labels) | set(deviations.keys())
    per_activity = sorted(
        (
            {
                "activity": lbl,
                "deviations": int(deviations.get(lbl, 0)),
                "log_moves": int(log_move_counts.get(lbl, 0)),
                "model_moves": int(model_move_counts.get(lbl, 0)),
                "cases_affected": int(cases_affected.get(lbl, 0)),
                "matched": lbl in matched_set,
            }
            for lbl in all_labels
        ),
        key=lambda r: (-r["deviations"], r["activity"]),
    )

    # ── per-variant ──────────────────────────────────────────────────────────
    per_variant: list[dict[str, Any]] = []
    for variant, cases in sorted(variant_cases.items(), key=lambda kv: -len(kv[1])):
        n = len(cases)
        avg_fit = sum(c["fitness"] for c in cases) / n if n else 0.0
        total_dev = sum(c["n_deviations"] for c in cases)
        per_variant.append(
            {
                # The platform's canonical variant id (blake2b-8 over the
                # "→"-joined sequence, see apps/api ingest/aggregation.py) so
                # the panel can link straight to /processes/{log}/variants/{id}.
                # Duplicated here because worker code must stay platform-free.
                "variant_id": hashlib.blake2b(
                    "→".join(variant).encode("utf-8"), digest_size=8
                ).hexdigest(),
                "activities": list(variant),
                "n_cases": n,
                "avg_fitness": round(avg_fit, 4),
                "deviations": total_dev,
                "detail": cases[0]["detail"],
            }
        )

    # ── per-case (top-N by deviation, then worst fitness) ────────────────────
    per_case = sorted(all_cases, key=lambda c: (-c["n_deviations"], c["fitness"]))[:_PER_CASE_CAP]

    perc_fit = _perc_fit(fit)
    conforming_pct = round(100.0 * n_conforming / n_cases, 2) if n_cases else 0.0

    return {
        "kind": "conformance",
        "version": 1,
        "technique": technique,
        "kpis": {
            "log_fitness": _finite(round(_log_fitness(fit), 4)),
            "precision": _finite(round(float(prec), 4)) if prec is not None else None,
            "perc_fit_traces": conforming_pct,
            "perc_fit_traces_pm4py": round(perc_fit, 2) if perc_fit is not None else None,
            "total_deviations": int(sum(deviations.values())),
            "n_cases": n_cases,
            "n_variants": len(variant_cases),
        },
        "per_activity": per_activity,
        "per_variant": per_variant,
        "per_case": per_case,
        "per_case_truncated": n_cases > _PER_CASE_CAP,
        "label_report": label_report,
        "bpmn_xml": bpmn_xml,
        # Non-null when the log exceeded the precision budget: the KPI tile shows a
        # dash plus this note instead of a crashed job (precision is the OOM-prone pass).
        "precision_skipped": precision_skipped,
    }
