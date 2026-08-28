"""Transform catalog (Phase 2) - DuckDB-backed shaping over a table dataset.

A transform chain is a list of ``{op, ...}`` steps applied in order to a
``table``-shaped :class:`DatasetEnvelope`. Each step runs as SQL over the
upstream rows registered as a DuckDB relation, reusing the Events-tab filter SQL
builders so the editor preview and the materialised dataset never diverge.

Supported ops (v1): ``filter``, ``select``, ``sort``, ``limit``, ``aggregate``.
"""

from __future__ import annotations

import json
from typing import Any

from mate.api.datasets.envelope import (
    ColumnSpec,
    ColumnType,
    DatasetEnvelope,
    DatasetMeta,
    table_envelope,
)
from mate.api.modules.event_filters import build_filter_where, quote_ident

TRANSFORM_OPS: frozenset[str] = frozenset(
    {
        "filter",
        "select",
        "sort",
        "limit",
        "aggregate",
        "pivot",
        "unpivot",
        "computed",
        "rename",
        "dedupe",
        "join",
    }
)
# Ops compiled to SQL over the upstream table; the rest run in pandas.
_SQL_OPS: frozenset[str] = frozenset({"filter", "select", "sort", "limit", "aggregate"})
_AGG_FNS: frozenset[str] = frozenset({"sum", "avg", "count", "count_distinct", "min", "max"})
_AGG_PANDAS: dict[str, str] = {
    "sum": "sum",
    "avg": "mean",
    "count": "count",
    "min": "min",
    "max": "max",
}


class TransformError(ValueError):
    """Raised on a malformed transform step (bad op, unknown column, ...)."""


def _coltype(dtype: Any) -> ColumnType:
    import pandas as pd

    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    return "string"


def _role_for(col_type: ColumnType) -> str:
    if col_type in ("number", "integer", "duration"):
        return "measure"
    if col_type == "datetime":
        return "time"
    return "dimension"


def _columns_from_df(df: Any) -> list[ColumnSpec]:
    cols: list[ColumnSpec] = []
    for name in df.columns:
        col_type = _coltype(df[name].dtype)
        cols.append(
            ColumnSpec(
                id=str(name),
                label=str(name).replace("_", " ").strip().title(),
                type=col_type,
                role=_role_for(col_type),  # type: ignore[arg-type]
            )
        )
    return cols


def _json_records(df: Any) -> list[dict[str, Any]]:
    """``df.to_json(orient="records")`` with UTC offsets on datetime columns.

    Event-log datetimes are stored naive UTC; ``date_format="iso"`` would emit
    them without an offset, which browsers parse as *local* wall-clock. Localize
    naive datetime columns to UTC first so the JSON carries ``Z`` like every
    other datetime the API serves.
    """
    import pandas as pd

    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype) and getattr(dtype, "tz", None) is None:
            df[col] = df[col].dt.tz_localize("UTC")
    return json.loads(df.to_json(orient="records", date_format="iso") or "[]")


def _step_sql(step: dict[str, Any], columns: set[str]) -> tuple[str, list[Any]]:
    op = step.get("op")
    if op == "filter":
        filters = step.get("filters") or []
        clauses, params = build_filter_where(filters, columns)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return f"SELECT * FROM t{where}", params
    if op == "select":
        cols = [c for c in (step.get("columns") or []) if c in columns]
        if not cols:
            raise TransformError("select requires at least one known column.")
        projection = ", ".join(quote_ident(c) for c in cols)
        return f"SELECT {projection} FROM t", []
    if op == "sort":
        by = step.get("by")
        if by not in columns:
            raise TransformError(f"sort by unknown column {by!r}.")
        direction = "DESC" if str(step.get("dir", "asc")).lower() == "desc" else "ASC"
        return f"SELECT * FROM t ORDER BY {quote_ident(by)} {direction}", []
    if op == "limit":
        n = int(step.get("n", 100))
        if n < 0:
            raise TransformError("limit n must be >= 0.")
        return f"SELECT * FROM t LIMIT {n}", []
    if op == "aggregate":
        group_by = [c for c in (step.get("group_by") or []) if c in columns]
        aggs = step.get("aggregations") or []
        if not aggs:
            raise TransformError("aggregate requires at least one aggregation.")
        select_parts = [quote_ident(c) for c in group_by]
        for a in aggs:
            fn = str(a.get("fn", "sum")).lower()
            if fn not in _AGG_FNS:
                raise TransformError(f"unknown aggregate fn {fn!r}; allowed: {sorted(_AGG_FNS)}")
            col = a.get("column")
            alias = a.get("as") or (f"{fn}_{col}" if col else fn)
            if fn == "count" and not col:
                expr = "COUNT(*)"
            elif fn == "count_distinct":
                if col not in columns:
                    raise TransformError(f"aggregate over unknown column {col!r}.")
                expr = f"COUNT(DISTINCT {quote_ident(col)})"
            else:
                if col not in columns:
                    raise TransformError(f"aggregate over unknown column {col!r}.")
                expr = f"{fn.upper()}({quote_ident(col)})"
            select_parts.append(f"{expr} AS {quote_ident(alias)}")
        group_clause = (
            (" GROUP BY " + ", ".join(quote_ident(c) for c in group_by)) if group_by else ""
        )
        return f"SELECT {', '.join(select_parts)} FROM t{group_clause}", []
    raise TransformError(f"unsupported transform op {op!r}; allowed: {sorted(TRANSFORM_OPS)}")


