"""/api/v1/admin/controls - admin-vs-user control policies (admin only).

The admin control framework's management surface. Lets an administrator put any
controllable thing into one of two modes:

* ``user`` (default) - each user owns their own value, exactly as before.
* ``admin`` - the administrator sets ONE shared value used for every user, who
  then sees a read-only "controlled by your administrator" state.

Two scopes:

* ``setting`` - a curated catalog of server-side settings (``ai.config``,
  ``analytics.config``, ``worker_concurrency``).
* ``card`` - one item per settings *card* a loaded module exposes (config / ai /
  model, from :func:`mate.api.modules.cards.derive_cards`), keyed
  ``"<module_id>:<card_id>"``. Locking a card pins only that card's slice of
  ``config_json``; the other cards stay per-user.

Gated by the Keycloak ``admin`` realm role. Secrets (AI keys) are never
serialized back - the catalog reports only ``secret_set``/``admin_value_set``
flags, and the AI value is merged blank-key-keeps-old on save, exactly like
``admin_storage``. The actual resolution happens at the per-user read
chokepoints via ``mate.api.policy``; see ``ai_config.load_ai_config`` and
``modules/loader._make_context``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy import distinct, select

from mate.api.ai_config import (
    AI_CONFIG_KEY,
    AiConfigOut,
    AiConfigPayload,
    Provider,
    ProviderConfig,
    mask_config,
    merge_ai_payload,
)
from mate.api.ai_models import FetchModelsResponse, fetch_provider_models
from mate.api.auth import AdminUserDep
from mate.api.db.models import ModuleInstall
from mate.api.db.session import SessionDep
from mate.api.jobs.runtime import (
    MAX_WORKERS,
    MIN_WORKERS,
    WORKER_CONCURRENCY_KEY,
    get_job_runtime,
    save_persisted_concurrency,
)
from mate.api.modules import get_module_loader
from mate.api.modules.cards import (
    CARD_AI,
    CARD_CONFIG,
    CARD_MODEL,
    CardSpec,
    card_key,
    derive_cards,
    parse_card_key,
)
from mate.api.policy import (
    MODE_ADMIN,
    MODE_USER,
    SCOPE_CARD,
    SCOPE_SETTING,
    get_policy,
    list_policies,
    set_policy,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin/controls", tags=["admin"])


# --------------------------------------------------------------------------
# Static settings catalog
# --------------------------------------------------------------------------


class _SettingSpec:
    __slots__ = ("description", "has_secret", "key", "label")

    def __init__(self, key: str, label: str, description: str, *, has_secret: bool) -> None:
        self.key = key
        self.label = label
        self.description = description
        self.has_secret = has_secret


_SETTINGS: tuple[_SettingSpec, ...] = (
    _SettingSpec(
        AI_CONFIG_KEY,
        "AI settings",
        "API keys, provider, and model used by MATE AI. Lock to set one shared key for all users.",
        has_secret=True,
    ),
    _SettingSpec(
        "analytics.config",
        "Usage analytics",
        "Whether product-usage analytics are captured. Lock to force on/off for all users.",
        has_secret=False,
    ),
    _SettingSpec(
        WORKER_CONCURRENCY_KEY,
        "Job worker concurrency",
        "Number of concurrent background workers. Already a system-wide admin "
        "value; surfaced here.",
        has_secret=False,
    ),
)
_SETTING_KEYS = {s.key for s in _SETTINGS}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class ControlItem(BaseModel):
    scope: Literal["setting", "card"]
    key: str
    label: str
    description: str | None = None
    control_mode: str = MODE_USER
    # Whether the admin value has been set (never the value itself for secrets).
    admin_value_set: bool = False
    # For non-secret settings/cards, the admin value can be safely echoed so the
    # editor can prefill it. Secrets (ai.config) leave this None and rely on
    # ``secret_set`` instead.
    admin_value: Any | None = None
    # True when *any* secret is stored in the admin value (ai.config only).
    secret_set: bool = False
    # The config card's JSON-schema so the editor can render inputs.
    config_schema: dict[str, Any] | None = None
    # Card scope only: which module + card this item controls, the card's human
    # title, and the manifest hints the admin UI renders the right editor from
    # (model_store for the model card, ai_models for the ai card).
    module_id: str | None = None
    card_id: str | None = None
    title: str | None = None
    model_store: dict[str, Any] | None = None
    ai_models: dict[str, Any] | None = None


class ControlItems(BaseModel):
    items: list[ControlItem]


class ControlUpdate(BaseModel):
    control_mode: Literal["user", "admin"]
    admin_value: Any | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ai_secret_set(value: Any | None) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        cfg = AiConfigPayload.model_validate(value)
    except Exception:
        return False
    return any(bool(getattr(cfg, p).api_key) for p in ("anthropic", "openai", "unigpt", "custom"))


def _setting_item(spec: _SettingSpec, policy_value: Any | None, mode: str) -> ControlItem:
    secret_set = spec.has_secret and _ai_secret_set(policy_value)
    return ControlItem(
        scope=SCOPE_SETTING,
        key=spec.key,
        label=spec.label,
        description=spec.description,
        control_mode=mode,
        admin_value_set=policy_value is not None,
        # Never echo a secret-bearing value (ai.config); safe for the rest.
        admin_value=None if spec.has_secret else policy_value,
        secret_set=secret_set,
    )


def _card_item(module_id: str, card: CardSpec, manifest: Any, policy: Any | None) -> ControlItem:
    """A per-card control item, carrying the manifest hints the admin UI renders
    the card's editor from (config schema / model store / ai models)."""
    return ControlItem(
        scope=SCOPE_CARD,
        key=card_key(module_id, card.card_id),
        module_id=module_id,
        card_id=card.card_id,
        label=manifest.name,
        title=card.title,
        control_mode=policy.control_mode if policy is not None else MODE_USER,
        admin_value_set=policy is not None and policy.admin_value_json is not None,
        admin_value=policy.admin_value_json if policy is not None else None,
        config_schema=(manifest.config_schema if card.card_id == CARD_CONFIG else None),
        model_store=(
            manifest.model_store.model_dump()
            if card.card_id == CARD_MODEL and manifest.model_store is not None
            else None
        ),
        ai_models=(
            manifest.ai_models.model_dump()
            if card.card_id == CARD_AI and manifest.ai_models is not None
            else None
        ),
    )


