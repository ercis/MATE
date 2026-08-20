"""Per-log module availability check (§5.8).

Given a loaded module's manifest and the event log's detected schema, decide
whether the module is **available** / **unavailable** / **degraded**, with a
human-readable reason for the frontend tooltip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from mate.sdk.manifest import EventLogRequirements, Manifest

AvailabilityStatus = Literal["available", "unavailable", "degraded"]


@dataclass(frozen=True)
class Availability:
    status: AvailabilityStatus
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons)}


def _columns_present(req: EventLogRequirements, schema: dict[str, Any]) -> list[str]:
    available = {*schema.get("columns", []), *schema.get("canonical_columns", [])}
    return [c for c in req.required_columns if c not in available]


def evaluate(
    manifest: Manifest,
    *,
    detected_schema: dict[str, Any] | None,
    events_count: int | None,
    cases_count: int | None,
    installed_module_ids: set[str],
    log_model: str = "case_centric",
) -> Availability:
    reasons: list[str] = []
    schema = detected_schema or {}

    # Log-model gate first: a case-centric module is unavailable on an
    # object-centric (OCEL) log and vice versa. Short-circuits so neither model
    # ever hits the other's column / count checks.
    required_model = manifest.requirements.event_log.log_model
    if required_model != log_model:
        return Availability(
            status="unavailable",
            reasons=[
                f"Requires a {required_model.replace('_', '-')} log; "
                f"this log is {log_model.replace('_', '-')}."
            ],
        )

    missing_cols = _columns_present(manifest.requirements.event_log, schema)
    if missing_cols:
        reasons.append(f"Missing required column(s) on events: {', '.join(missing_cols)}.")

    min_events = manifest.requirements.event_log.min_events
    if min_events is not None and (events_count or 0) < min_events:
        reasons.append(f"Needs at least {min_events} events; this log has {events_count or 0}.")
    min_cases = manifest.requirements.event_log.min_cases
    if min_cases is not None and (cases_count or 0) < min_cases:
        reasons.append(f"Needs at least {min_cases} cases; this log has {cases_count or 0}.")

    missing_hard = [m for m in manifest.requirements.modules if m not in installed_module_ids]
    if missing_hard:
        reasons.append(f"Requires module(s) not installed: {', '.join(missing_hard)}.")

    if reasons:
        return Availability(status="unavailable", reasons=reasons)

    missing_soft = [
        opt.id
        for opt in manifest.requirements.optional_modules
        if opt.id not in installed_module_ids
    ]
    if missing_soft:
        return Availability(
            status="degraded",
            reasons=[f"Limited - optional module(s) missing: {', '.join(missing_soft)}."],
        )

    return Availability(status="available", reasons=[])
