"""Unit tests for the dataset transform engine (Phase 2)."""

from __future__ import annotations

import pytest

from mate.api.datasets.envelope import ColumnSpec, table_envelope
from mate.api.datasets.transforms import TransformError, apply_transforms, join_envelopes


def _sample():
    cols = [
        ColumnSpec(id="activity", label="Activity", type="string", role="dimension"),
        ColumnSpec(id="dev", label="Dev", type="integer", role="measure"),
    ]
    rows = [
        {"activity": "A", "dev": 5},
        {"activity": "B", "dev": 2},
        {"activity": "A", "dev": 3},
    ]
    return table_envelope(cols, rows)


def test_filter_gte() -> None:
    out = apply_transforms(
        _sample(), [{"op": "filter", "filters": [{"field": "dev", "op": "gte", "value": 3}]}]
    )
    devs = sorted(r["dev"] for r in out.data.rows)  # type: ignore[attr-defined]
    assert devs == [3, 5]


def test_select_projects_columns() -> None:
    out = apply_transforms(_sample(), [{"op": "select", "columns": ["activity"]}])
    assert [c.id for c in out.schema_.columns] == ["activity"]
    assert all(set(r.keys()) == {"activity"} for r in out.data.rows)  # type: ignore[attr-defined]


def test_sort_desc_then_limit() -> None:
    out = apply_transforms(
        _sample(),
        [{"op": "sort", "by": "dev", "dir": "desc"}, {"op": "limit", "n": 1}],
    )
    assert len(out.data.rows) == 1  # type: ignore[attr-defined]
    assert out.data.rows[0]["dev"] == 5  # type: ignore[attr-defined]


def test_aggregate_sum_by_group() -> None:
    out = apply_transforms(
        _sample(),
        [
            {
                "op": "aggregate",
                "group_by": ["activity"],
                "aggregations": [{"column": "dev", "fn": "sum", "as": "total"}],
            }
        ],
    )
    by = {r["activity"]: r["total"] for r in out.data.rows}  # type: ignore[attr-defined]
    assert by == {"A": 8, "B": 2}


def test_unknown_op_raises() -> None:
    with pytest.raises(TransformError):
        apply_transforms(_sample(), [{"op": "bogus"}])


def test_empty_chain_passthrough() -> None:
    env = _sample()
    assert apply_transforms(env, []) is env


def test_computed_and_rename() -> None:
    out = apply_transforms(
        _sample(),
        [
            {"op": "computed", "as": "doubled", "operator": "*", "left": "dev", "right": 2},
            {"op": "rename", "from": "activity", "to": "act"},
        ],
    )
    cols = [c.id for c in out.schema_.columns]
    assert "doubled" in cols and "act" in cols
    by = {r["act"]: r["doubled"] for r in out.data.rows}  # type: ignore[attr-defined]
    assert by["B"] == 4


def test_dedupe() -> None:
    cols = [ColumnSpec(id="x", label="X", type="string", role="dimension")]
    env = table_envelope(cols, [{"x": "a"}, {"x": "a"}, {"x": "b"}])
    out = apply_transforms(env, [{"op": "dedupe"}])
    assert sorted(r["x"] for r in out.data.rows) == ["a", "b"]  # type: ignore[attr-defined]


def test_pivot() -> None:
    cols = [
        ColumnSpec(id="cat", label="Cat", type="string", role="dimension"),
        ColumnSpec(id="grp", label="Grp", type="string", role="dimension"),
        ColumnSpec(id="v", label="V", type="integer", role="measure"),
    ]
    env = table_envelope(
        cols,
        [
            {"cat": "A", "grp": "x", "v": 1},
            {"cat": "A", "grp": "y", "v": 2},
            {"cat": "B", "grp": "x", "v": 3},
        ],
    )
    out = apply_transforms(
        env, [{"op": "pivot", "index": ["cat"], "columns": "grp", "values": "v", "agg": "sum"}]
    )
    by = {r["cat"]: r for r in out.data.rows}  # type: ignore[attr-defined]
    assert by["A"]["x"] == 1 and by["A"]["y"] == 2 and by["B"]["x"] == 3


def test_join() -> None:
    left = table_envelope(
        [
            ColumnSpec(id="id", label="Id", type="string", role="id"),
            ColumnSpec(id="a", label="A", type="integer", role="measure"),
        ],
        [{"id": "1", "a": 10}, {"id": "2", "a": 20}],
    )
    right = table_envelope(
        [
            ColumnSpec(id="id", label="Id", type="string", role="id"),
            ColumnSpec(id="b", label="B", type="integer", role="measure"),
        ],
        [{"id": "1", "b": 100}, {"id": "3", "b": 300}],
    )
    out = join_envelopes(left, right, {"on": "id", "how": "inner"})
    rows = out.data.rows  # type: ignore[attr-defined]
    assert len(rows) == 1 and rows[0]["id"] == "1" and rows[0]["a"] == 10 and rows[0]["b"] == 100
