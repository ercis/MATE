"""Pure compute for conformance checking - runs on a process-pool core.

``_conformance_worker`` is a top-level, picklable function handed to
``ctx.run_in_process``: it receives *paths* (never DataFrames or pm4py objects
across the pickle boundary), reads the log with plain pandas, reads the uploaded
reference BPMN, converts it to a Petri net, replays/aligns the log against it,
and returns the serialised JSON dict. It never imports the platform.

pm4py call sequence (2.7): ``read_bpmn`` → ``convert_to_petri_net`` →
``conformance_diagnostics_token_based_replay`` (default) or
``conformance_diagnostics_alignments`` (opt-in).

The token-replay path runs exactly **one** replay: log-fitness is aggregated from
the per-trace diagnostics (``_aggregate_tbr_fitness``) instead of calling
``fitness_token_based_replay`` (which re-replays the whole log for numbers we
already have), and ETConformance ``precision_token_based_replay`` - O(events)
memory that OOM-killed the offload child on large logs - is gated behind an
event budget (``precision_max_events``). Alignments keep all three passes: they
are bounded upstream by ``_guard_alignments_size``.

``multi_processing=False`` is mandatory for alignments: the worker is already on
a pool core, and pm4py's own multiprocessing inside a pool child deadlocks.

Label matching between the log and the model is **exact after canonicalisation**
(trim + collapse whitespace + casefold, ``_canonicalize_log_labels``). There is
deliberately no fuzzy/similarity matching: a one-letter difference is a
different activity and shows up as a deviation / label mismatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .serializers import serialize_conformance


def _rename_pm4py(df: Any) -> Any:
    return df.rename(
        columns={
            "case_id": "case:concept:name",
            "activity": "concept:name",
            "timestamp": "time:timestamp",
        }
    )


def _canon_label(value: Any) -> str:
    """Canonical key for activity-label matching: trim, collapse internal
    whitespace, casefold. Matching stays EXACT on this key - whitespace/case
    variants unify, but one wrong letter is a different activity ("Aproval"
    never matches "Approval"). Deliberately no fuzzy/similarity matching."""
    return " ".join(str(value).split()).casefold()


def _canonicalize_log_labels(df: Any, net: Any) -> Any:
    """Rewrite log activities that are whitespace/case variants of a model label
    to the model's exact spelling, so the replay, the label report and the
    diagram heatmap all agree on one string. Anything else - typos included -
    is left untouched and surfaces explicitly as a deviation / label mismatch."""
    model_exact = {str(t.label) for t in net.transitions if t.label is not None}
    canon_to_model: dict[str, str] = {}
    for lbl in sorted(model_exact):  # sorted → deterministic pick on collisions
        canon_to_model.setdefault(_canon_label(lbl), lbl)
    rename: dict[Any, str] = {}
    for a in df["concept:name"].unique():
        s = str(a)
        if s in model_exact:
            continue  # already the model's exact spelling
        target = canon_to_model.get(_canon_label(s))
        if target is not None and target != s:
            rename[a] = target
    if rename:
        df["concept:name"] = df["concept:name"].replace(rename)
    return df


def _label_report(net: Any, log_labels: set[str]) -> dict[str, Any]:
    """Exact set comparison of model vs log labels. The log side has already
    been canonicalised (`_canonicalize_log_labels`), so whitespace/case
    variants count as matched while any other difference is reported."""
    model_labels = {t.label for t in net.transitions if t.label is not None}
    return {
        "in_model_not_log": sorted(model_labels - log_labels),
        "in_log_not_model": sorted(log_labels - model_labels),
        "matched": sorted(model_labels & log_labels),
        "model_count": len(model_labels),
        "log_count": len(log_labels),
    }


def validate_bpmn_file(path: str) -> int:
    """Parse a BPMN file with pm4py and return its labelled-task count.

    Raises if the file is not a BPMN pm4py can read or cannot be converted to a
    Petri net. Used at upload time so bad files fail fast with a 422 instead of
    blowing up mid-job.
    """
    import pm4py

    bpmn_graph = pm4py.read_bpmn(path)
    net, _im, _fm = pm4py.convert_to_petri_net(bpmn_graph)
    return sum(1 for t in net.transitions if t.label is not None)


def _aggregate_tbr_fitness(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-trace token-replay diagnostics into the dict
    ``fitness_token_based_replay`` would return - WITHOUT replaying again.

    ``conformance_diagnostics_token_based_replay`` already replayed every trace
    and handed back its per-trace token counts; pm4py's ``fitness_token_based_replay``
    just re-runs the *same* replay to sum them. Summing the diagnostics we already
    hold is exact (verified against pm4py for clean + deviant logs) and halves the
    work - the redundant second replay was a full O(events) pass and, on large
    logs, doubled the peak memory of the offload child.

    ``log_fitness`` is the canonical token-replay fitness (van der Aalst / Rozinat):
    ``0.5·(1 - Σmissing/Σconsumed) + 0.5·(1 - Σremaining/Σproduced)``.
    """
    sum_missing = sum_remaining = sum_consumed = sum_produced = 0
    sum_trace_fitness = 0.0
    fit_traces = 0
    n = 0
    for d in diagnostics:
        sum_missing += int(d.get("missing_tokens", 0) or 0)
        sum_remaining += int(d.get("remaining_tokens", 0) or 0)
        sum_consumed += int(d.get("consumed_tokens", 0) or 0)
        sum_produced += int(d.get("produced_tokens", 0) or 0)
        sum_trace_fitness += float(d.get("trace_fitness", 0.0) or 0.0)
        if d.get("trace_is_fit"):
            fit_traces += 1
        n += 1
    log_fitness = 0.5 * (1.0 - (sum_missing / sum_consumed if sum_consumed else 0.0)) + 0.5 * (
        1.0 - (sum_remaining / sum_produced if sum_produced else 0.0)
    )
    perc = 100.0 * fit_traces / n if n else 0.0
    return {
        "log_fitness": log_fitness,
        "average_trace_fitness": sum_trace_fitness / n if n else 0.0,
        "percentage_of_fitting_traces": perc,
        "perc_fit_traces": perc,
    }


