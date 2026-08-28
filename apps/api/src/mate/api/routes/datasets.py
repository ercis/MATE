"""/api/v1/datasets - catalog of module *data* outputs for generic visualization.

A *dataset* is a module's named, typed data output (manifest ``datasets:``)
rendered by the platform's generic visualization layer instead of a
module-authored widget. This router only exposes the **catalog**; the data
itself is fetched by the frontend straight from the module's own route
(``GET /api/v1/modules/{module_id}{route}``), so the per-request ephemeral
event filter and result-cache variant apply unchanged - no new data path.

Mirrors ``GET /api/v1/modules/cards`` (the widget catalog): it returns every
owned module's datasets with their ``log_models`` and the Dashboards palette
filters by the board's model client-side.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from mate.api.auth import CurrentUserDep
from mate.api.datasets.adapters import resolve_dataset
from mate.api.datasets.envelope import DatasetEnvelope
from mate.api.datasets.transforms import TransformError, apply_transforms
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.installs import user_module_ids, user_owns_module
from mate.api.modules.loader import decode_event_filter_header
from mate.api.schemas.event_logs import LogModel

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetCatalogEntry(BaseModel):
    """One module data output the Dashboards palette can drop as a generic-viz
    card. Aggregated from every owned module's ``datasets:`` so the palette can
    render the catalog without loading any bundle - the data is fetched lazily
    from ``route`` when the card mounts."""

    module_id: str
    module_name: str
    dataset_id: str
    title: str
    description: str | None = None
    icon: str | None = None
    # Data shape this dataset produces - drives which generic viz can render it.
    shape: Literal["table", "graph", "kpi", "tree", "blob"]
    # Module sub-route (leading slash) the frontend calls under
    # /api/v1/modules/{module_id} to fetch the data.
    route: str
    # Log model(s) this dataset applies to. The palette only offers a dataset
    # whose models include the board's model (case-centric vs OCEL).
    log_models: list[LogModel] = Field(default_factory=lambda: ["case_centric"])
    params_schema: dict[str, Any] | None = None


@router.get("/catalog", response_model=list[DatasetCatalogEntry])
async def list_datasets(session: SessionDep, user: CurrentUserDep) -> list[DatasetCatalogEntry]:
    """Catalog of every dataset exposed by the modules this user owns.

    Ordering is stable (module, then declared dataset order) so the palette
    doesn't reshuffle between loads.
    """
    try:
        loader = get_module_loader()
    except HTTPException:
        return []
    manifests = loader.manifests()
    if not manifests:
        return []

    owned = await user_module_ids(session, user.id)
    entries: list[DatasetCatalogEntry] = []
    for m in manifests:
        if m.id not in owned:
            continue
        for d in m.datasets:
            entries.append(
                DatasetCatalogEntry(
                    module_id=m.id,
                    module_name=m.name,
                    dataset_id=d.id,
                    title=d.title or d.id.replace("-", " ").replace("_", " ").title(),
                    description=d.description,
                    icon=d.icon,
                    shape=d.shape,
                    route=d.route,
                    log_models=d.log_models,
                    params_schema=d.params_schema,
                )
            )
    return entries


async def _assert_owns(session: SessionDep, user_id: str, module_id: str) -> None:
    if not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not installed.")


@router.get("/{module_id}/{dataset_id}", response_model=DatasetEnvelope)
async def get_dataset(
    module_id: str,
    dataset_id: str,
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    x_ff_event_filter: str | None = Header(default=None, alias="X-FF-Event-Filter"),
) -> DatasetEnvelope:
    """Resolve a module dataset to a canonical envelope (server-side). Honors the
    ephemeral ``X-FF-Event-Filter`` header exactly like the module's own route."""
    await _assert_owns(session, user.id, module_id)
    loader = get_module_loader()
    try:
        return await resolve_dataset(
            loader,
            module_id,
            dataset_id,
            log_id,
            user.id,
            filter_override=decode_event_filter_header(x_ff_event_filter),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class TransformRequest(BaseModel):
    log_id: str
    transforms: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/{module_id}/{dataset_id}/transform", response_model=DatasetEnvelope)
async def transform_dataset(
    module_id: str,
    dataset_id: str,
    body: TransformRequest,
    session: SessionDep,
    user: CurrentUserDep,
    x_ff_event_filter: str | None = Header(default=None, alias="X-FF-Event-Filter"),
) -> DatasetEnvelope:
    """Resolve a module dataset then apply an ordered transform chain over it."""
    await _assert_owns(session, user.id, module_id)
    loader = get_module_loader()
    try:
        env = await resolve_dataset(
            loader,
            module_id,
            dataset_id,
            body.log_id,
            user.id,
            filter_override=decode_event_filter_header(x_ff_event_filter),
        )
        return apply_transforms(env, body.transforms)
    except TransformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
