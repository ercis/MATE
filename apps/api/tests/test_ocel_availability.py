"""Module availability gating by log model - the two models never cross."""

from __future__ import annotations

from mate.api.modules.availability import evaluate
from mate.sdk.manifest import (
    EventLogRequirements,
    Manifest,
    Requirements,
)


def _manifest(log_model: str, required_columns: list[str]) -> Manifest:
    return Manifest(
        id="m",
        name="M",
        version="0.0.1",
        category="foundation",
        requirements=Requirements(
            event_log=EventLogRequirements(
                log_model=log_model,  # type: ignore[arg-type]
                required_columns=required_columns,
            )
        ),
    )


_CASE_SCHEMA = {"columns": ["case_id", "activity", "timestamp"]}
_OCEL_SCHEMA = {"log_model": "object_centric", "ocel_columns": {"events": ["ocel:eid"]}}


def test_case_module_unavailable_on_ocel_log() -> None:
    m = _manifest("case_centric", ["case_id", "activity", "timestamp"])
    avail = evaluate(
        m,
        detected_schema=_OCEL_SCHEMA,
        events_count=100,
        cases_count=None,
        installed_module_ids=set(),
        log_model="object_centric",
    )
    assert avail.status == "unavailable"
    assert "object-centric" in avail.reasons[0]


def test_ocel_module_unavailable_on_case_log() -> None:
    m = _manifest("object_centric", [])
    avail = evaluate(
        m,
        detected_schema=_CASE_SCHEMA,
        events_count=100,
        cases_count=10,
        installed_module_ids=set(),
        log_model="case_centric",
    )
    assert avail.status == "unavailable"
    assert "case-centric" in avail.reasons[0]


def test_ocel_module_available_on_ocel_log() -> None:
    m = _manifest("object_centric", [])
    avail = evaluate(
        m,
        detected_schema=_OCEL_SCHEMA,
        events_count=100,
        cases_count=None,
        installed_module_ids=set(),
        log_model="object_centric",
    )
    assert avail.status == "available"


def test_case_module_available_on_case_log() -> None:
    m = _manifest("case_centric", ["case_id", "activity", "timestamp"])
    avail = evaluate(
        m,
        detected_schema=_CASE_SCHEMA,
        events_count=100,
        cases_count=10,
        installed_module_ids=set(),
        log_model="case_centric",
    )
    assert avail.status == "available"
