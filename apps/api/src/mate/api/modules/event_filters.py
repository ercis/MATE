"""Shared SQL builders for the Events-tab column filters.

Two consumers need to turn a list of ``{field, op, value?}`` filter entries
into SQL, and they need it in two different shapes:

* The events route binds values as ``?`` parameters (``build_filter_where``).
* :class:`EventLogAccess` bakes the *applied* filter straight into a
  ``CREATE VIEW`` - and DuckDB rejects parameter binding inside a view
  definition - so it needs the predicate with inlined, escaped literals
  (``render_filter_sql``).

Both paths share one validation + op vocabulary so the editor preview and the
materialised module-facing dataset can never diverge.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

# Ops the editor can send. ``in`` backs the Excel-style "pick from the unique
# values" checklist; the rest are the single-value operators.
FILTER_OPS: frozenset[str] = frozenset(
    {"contains", "equals", "gte", "lte", "is_null", "is_not_null", "in"}
)

# Ops that don't carry a value.
_VALUELESS_OPS: frozenset[str] = frozenset({"is_null", "is_not_null"})


def quote_ident(name: str) -> str:
    """DuckDB identifier quoting - doubles internal quotes."""
    return '"' + name.replace('"', '""') + '"'


def validate_filters(filters: list[dict[str, Any]], column_names: set[str]) -> list[dict[str, Any]]:
    """Validate shape/op/field of each entry, raising HTTP 422 on problems.

    Returns the same list so callers can use it inline.
    """
    for entry in filters:
        field = entry.get("field")
        op = entry.get("op")
        if not isinstance(field, str):
            raise HTTPException(status_code=422, detail="filter.field must be a string.")
        if op not in FILTER_OPS:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported filter op {op!r}; allowed: {sorted(FILTER_OPS)}",
            )
        if field not in column_names:
            raise HTTPException(status_code=422, detail=f"Unknown filter field: {field!r}.")
        if op == "in" and not isinstance(entry.get("value"), list):
            raise HTTPException(status_code=422, detail="filter op 'in' requires a list value.")
    return filters


def build_filter_where(
    filters: list[dict[str, Any]], column_names: set[str]
) -> tuple[list[str], list[Any]]:
    """Parameterised predicate fragments + bind params for the column filters.

    Identifiers are validated against ``column_names``; values always flow
    through ``?`` placeholders. Returns ``(clauses, params)`` so the caller can
    splice the clauses into a larger ``WHERE``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    for f in filters:
        field = f["field"]
        if field not in column_names:
            raise HTTPException(status_code=422, detail=f"Unknown filter field: {field!r}.")
        ident = quote_ident(field)
        op = f["op"]
        if op == "is_null":
            clauses.append(f"{ident} IS NULL")
        elif op == "is_not_null":
            clauses.append(f"{ident} IS NOT NULL")
        elif op == "contains":
            clauses.append(f"CAST({ident} AS VARCHAR) ILIKE ?")
            params.append(f"%{f.get('value', '')}%")
        elif op == "equals":
            clauses.append(f"{ident} = ?")
            params.append(f.get("value"))
        elif op == "gte":
            clauses.append(f"{ident} >= ?")
            params.append(f.get("value"))
        elif op == "lte":
            clauses.append(f"{ident} <= ?")
            params.append(f.get("value"))
        elif op == "in":
            values = f.get("value") or []
            if not values:
                # An empty pick-list matches nothing - make that explicit
                # rather than degrading to "no filter".
                clauses.append("FALSE")
                continue
            placeholders = ", ".join("?" for _ in values)
            clauses.append(f"CAST({ident} AS VARCHAR) IN ({placeholders})")
            params.extend(str(v) for v in values)
    return clauses, params


def render_filter_sql(filters: list[dict[str, Any]], column_names: set[str]) -> str:
    """Predicate with inlined literals - for embedding in a ``CREATE VIEW``.

    DuckDB won't bind ``?`` inside a view definition, so values are rendered as
    escaped SQL literals here. Identifiers are still validated against
    ``column_names`` and strings are single-quote-escaped, so this stays
    injection-safe for the closed op/value vocabulary the editor produces.
    Returns ``""`` when there is nothing to filter.
    """
    clauses: list[str] = []
    for f in filters:
        field = f["field"]
        if field not in column_names:
            # Stale filter referencing a since-removed column - drop it rather
            # than poisoning every downstream read.
            continue
        ident = quote_ident(field)
        op = f["op"]
        if op == "is_null":
            clauses.append(f"{ident} IS NULL")
        elif op == "is_not_null":
            clauses.append(f"{ident} IS NOT NULL")
        elif op == "contains":
            like = "%" + str(f.get("value", "")) + "%"
            clauses.append(f"CAST({ident} AS VARCHAR) ILIKE {_lit(like)}")
        elif op == "equals":
            clauses.append(f"{ident} = {_lit(f.get('value'))}")
        elif op == "gte":
            clauses.append(f"{ident} >= {_lit(f.get('value'))}")
        elif op == "lte":
            clauses.append(f"{ident} <= {_lit(f.get('value'))}")
        elif op == "in":
            values = f.get("value") or []
            if not values:
                clauses.append("FALSE")
                continue
            rendered = ", ".join(_lit(str(v)) for v in values)
            clauses.append(f"CAST({ident} AS VARCHAR) IN ({rendered})")
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _lit(value: Any) -> str:
    """Render a Python value as a safe DuckDB SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "NULL" if math.isnan(value) or math.isinf(value) else repr(value)
    return "'" + str(value).replace("'", "''") + "'"
