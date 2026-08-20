"""Shared AI-config types + accessors.

Lives outside ``routes/`` on purpose: importing anything from ``routes/``
triggers ``routes/__init__.py``, which mounts every router. That would
create a cycle when ``modules/refactor.py`` (used by ``routes/modules.py``)
needs to read the user's API key. Keep this module free of FastAPI route
declarations.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import UserSetting

AI_CONFIG_KEY = "ai.config"

Provider = Literal["anthropic", "openai", "unigpt", "custom"]


class ProviderConfig(BaseModel):
    api_key: str | None = None
    base_url: str | None = None


class AiConfigPayload(BaseModel):
    system_prompt: str = ""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    unigpt: ProviderConfig = Field(default_factory=ProviderConfig)
    custom: ProviderConfig = Field(default_factory=ProviderConfig)
    selected_provider: Provider | None = None
    selected_model: str | None = None
    # Optional cheaper model (same provider) used only for the intent classifier
    # behind MATE AI's navigation routing. Falls back to ``selected_model`` when
    # unset, so existing configs keep working untouched.
    classifier_model: str | None = None
    # Opt-in: when true, MATE AI may read the user's process data (names + stats
    # like variant/case counts) to answer questions and navigate to named
    # processes. Sends that data to the configured provider, so it defaults off.
    allow_process_data: bool = False


class AiConfigOut(BaseModel):
    """Masked AI config returned to the browser - never carries an API key.

    The keys live only in SQLite and flow to the outbound provider call via
    ``load_ai_config``; here we expose only *whether* a key is stored per
    provider (``*_key_set``) so the form can show a "leave blank to keep"
    placeholder, plus ``controlled_by_admin`` so the UI can render a read-only
    "controlled by your administrator" state. When admin-controlled the masked
    *shared* config is shown to every user.
    """

    system_prompt: str = ""
    anthropic_base_url: str | None = None
    openai_base_url: str | None = None
    unigpt_base_url: str | None = None
    custom_base_url: str | None = None
    anthropic_key_set: bool = False
    openai_key_set: bool = False
    unigpt_key_set: bool = False
    custom_key_set: bool = False
    selected_provider: Provider | None = None
    selected_model: str | None = None
    classifier_model: str | None = None
    allow_process_data: bool = False
    controlled_by_admin: bool = False


def mask_config(cfg: AiConfigPayload, *, controlled_by_admin: bool) -> AiConfigOut:
    return AiConfigOut(
        system_prompt=cfg.system_prompt,
        anthropic_base_url=cfg.anthropic.base_url,
        openai_base_url=cfg.openai.base_url,
        unigpt_base_url=cfg.unigpt.base_url,
        custom_base_url=cfg.custom.base_url,
        anthropic_key_set=bool(cfg.anthropic.api_key),
        openai_key_set=bool(cfg.openai.api_key),
        unigpt_key_set=bool(cfg.unigpt.api_key),
        custom_key_set=bool(cfg.custom.api_key),
        selected_provider=cfg.selected_provider,
        selected_model=cfg.selected_model,
        classifier_model=cfg.classifier_model,
        allow_process_data=cfg.allow_process_data,
        controlled_by_admin=controlled_by_admin,
    )


def merge_provider(new: ProviderConfig, old: ProviderConfig) -> ProviderConfig:
    """Keep the stored key when the incoming one is blank (the GET masks keys,
    so a naive round-trip would otherwise wipe them) - mirrors
    ``admin_storage._settings_from_in``."""
    return ProviderConfig(
        api_key=(new.api_key or None) or old.api_key,
        base_url=new.base_url,
    )


def merge_ai_payload(payload: AiConfigPayload, existing: AiConfigPayload) -> AiConfigPayload:
    """Merge an incoming AI payload over the stored one, keeping blank keys."""
    return payload.model_copy(
        update={
            "anthropic": merge_provider(payload.anthropic, existing.anthropic),
            "openai": merge_provider(payload.openai, existing.openai),
            "unigpt": merge_provider(payload.unigpt, existing.unigpt),
            "custom": merge_provider(payload.custom, existing.custom),
        }
    )


def _load_config(row: UserSetting | None) -> AiConfigPayload:
    if row is None or not isinstance(row.value_json, dict):
        return AiConfigPayload()
    return AiConfigPayload.model_validate(row.value_json)


async def load_ai_config(session: AsyncSession, user_id: str) -> AiConfigPayload:
    """Effective AI config for ``user_id`` - the single read chokepoint.

    When an admin has locked ``ai.config`` (``mate.api.policy``), the shared
    admin value is returned for *every* user, so the resolved key flows from one
    place into ``_provider_creds`` and on to every outbound provider call.
    Otherwise the user's own ``ai.config`` UserSetting is used.
    """
    from mate.api.policy import SCOPE_SETTING, resolve

    admin_value, controlled = await resolve(session, SCOPE_SETTING, AI_CONFIG_KEY, user_id)
    if controlled:
        if isinstance(admin_value, dict):
            return AiConfigPayload.model_validate(admin_value)
        return AiConfigPayload()
    row = await session.get(UserSetting, (user_id, AI_CONFIG_KEY))
    return _load_config(row)


async def ai_control_state(session: AsyncSession, user_id: str) -> bool:
    """Whether ``ai.config`` is admin-controlled for ``user_id`` (routes mask/403)."""
    from mate.api.policy import SCOPE_SETTING, resolve

    _, controlled = await resolve(session, SCOPE_SETTING, AI_CONFIG_KEY, user_id)
    return controlled


async def _provider_creds(
    session: AsyncSession, provider: Provider, user_id: str
) -> tuple[str, str | None]:
    cfg = await load_ai_config(session, user_id)
    p = getattr(cfg, provider)
    if not p.api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {provider!r}. Save one in Settings → AI first.",
        )
    return p.api_key, p.base_url
