"""Unit tests for the DashboardItem widget/viz discriminated union.

The board's ``layout_json`` round-trips through ``DashboardItem``, so the union
must validate both card kinds, reject malformed cards, and load legacy items
(written before ``kind`` existed) as ``widget`` - the property that lets the
feature ship with no migration.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mate.api.schemas.dashboards import (
    GRID_COLS,
    LEGACY_MAX_COLS,
    MAX_ROW,
    CanvasSettings,
    DashboardItem,
)


def test_widget_kind_requires_module_and_widget() -> None:
    item = DashboardItem(i="a", module_id="discovery", widget_id="process-map")
    assert item.kind == "widget"

    with pytest.raises(ValidationError):
        DashboardItem(i="a", kind="widget", module_id="discovery")  # no widget_id


def test_legacy_item_without_kind_loads_as_widget() -> None:
    # A row written before `kind` existed: no `kind`, carries module_id/widget_id.
    item = DashboardItem.model_validate(
        {
            "i": "a",
            "module_id": "discovery",
            "widget_id": "process-map",
            "x": 0,
            "y": 0,
            "w": 6,
            "h": 8,
        }
    )
    assert item.kind == "widget"
    assert item.module_id == "discovery"


def test_viz_kind_requires_dataset_ref() -> None:
    item = DashboardItem(
        i="b",
        kind="viz",
        dataset_ref={"module_id": "discovery", "dataset_id": "dfg"},
    )
    assert item.kind == "viz"
    assert item.dataset_ref is not None
    assert item.dataset_ref.dataset_id == "dfg"
    # Unconfigured viz card is valid: viz_id/mapping fill in later.
    assert item.viz_id is None
    assert item.mapping == {}

    with pytest.raises(ValidationError):
        DashboardItem(i="b", kind="viz", viz_id="bar")  # no dataset_ref


def test_viz_kind_roundtrips_mapping_and_options() -> None:
    raw = {
        "i": "c",
        "kind": "viz",
        "dataset_ref": {"module_id": "conformance", "dataset_id": "per_activity"},
        "viz_id": "bar",
        "mapping": {"x": "activity", "y": "deviations"},
        "config": {"stacked": True},
        "x": 2,
        "y": 0,
        "w": 6,
        "h": 8,
    }
    item = DashboardItem.model_validate(raw)
    dumped = item.model_dump()
    assert dumped["kind"] == "viz"
    assert dumped["mapping"] == {"x": "activity", "y": "deviations"}
    assert dumped["config"] == {"stacked": True}


# ── 12-column grid bounds ────────────────────────────────────────────────────


def _widget(**geometry: int) -> DashboardItem:
    return DashboardItem(i="a", module_id="discovery", widget_id="process-map", **geometry)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", LEGACY_MAX_COLS),  # 0-59
        ("x", -1),
        ("w", LEGACY_MAX_COLS + 1),  # 1-60
        ("w", 0),
        ("y", MAX_ROW + 1),  # y is bounded now; it used to be unbounded
        ("y", -1),
        ("h", 49),
        ("h", 0),
    ],
)
def test_absurd_geometry_is_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _widget(**{field: value})


def test_legacy_wide_geometry_is_accepted_by_the_schema() -> None:
    """Wider-than-grid input must parse, or legacy exports could never import.

    A board exported from the 60-column `free` grid carries x/w far past 12.
    The schema's job is to accept it; `layout_blob` is what guarantees nothing
    out-of-grid is ever *stored* (see test_dashboards.py).
    """
    legacy = _widget(x=48, w=12)
    assert (legacy.x, legacy.w) == (48, 12)
    assert legacy.x >= GRID_COLS  # i.e. this would be invalid as stored geometry


# ── legacy granularity marker ────────────────────────────────────────────────


def test_legacy_granularity_is_read_but_never_re_emitted() -> None:
    """The marker decodes pre-v2 geometry, then must disappear.

    If it survived a round-trip, a coerced board would be rescaled again on
    every subsequent write.
    """
    settings = CanvasSettings.model_validate({"granularity": "free", "chrome": {"border": False}})
    assert settings.legacy_granularity == "free"

    dumped = settings.model_dump()
    assert "granularity" not in dumped
    assert "legacy_granularity" not in dumped
    assert dumped["grid_version"] == 2
    # Unrelated keys still round-trip.
    assert dumped["chrome"] == {"border": False}

    # Re-validating the dump yields a board with no marker: idempotent.
    assert CanvasSettings.model_validate(dumped).legacy_granularity is None


def test_settings_without_granularity_is_v2() -> None:
    assert CanvasSettings().legacy_granularity is None
    assert CanvasSettings().grid_version == 2
