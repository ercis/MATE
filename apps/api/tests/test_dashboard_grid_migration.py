"""0014_dashboard_grid_12col - rescale board geometry onto the 12-column grid.

Drives the real ``upgrade`` against an in-memory SQLite by monkeypatching the
migration's ``op`` proxy onto an Operations bound to our own connection - the
same harness as ``test_card_migration.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0014_dashboard_grid_12col.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0014", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str, mig, monkeypatch: pytest.MonkeyPatch, conn) -> None:
    ops = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(mig, "op", ops)
    getattr(mig, fn)()


def _card(i: str, x: int, y: int, w: int, h: int) -> dict[str, Any]:
    return {
        "i": i,
        "kind": "widget",
        "module_id": "discovery",
        "widget_id": "process-map",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "config": {},
    }


def _blob(items: list[dict[str, Any]], granularity: str | None) -> str:
    settings: dict[str, Any] = {"chrome": {"border": True}, "presets": []}
    if granularity is not None:
        settings["granularity"] = granularity
    return json.dumps({"items": items, "settings": settings})


def _seed(conn, rows: dict[str, str]) -> None:
    conn.exec_driver_sql("CREATE TABLE dashboards (id TEXT PRIMARY KEY, layout_json JSON)")
    stmt = sa.text("INSERT INTO dashboards (id, layout_json) VALUES (:i, :b)")
    for dash_id, blob in rows.items():
        conn.execute(stmt, {"i": dash_id, "b": blob})


def _read(conn, dash_id: str) -> dict[str, Any]:
    raw = conn.execute(
        sa.text("SELECT layout_json FROM dashboards WHERE id = :i"), {"i": dash_id}
    ).scalar_one()
    out = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(out, dict)
    return out


def test_rescales_each_legacy_granularity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pre-v2 column count maps onto 12 columns, keeping relative width."""
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed(
            conn,
            {
                # A half-width card at the horizontal midpoint, expressed in each
                # legacy grid. All four must land on the same 12-col geometry.
                "d-free": _blob([_card("a", x=30, y=0, w=30, h=10)], "free"),  # 60 cols
                "d-fine": _blob([_card("a", x=20, y=0, w=20, h=10)], "fine"),  # 40 cols
                "d-medium": _blob([_card("a", x=12, y=0, w=12, h=10)], "medium"),  # 24
                "d-low": _blob([_card("a", x=6, y=0, w=6, h=10)], "low"),  # already 12
            },
        )
        _run("upgrade", mig, monkeypatch, conn)

        for dash_id in ("d-free", "d-fine", "d-medium", "d-low"):
            blob = _read(conn, dash_id)
            card = blob["items"][0]
            assert (card["x"], card["w"]) == (6, 6), dash_id
            # y/h are row-based and the row height is unchanged, so they survive.
            assert (card["y"], card["h"]) == (0, 10), dash_id
            assert "granularity" not in blob["settings"], dash_id
            assert blob["settings"]["grid_version"] == 2, dash_id
            # Unrelated settings keys are preserved.
            assert blob["settings"]["chrome"] == {"border": True}, dash_id


def test_de_overlaps_after_a_column_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    """60 -> 12 divides x by five, so distinct columns collapse onto one cell.

    A faithful rescale alone would stack these three cards; the migration must
    push them down instead, preserving reading order.
    """
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed(
            conn,
            {
                "d": _blob(
                    [
                        _card("a", x=0, y=0, w=4, h=5),
                        _card("b", x=1, y=0, w=4, h=5),
                        _card("c", x=2, y=0, w=4, h=5),
                    ],
                    "free",
                )
            },
        )
        _run("upgrade", mig, monkeypatch, conn)

        cards = {c["i"]: c for c in _read(conn, "d")["items"]}
        assert len(cards) == 3  # nothing dropped
        # All three rescale to x=0,w=1 and must end up stacked vertically.
        assert [cards[i]["y"] for i in ("a", "b", "c")] == [0, 5, 10]

        # No pair overlaps.
        placed = list(cards.values())
        for idx, one in enumerate(placed):
            for other in placed[idx + 1 :]:
                overlaps = (
                    one["x"] < other["x"] + other["w"]
                    and one["x"] + one["w"] > other["x"]
                    and one["y"] < other["y"] + other["h"]
                    and one["y"] + one["h"] > other["y"]
                )
                assert not overlaps, (one, other)


def test_is_idempotent_and_skips_v2_boards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running must not rescale twice, and a v2 board is left alone.

    The marker is the whole guard: once `granularity` is gone the board is
    v2 forever, so a second upgrade is a no-op.
    """
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed(
            conn,
            {
                "legacy": _blob([_card("a", x=30, y=0, w=30, h=10)], "free"),
                # Already migrated: no marker, and geometry must not move.
                "v2": _blob([_card("a", x=6, y=0, w=6, h=10)], None),
            },
        )
        _run("upgrade", mig, monkeypatch, conn)
        after_first = _read(conn, "legacy")["items"][0]
        v2_after = _read(conn, "v2")["items"][0]
        assert (after_first["x"], after_first["w"]) == (6, 6)
        assert (v2_after["x"], v2_after["w"]) == (6, 6)

        _run("upgrade", mig, monkeypatch, conn)
        after_second = _read(conn, "legacy")["items"][0]
        assert (after_second["x"], after_second["w"]) == (6, 6)


def test_clamps_out_of_range_and_survives_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Geometry is clamped into the grid; a non-dict item is dropped, not fatal."""
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed(
            conn,
            {
                "d": json.dumps(
                    {
                        "items": [
                            # w rescales to 12; x must be pulled back to 0 so the
                            # card cannot hang off the right edge.
                            _card("wide", x=50, y=0, w=60, h=10),
                            "not-a-dict",
                            _card("tall", x=0, y=99999, w=6, h=999),
                        ],
                        "settings": {"granularity": "free"},
                    }
                ),
                # A board with no settings at all must not explode.
                "empty": json.dumps({"items": []}),
            },
        )
        _run("upgrade", mig, monkeypatch, conn)

        cards = {c["i"]: c for c in _read(conn, "d")["items"]}
        assert set(cards) == {"wide", "tall"}  # junk entry dropped
        assert (cards["wide"]["x"], cards["wide"]["w"]) == (0, 12)
        assert cards["tall"]["y"] <= mig.MAX_ROW
        assert cards["tall"]["h"] <= mig.MAX_H
        # No settings key at all => no marker => untouched.
        assert _read(conn, "empty")["items"] == []
