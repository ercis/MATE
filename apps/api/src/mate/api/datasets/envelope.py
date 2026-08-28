"""Canonical dataset envelope (Phase 2).

Mirror of the TS ``DatasetEnvelope`` (``apps/web/lib/visualizations/types.ts``)
so a server-resolved dataset renders with the exact same generic-viz
components. Field names that are camelCase on the TS side (``performanceSeconds``,
``sourceKind``, ``rowCount``) are kept camelCase here to keep the JSON identical
without alias gymnastics.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DatasetShape = Literal["table", "graph", "kpi", "tree", "blob"]
ColumnType = Literal["number", "integer", "string", "boolean", "datetime", "duration", "enum"]
FieldRole = Literal["dimension", "measure", "time", "id"]
KpiFormat = Literal["number", "integer", "percent", "duration"]


class ColumnSpec(BaseModel):
    id: str
    label: str
    type: ColumnType
    role: FieldRole | None = None


class TableData(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    value: float | None = None
    kind: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    value: float | None = None
    label: str | None = None
    performanceSeconds: float | None = None  # noqa: N815 - matches TS GraphEdge


class GraphData(BaseModel):
    directed: bool = True
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    start: list[str] | None = None
    end: list[str] | None = None


class KpiMeasure(BaseModel):
    key: str
    label: str
    value: float | str | None = None
    unit: str | None = None
    format: KpiFormat | None = None


class KpiData(BaseModel):
    items: list[KpiMeasure] = Field(default_factory=list)


class TreeNode(BaseModel):
    id: str
    label: str
    value: float | None = None
    children: list[TreeNode] = Field(default_factory=list)


class TreeData(BaseModel):
    root: TreeNode


class BlobData(BaseModel):
    media: str
    value: str


class DatasetSchema(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)


class DatasetMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    sourceKind: str | None = None  # noqa: N815 - matches TS meta.sourceKind
    rowCount: int | None = None  # noqa: N815 - matches TS meta.rowCount
    truncated: bool | None = None
    note: str | None = None


class DatasetEnvelope(BaseModel):
    shape: DatasetShape
    schema_: DatasetSchema = Field(default_factory=DatasetSchema, alias="schema")
    # `schema` is a reserved BaseModel attr name; expose it via alias so the JSON
    # key stays `schema` (what the frontend reads) while the Python attr is
    # `schema_`.
    model_config = ConfigDict(populate_by_name=True)
    data: TableData | GraphData | KpiData | TreeData | BlobData
    meta: DatasetMeta | None = None


TreeNode.model_rebuild()


def table_envelope(
    columns: list[ColumnSpec], rows: list[dict[str, Any]], *, meta: DatasetMeta | None = None
) -> DatasetEnvelope:
    return DatasetEnvelope(
        shape="table",
        schema=DatasetSchema(columns=columns),
        data=TableData(columns=columns, rows=rows),
        meta=meta,
    )
