"""Curated starter dashboards ("start from template").

Boards otherwise start blank; these seed a new board with a hand-picked set of
cards. Definitions are server-side so every template's ``items`` validate
against the real :class:`DashboardItem` schema at *import* time - each placement
is built as a ``DashboardItem`` instance, so an invalid card fails module import
loudly rather than shipping a broken board. ``create_dashboard`` seeds from one
of these when the request carries a ``template_id``.

Templates only reference widgets from ``default_enabled`` modules
(``discovery``, ``performance``, ``complexity``) whose precompute runs on
``log.imported``, so a template renders for any freshly imported case-centric
log with no extra setup. Geometry is authored directly on the fixed 12-column
grid (``schemas.dashboards.GRID_COLS``), so templates carry no legacy marker
and ``coerce_grid`` is a no-op for them.

These ``DashboardItem``s are constructed at *import* time, so a geometry value
that violates the schema bounds fails API startup rather than one create call.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mate.api.schemas.dashboards import CanvasSettings, DashboardItem
from mate.api.schemas.event_logs import LogModel


class DashboardTemplateDef(BaseModel):
    """Full server-side definition of a starter board, incl. its seeded cards.

    The public listing (``schemas.dashboards.DashboardTemplate``) exposes only
    the label + a card-count preview; the ``items`` here are applied at create
    time when a request supplies this template's ``id``.
    """

    id: str
    name: str
    description: str
    log_model: LogModel = "case_centric"
    items: list[DashboardItem]
    settings: CanvasSettings = Field(default_factory=CanvasSettings)


def _widget(
    i: str,
    module_id: str,
    widget_id: str,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    config: dict[str, Any] | None = None,
) -> DashboardItem:
    """A validated ``kind="widget"`` placement (module-authored card)."""
    return DashboardItem(
        i=i,
        kind="widget",
        module_id=module_id,
        widget_id=widget_id,
        x=x,
        y=y,
        w=w,
        h=h,
        config=config or {},
    )


DASHBOARD_TEMPLATES: list[DashboardTemplateDef] = [
    DashboardTemplateDef(
        id="process-overview",
        name="Process Overview",
        description=(
            "The discovered process at a glance - summary counts, the "
            "directly-follows map, and the most frequent activities."
        ),
        items=[
            _widget("overview", "discovery", "process-overview", x=0, y=0, w=4, h=8),
            _widget("map", "discovery", "process-map", x=4, y=0, w=8, h=13),
            _widget(
                "activities",
                "discovery",
                "activity-frequency",
                x=0,
                y=8,
                w=4,
                h=9,
                config={"top_n": 10},
            ),
        ],
    ),
    DashboardTemplateDef(
        id="performance",
        name="Performance & Bottlenecks",
        description=(
            "Where the process spends time - cycle-time KPIs, the slowest "
            "activities, and per-activity throughput."
        ),
        items=[
            _widget("kpis", "performance", "kpi-overview", x=0, y=0, w=4, h=15),
            _widget(
                "bottlenecks",
                "performance",
                "bottlenecks",
                x=4,
                y=0,
                w=4,
                h=10,
                config={"top_n": 8},
            ),
            _widget(
                "throughput",
                "performance",
                "activity-throughput",
                x=8,
                y=0,
                w=4,
                h=10,
                config={"top_n": 10},
            ),
        ],
    ),
    DashboardTemplateDef(
        id="complexity",
        name="Complexity & Structure",
        description=(
            "How tangled the process is - headline complexity metrics, entropy "
            "comparison, and the activity mix driving variety."
        ),
        items=[
            _widget("metrics", "complexity", "complexity-metrics", x=0, y=0, w=4, h=15),
            _widget("entropy", "complexity", "entropy-comparison", x=4, y=0, w=8, h=8),
            _widget(
                "activities",
                "discovery",
                "activity-frequency",
                x=4,
                y=8,
                w=8,
                h=9,
                config={"top_n": 12},
            ),
        ],
    ),
]

_TEMPLATES_BY_ID: dict[str, DashboardTemplateDef] = {t.id: t for t in DASHBOARD_TEMPLATES}


def list_templates() -> list[DashboardTemplateDef]:
    """All curated starter boards, in display order."""
    return DASHBOARD_TEMPLATES


def get_template(template_id: str) -> DashboardTemplateDef | None:
    """The template with ``template_id``, or ``None`` if unknown."""
    return _TEMPLATES_BY_ID.get(template_id)