def _merge_ai_value(new: dict[str, Any], old: Any | None) -> dict[str, Any]:
    """Validate an ai.config admin value, keeping stored keys when blank - mirrors
    ``admin_storage._settings_from_in`` and ``routes/ai.put_config``."""
    incoming = AiConfigPayload.model_validate(new)
    prev = AiConfigPayload.model_validate(old) if isinstance(old, dict) else AiConfigPayload()

    def merge(p: ProviderConfig, q: ProviderConfig) -> ProviderConfig:
        return ProviderConfig(api_key=(p.api_key or None) or q.api_key, base_url=p.base_url)

    merged = incoming.model_copy(
        update={
            "anthropic": merge(incoming.anthropic, prev.anthropic),
            "openai": merge(incoming.openai, prev.openai),
            "unigpt": merge(incoming.unigpt, prev.unigpt),
            "custom": merge(incoming.custom, prev.custom),
        }
    )
    return merged.model_dump()


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/items", response_model=ControlItems)
async def list_items(
    user: AdminUserDep,
    session: SessionDep,
    scope: Literal["setting", "card"] = "setting",
) -> ControlItems:
    """The controllable catalog for ``scope`` joined to existing policy rows."""
    policies = {p.key: p for p in await list_policies(session, scope)}

    if scope == SCOPE_SETTING:
        items = [
            _setting_item(
                spec,
                policies[spec.key].admin_value_json if spec.key in policies else None,
                policies[spec.key].control_mode if spec.key in policies else MODE_USER,
            )
            for spec in _SETTINGS
        ]
        return ControlItems(items=items)

    # scope == card: one item per (module, card) each loaded module exposes.
    manifests: dict[str, Any] = {}
    try:
        loader = get_module_loader()
        for m in loader.manifests():
            manifests[m.id] = m
    except HTTPException:
        manifests = {}

    installed_ids = set((await session.scalars(select(distinct(ModuleInstall.module_id)))).all())

    items: list[ControlItem] = []
    emitted: set[str] = set()
    for mid in sorted(set(manifests) | installed_ids):
        manifest = manifests.get(mid)
        if manifest is None:
            # Installed but not loaded → its cards are unknown until it loads.
            continue
        for card in derive_cards(manifest):
            k = card_key(mid, card.card_id)
            emitted.add(k)
            items.append(_card_item(mid, card, manifest, policies.get(k)))

    # Orphan card policies whose module is no longer loaded - surfaced so an
    # admin can still see and clear a stale lock.
    for k, policy in policies.items():
        if k in emitted:
            continue
        parsed = parse_card_key(k)
        if parsed is None:
            continue
        omid, ocid = parsed
        items.append(
            ControlItem(
                scope=SCOPE_CARD,
                key=k,
                module_id=omid,
                card_id=ocid,
                label=omid,
                title=ocid,
                control_mode=policy.control_mode,
                admin_value_set=policy.admin_value_json is not None,
                admin_value=policy.admin_value_json,
            )
        )
    return ControlItems(items=items)