def _pivot(df: Any, step: dict[str, Any]) -> Any:
    import pandas as pd

    index = [c for c in (step.get("index") or []) if c in df.columns]
    columns = step.get("columns")
    values = step.get("values")
    if not index or columns not in df.columns or values not in df.columns:
        raise TransformError("pivot needs an index, a `columns` field and a `values` field.")
    aggfunc = _AGG_PANDAS.get(str(step.get("agg", "sum")).lower(), "sum")
    pv = pd.pivot_table(
        df, index=index, columns=columns, values=values, aggfunc=aggfunc, fill_value=0
    )
    pv.columns = [str(c) for c in pv.columns]
    return pv.reset_index()


def _unpivot(df: Any, step: dict[str, Any]) -> Any:
    id_vars = [c for c in (step.get("id_vars") or []) if c in df.columns]
    value_vars = [c for c in (step.get("value_vars") or []) if c in df.columns]
    return df.melt(
        id_vars=id_vars or None,
        value_vars=value_vars or None,
        var_name=step.get("var_name", "variable"),
        value_name=step.get("value_name", "value"),
    )


def _computed(df: Any, step: dict[str, Any]) -> Any:
    import pandas as pd

    name = step.get("as")
    op = step.get("operator")
    if not name or op not in {"+", "-", "*", "/"}:
        raise TransformError("computed needs `as`, `operator` (+ - * /), `left` and `right`.")

    def operand(v: Any) -> Any:
        if isinstance(v, str) and v in df.columns:
            return pd.to_numeric(df[v], errors="coerce")
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            raise TransformError(f"computed operand {v!r} is not a column or number.") from exc

    lv, rv = operand(step.get("left")), operand(step.get("right"))
    df[name] = {"+": lv + rv, "-": lv - rv, "*": lv * rv, "/": lv / rv}[op]
    return df


def _rename(df: Any, step: dict[str, Any]) -> Any:
    frm, to = step.get("from"), step.get("to")
    if frm not in df.columns or not to:
        raise TransformError("rename needs an existing `from` column and a `to` name.")
    return df.rename(columns={frm: to})


def apply_transforms(env: DatasetEnvelope, transforms: list[dict[str, Any]]) -> DatasetEnvelope:
    """Apply an ordered transform chain to a table envelope, returning a new
    table envelope. SQL ops (filter/select/sort/limit/aggregate) run over a
    DuckDB relation; the rest run in pandas. Non-table shapes pass through when
    the chain is empty, else raise."""
    if env.shape != "table":
        if not transforms:
            return env
        raise TransformError("Transforms apply to table-shaped datasets only.")
    if not transforms:
        return env

    import duckdb
    import pandas as pd

    src = env.data
    col_ids = [c.id for c in env.schema_.columns] or list({k for r in src.rows for k in r})  # type: ignore[attr-defined]
    df = pd.DataFrame(src.rows, columns=col_ids) if src.rows else pd.DataFrame(columns=col_ids)  # type: ignore[attr-defined]

    con = duckdb.connect(":memory:")
    try:
        for step in transforms:
            op = step.get("op")
            if op in _SQL_OPS:
                con.register("t", df)
                sql, params = _step_sql(step, set(df.columns))
                df = con.execute(sql, params).df()
                con.unregister("t")
            elif op == "pivot":
                df = _pivot(df, step)
            elif op == "unpivot":
                df = _unpivot(df, step)
            elif op == "computed":
                df = _computed(df, step)
            elif op == "rename":
                df = _rename(df, step)
            elif op == "dedupe":
                subset = [c for c in (step.get("columns") or []) if c in df.columns]
                df = df.drop_duplicates(subset=subset or None).reset_index(drop=True)
            else:
                raise TransformError(
                    f"unsupported transform op {op!r}; allowed: {sorted(TRANSFORM_OPS - {'join'})}"
                )
    finally:
        con.close()

    columns = _columns_from_df(df)
    rows = _json_records(df)
    return table_envelope(
        columns,
        rows,
        meta=DatasetMeta(
            sourceKind=env.meta.sourceKind if env.meta else None,
            rowCount=len(rows),
            note=None if rows else "No rows after transforms.",
        ),
    )


def join_envelopes(
    left: DatasetEnvelope, right: DatasetEnvelope, step: dict[str, Any]
) -> DatasetEnvelope:
    """Join two table envelopes on a shared key (engine-level, two-input)."""
    if left.shape != "table" or right.shape != "table":
        raise TransformError("join requires two table datasets.")
    import duckdb
    import pandas as pd

    on = step.get("on")
    how = str(step.get("how", "inner")).lower()
    if how not in {"inner", "left", "right", "outer"}:
        raise TransformError("join `how` must be inner/left/right/outer.")
    ldf = pd.DataFrame(left.data.rows, columns=[c.id for c in left.schema_.columns])  # type: ignore[attr-defined]
    rdf = pd.DataFrame(right.data.rows, columns=[c.id for c in right.schema_.columns])  # type: ignore[attr-defined]
    if not isinstance(on, str) or on not in ldf.columns or on not in rdf.columns:
        raise TransformError(f"join key {on!r} must exist in both inputs.")
    join_kw = "FULL" if how == "outer" else how.upper()
    con = duckdb.connect(":memory:")
    try:
        con.register("l", ldf)
        con.register("r", rdf)
        out = con.execute(f"SELECT * FROM l {join_kw} JOIN r USING ({quote_ident(on)})").df()
    finally:
        con.close()
    columns = _columns_from_df(out)
    rows = _json_records(out)
    return table_envelope(
        columns,
        rows,
        meta=DatasetMeta(rowCount=len(rows), note=None if rows else "No rows after join."),
    )