def _conformance_worker(
    events_path: str,
    bpmn_path: str,
    technique: str,
    conforming_threshold: float,
) -> dict[str, Any]:
    """Core conformance pass: diagnostics + fitness + the serialised result.

    Token-replay **precision** is deliberately NOT computed here. It is the
    O(events) ETConformance pass that OOM-killed *this* offload child on large
    logs - and a child SIGKILL takes the (cheap, already-computed) fitness and
    deviations down with it, so the run caches nothing and the panel shows an
    empty "run" state. The host instead runs precision in a *separate*,
    crash-isolated offload (`_precision_token_worker`) and degrades to
    ``precision=None`` if that child dies. Alignments precision stays inline: it
    is bounded upstream by ``_guard_alignments_size`` and cannot blow up.
    """
    import pandas as pd
    import pm4py

    df = _rename_pm4py(pd.read_parquet(events_path))
    n_events = len(df)

    bpmn_graph = pm4py.read_bpmn(bpmn_path)
    net, im, fm = pm4py.convert_to_petri_net(bpmn_graph)

    # Unify whitespace/case variants with the model spelling (exact-only, no
    # fuzzy matching) BEFORE replaying, so replay and report agree.
    df = _canonicalize_log_labels(df, net)

    name_to_label = {t.name: t.label for t in net.transitions}
    log_labels = {str(a) for a in df["concept:name"].unique()}
    label_report = _label_report(net, log_labels)

    # Explicit EventLog so trace order is stable and zips 1:1 with diagnostics.
    log = pm4py.convert_to_event_log(df)

    prec: float | None
    if technique == "alignments":
        # Bounded upstream by `_guard_alignments_size` (cases + variants capped),
        # so all three alignment passes are safe to run here.
        diagnostics = pm4py.conformance_diagnostics_alignments(
            log, net, im, fm, multi_processing=False
        )
        fit = pm4py.fitness_alignments(log, net, im, fm, multi_processing=False)
        prec = float(pm4py.precision_alignments(log, net, im, fm))
    else:
        # ONE replay only: the per-trace diagnostics already carry every token
        # count log-fitness needs, so we aggregate them rather than replay twice.
        # Precision is left to the host's isolated offload (see docstring).
        diagnostics = pm4py.conformance_diagnostics_token_based_replay(log, net, im, fm)
        fit = _aggregate_tbr_fitness(diagnostics)
        prec = None

    bpmn_xml = Path(bpmn_path).read_text(encoding="utf-8")
    result = serialize_conformance(
        log,
        diagnostics,
        fit,
        prec,
        technique,
        name_to_label,
        label_report,
        conforming_threshold,
        bpmn_xml,
        None,
    )
    # The host needs the event count to apply the token-precision budget without
    # re-reading the parquet; it pops this private key before caching.
    result["_n_events"] = n_events
    return result


def _precision_token_worker(events_path: str, bpmn_path: str) -> float:
    """Token-replay (ETConformance) precision, run in its OWN offload child.

    Isolated on purpose: this is the O(events) pass whose memory spike OOM-kills
    the child on large/complex models. Running it apart from the core worker means
    that death only loses *precision* - the host catches the dropped pipe and
    degrades to ``None`` while still shipping the fitness + deviations the core
    worker already returned.
    """
    import pandas as pd
    import pm4py

    df = _rename_pm4py(pd.read_parquet(events_path))
    bpmn_graph = pm4py.read_bpmn(bpmn_path)
    net, im, fm = pm4py.convert_to_petri_net(bpmn_graph)
    # Same exact-after-canonicalisation label unification as the core worker,
    # so precision is measured on the identical label space.
    df = _canonicalize_log_labels(df, net)
    log = pm4py.convert_to_event_log(df)
    return float(pm4py.precision_token_based_replay(log, net, im, fm))