@router.put("/items/{scope}/{key}", response_model=ControlItem)
async def set_item(
    scope: Literal["setting", "card"],
    key: str,
    body: ControlUpdate,
    user: AdminUserDep,
    session: SessionDep,
) -> ControlItem:
    """Set the control mode (and, when locking, the shared admin value).

    Per-scope validation. ``control_mode="user"`` clears the admin value (so a
    later relock starts clean and never leaks a stale secret). Returns a masked
    echo (secrets reported as flags only).
    """
    if scope == SCOPE_SETTING and key not in _SETTING_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown setting {key!r}.")
    if scope == SCOPE_CARD and parse_card_key(key) is None:
        raise HTTPException(status_code=404, detail=f"Invalid card key {key!r}.")

    admin_value: Any | None = None
    if body.control_mode == MODE_ADMIN:
        if body.admin_value is None:
            # Flipping the lock switch sends no value: keep any previously stored
            # admin value and let the per-key editor set the real one later.
            # ai.config never echoes its secret value back, so the client cannot
            # resend it - the server must preserve it here rather than reject None.
            existing = await get_policy(session, scope, key)
            admin_value = existing.admin_value_json if existing is not None else None
        else:
            admin_value = await _validate_admin_value(scope, key, body.admin_value, session)

    row = await set_policy(
        session,
        scope,
        key,
        control_mode=body.control_mode,
        admin_value=admin_value,
        updated_by=user.id,
    )
    await session.commit()

    # worker_concurrency owns live state in routes/system.py - delegate, don't
    # fork: apply the locked value immediately so the pool resizes now.
    if (
        scope == SCOPE_SETTING
        and key == WORKER_CONCURRENCY_KEY
        and body.control_mode == MODE_ADMIN
        and isinstance(admin_value, int)
    ):
        applied = await get_job_runtime().set_concurrency(admin_value)
        await save_persisted_concurrency(applied)

    log.info(
        "admin_control_set",
        admin_id=user.id,
        scope=scope,
        key=key,
        mode=body.control_mode,
    )

    if scope == SCOPE_SETTING:
        spec = next(s for s in _SETTINGS if s.key == key)
        return _setting_item(spec, row.admin_value_json, row.control_mode)

    # scope == card
    parsed = parse_card_key(key)
    assert parsed is not None  # validated at the top of the handler
    module_id, card_id = parsed
    try:
        loaded = get_module_loader().loaded.get(module_id)
    except HTTPException:
        loaded = None
    if loaded is not None:
        card = next((c for c in derive_cards(loaded.manifest) if c.card_id == card_id), None)
        if card is not None:
            return _card_item(module_id, card, loaded.manifest, row)
    # Unknown / orphan card - echo minimal state.
    return ControlItem(
        scope=SCOPE_CARD,
        key=key,
        module_id=module_id,
        card_id=card_id,
        label=module_id,
        title=card_id,
        control_mode=row.control_mode,
        admin_value_set=row.admin_value_json is not None,
        admin_value=row.admin_value_json,
    )


# --------------------------------------------------------------------------
# Shared AI config editor
# --------------------------------------------------------------------------
#
# Mirrors the per-user ``routes/ai`` config/model endpoints, but reads/writes
# the ``ai.config`` ControlPolicy admin value and lists models with the shared
# admin key. Powers the same rich card editor (provider picker, model fetch,
# dropdowns) in Admin -> Controls.


