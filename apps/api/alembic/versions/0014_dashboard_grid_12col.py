"""dashboards - rescale stored geometry onto the fixed 12-column grid

Revision ID: 0014_dashboard_grid_12col
Revises: 0013_drop_storage_config
Create Date: 2026-07-28

Boards used to carry a per-board snap level (``settings.granularity``) that
decided the column count: ``free`` 60, ``fine`` 40, ``medium`` 24, ``low`` 12.
A card's ``x``/``w`` were therefore only meaningful relative to that level -
and so was a widget's declared ``min_w``, which is why a manifest minimum
could render 3.6x larger on one board than another. The grid is now a single
fixed 12 columns (``schemas.dashboards.GRID_COLS``) with absolute pixel floors
declared on the widget instead.

This data-only migration rewrites ``dashboards.layout_json``:

* ``x``/``w`` are scaled by ``12 / from_cols`` and clamped into the grid.
* ``y``/``h`` are left alone - the new grid keeps the old ``medium`` row height
  (18px), so vertical geometry is preserved exactly.
* Overlaps introduced by the rescale are resolved by pushing cards *down*
  (see ``_push_down``). Collapsing 60 columns into 12 divides ``x`` by five, so
  cards that sat in distinct columns routinely land on the same cell; a
  faithful rescale alone would silently stack them.
* ``settings.granularity`` is dropped and ``settings.grid_version`` set to 2.

Idempotent: a board that already has no ``granularity`` key is skipped, and
``CanvasSettings`` never re-emits the key, so a migrated board stays migrated.
Boards that were already on ``low`` (12 columns) only get the marker swap.

``downgrade`` is a deliberate no-op: the original granularity is not
recoverable from the rescaled geometry, and a 12-column board is still valid
under the old code path (the old ``low`` level was also 12 columns).
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0014_dashboard_grid_12col"
down_revision: str | None = "0013_drop_storage_config"
branch_labels = None
depends_on = None

# Kept local rather than imported from `mate.api.schemas.dashboards`: a
# migration must describe the schema as of its own revision, so it cannot move
# when the application constants later do.
GRID_COLS = 12
GRID_VERSION = 2
MAX_ROW = 400
MAX_H = 48
LEGACY_COLS: dict[str, int] = {"free": 60, "fine": 40, "medium": 24, "low": 12}


def _table() -> sa.Table:
    return sa.table(
        "dashboards",
        sa.column("id", sa.String()),
        sa.column("layout_json", sa.JSON()),
    )


def _as_obj(value: object) -> object:
    """Defensive JSON decode - the JSON column type usually deserializes, but a
    raw string slips through on some drivers."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _int(value: object, default: int) -> int:
    # bool is an int subclass - never meaningful geometry.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(value)


def _push_down(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve overlaps by moving cards straight down, never sideways.

    Walks in reading order and drops each card below anything it lands on, so
    relative order survives the rescale. Mirrors the canvas's ``reflowFree``.
    """
    placed: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda c: (c["y"], c["x"])):
        y = it["y"]
        moved = True
        guard = 0
        while moved and guard < 1000:
            guard += 1
            moved = False
            for other in placed:
                if (
                    it["x"] < other["x"] + other["w"]
                    and it["x"] + it["w"] > other["x"]
                    and y < other["y"] + other["h"]
                    and y + it["h"] > other["y"]
                ):
                    y = other["y"] + other["h"]
                    moved = True
        placed.append({**it, "y": min(y, MAX_ROW)})
    return placed


def _rescale(items: list[Any], from_cols: int) -> list[dict[str, Any]]:
    factor = GRID_COLS / from_cols
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        it: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
        h = max(1, min(MAX_H, _int(it.get("h"), 8)))
        y = max(0, min(MAX_ROW, _int(it.get("y"), 0)))
        w = max(1, min(GRID_COLS, round(_int(it.get("w"), 6) * factor)))
        # Clamp x against the scaled width so no card hangs off the right edge.
        x = max(0, min(GRID_COLS - w, round(_int(it.get("x"), 0) * factor)))
        it.update(x=x, y=y, w=w, h=h)
        out.append(it)
    return _push_down(out)


def upgrade() -> None:
    bind = op.get_bind()
    if "dashboards" not in set(sa.inspect(bind).get_table_names()):
        return
    dashboards = _table()

    for row in bind.execute(sa.select(dashboards)).mappings():
        blob = _as_obj(row["layout_json"])
        if not isinstance(blob, dict):
            continue
        settings = blob.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        gran = settings.get("granularity")
        if gran is None:
            continue  # already v2 (or never had a granularity) - nothing to do

        from_cols = LEGACY_COLS.get(gran if isinstance(gran, str) else "", 24)
        raw_items = blob.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        # `low` was already 12 columns, so its geometry is valid as-is; only the
        # marker swap below applies.
        new_items = _rescale(items, from_cols) if from_cols != GRID_COLS else items

        new_settings = {k: v for k, v in settings.items() if k != "granularity"}
        new_settings["grid_version"] = GRID_VERSION
        bind.execute(
            dashboards.update()
            .where(dashboards.c.id == row["id"])
            .values(layout_json={**blob, "items": new_items, "settings": new_settings})
        )


def downgrade() -> None:
    """No-op. See the module docstring: the pre-v2 granularity cannot be
    recovered from rescaled geometry, and a 12-column board remains valid
    under the old code path."""