async def _admin_ai_payload(session: SessionDep) -> AiConfigPayload:
    """The stored shared ai.config admin value as a payload (empty when unset)."""
    existing = await get_policy(session, SCOPE_SETTING, AI_CONFIG_KEY)
    value = existing.admin_value_json if existing is not None else None
    if isinstance(value, dict):
        return AiConfigPayload.model_validate(value)
    return AiConfigPayload()


@router.get("/ai/config", response_model=AiConfigOut)
async def get_ai_config(user: AdminUserDep, session: SessionDep) -> AiConfigOut:
    """Masked shared AI config - never echoes a key (mirrors GET /ai/config)."""
    existing = await get_policy(session, SCOPE_SETTING, AI_CONFIG_KEY)
    controlled = existing is not None and existing.control_mode == MODE_ADMIN
    cfg = await _admin_ai_payload(session)
    return mask_config(cfg, controlled_by_admin=controlled)


@router.put("/ai/config", response_model=AiConfigOut)
async def put_ai_config(
    payload: AiConfigPayload, user: AdminUserDep, session: SessionDep
) -> AiConfigOut:
    """Save the shared AI config and lock ``ai.config`` to admin control.

    Blank keys keep the stored ones (the masked GET round-trip), exactly like
    the per-user PUT. Configuring the shared value implies admin control, so
    this sets ``control_mode=admin``.
    """
    existing = await _admin_ai_payload(session)
    merged = merge_ai_payload(payload, existing)
    await set_policy(
        session,
        SCOPE_SETTING,
        AI_CONFIG_KEY,
        control_mode=MODE_ADMIN,
        admin_value=merged.model_dump(),
        updated_by=user.id,
    )
    await session.commit()
    return mask_config(merged, controlled_by_admin=True)


@router.post("/ai/models/{provider}", response_model=FetchModelsResponse)
async def fetch_ai_models(
    provider: Annotated[Provider, Path()], user: AdminUserDep, session: SessionDep
) -> FetchModelsResponse:
    """List models for ``provider`` using the shared admin key (mirrors
    POST /ai/models/{provider})."""
    cfg = await _admin_ai_payload(session)
    p: ProviderConfig = getattr(cfg, provider)
    if not p.api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {provider!r}. Save a key above first.",
        )
    return await fetch_provider_models(provider, p.api_key, p.base_url)


async def _validate_admin_value(
    scope: str, key: str, value: Any | None, session: SessionDep
) -> Any | None:
    """Coerce/validate the admin value for ``(scope, key)`` before storing."""
    if scope == SCOPE_SETTING and key == AI_CONFIG_KEY:
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ai.config admin value must be an object.",
            )
        existing = await get_policy(session, scope, key)
        prev = existing.admin_value_json if existing is not None else None
        try:
            return _merge_ai_value(value, prev)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid AI config: {exc}",
            ) from exc

    if scope == SCOPE_SETTING and key == WORKER_CONCURRENCY_KEY:
        try:
            n = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="worker_concurrency admin value must be an integer.",
            ) from exc
        return max(MIN_WORKERS, min(MAX_WORKERS, n))

    if scope == SCOPE_SETTING and key == "analytics.config":
        from mate.api.routes.analytics import AnalyticsConfigPayload

        if not isinstance(value, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="analytics.config admin value must be an object.",
            )
        try:
            return AnalyticsConfigPayload.model_validate(value).model_dump()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid analytics config: {exc}",
            ) from exc

    if scope == SCOPE_CARD:
        # A card's admin value is the dict merged into config_json for the slice
        # it owns (config props / ``{"ai": {...}}`` / ``{config_key: name}``).
        # Best-effort: require an object so it flows into ModuleContext.config.
        parsed = parse_card_key(key)
        if parsed is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid card key {key!r}.",
            )
        _module_id, card_id = parsed
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Card admin config must be an object.",
            )
        if card_id == CARD_MODEL and not any(
            isinstance(v, str) and v.strip() for v in value.values()
        ):
            # Pinning a model card requires a concrete model name; an empty pin
            # would just make every user's autodetect fail with "no model".
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Select a model to lock; the shared value must name a model.",
            )
        return value

    return value
