"""Per-card admin/user control - the lockable "cards" of a module's settings
page, derived from its manifest, and the ``config_json`` slice each one owns.

A module's settings page is composed of independently lockable cards, each
mapping to a **disjoint** slice of ``module_configs.config_json``:

* ``config`` - the top-level keys declared by ``config_schema.properties``
  (minus the keys the ai/model cards own).
* ``ai``     - the ``ai`` sub-object (``config_json["ai"]``).
* ``model``  - the model-store selection (``config_json[config_key]``, default
  ``"model"``); the loader additionally injects the
  ``__model_admin_locked__`` sentinel when this card is locked.

Each card is admin-lockable via one :class:`~mate.api.db.models.ControlPolicy`
keyed ``(scope="card", key="<module_id>:<card_id>")``. A locked card's stored
``admin_value`` is *exactly* the dict merged into ``config_json``
(``cfg_json.update(value)``), so the runtime overlay is a uniform one-liner per
card and a locked card never disturbs another card's per-user value.

Kept import-light (only ``mate.sdk.manifest`` + ``mate.api.policy``, neither of
which imports back here) - the same discipline ``mate.api.policy`` follows so it
can be consulted from the loader's read chokepoint without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.policy import SCOPE_CARD, resolve
from mate.sdk.manifest import Manifest

CARD_CONFIG = "config"
CARD_AI = "ai"
CARD_MODEL = "model"

# The ``config_json`` key the AI card owns, and the loader-injected runtime
# markers that tell a module's route to render/act read-only when its card is
# admin-locked (``/models`` for model, the Pinecone rebuild for ai). All are
# excluded from the config card's namespace so the cards stay disjoint.
AI_KEY = "ai"
MODEL_LOCK_SENTINEL = "__model_admin_locked__"
AI_LOCK_SENTINEL = "__ai_admin_locked__"


@dataclass(frozen=True)
class CardSpec:
    """One lockable card of a module's settings page."""

    card_id: str
    title: str
    # Only the model card: the config_json key the selection is stored under
    # (``ModelStoreManifest.config_key``). ``None`` for config / ai cards.
    config_key: str | None = None


def card_key(module_id: str, card_id: str) -> str:
    """The ``ControlPolicy`` key for a module's card: ``"<module_id>:<card_id>"``.

    Module ids are validated lowercase snake_case (``Manifest._validate_id``),
    so ``:`` never appears in one and :func:`parse_card_key` is unambiguous.
    """
    return f"{module_id}:{card_id}"


def parse_card_key(key: str) -> tuple[str, str] | None:
    """Inverse of :func:`card_key`. ``None`` when *key* is not a card key."""
    module_id, sep, card_id = key.rpartition(":")
    if not sep or not module_id or not card_id:
        return None
    return module_id, card_id


def derive_cards(manifest: Manifest) -> list[CardSpec]:
    """The lockable cards *manifest* exposes.

    The single source of card presence - the resolver, the admin catalog, and
    (mirrored) the settings page all go through here so they never disagree.
    Presence rules match the settings page's own card-render checks:

    * config iff ``config_schema.properties`` is non-empty
    * ai iff ``ai_models`` declares an ``llm`` or ``embedding`` slot
    * model iff ``model_store`` is present
    """
    cards: list[CardSpec] = []

    schema = manifest.config_schema or {}
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        cards.append(CardSpec(CARD_CONFIG, "Configuration"))

    ai = manifest.ai_models
    if ai is not None and (ai.llm is not None or ai.embedding is not None):
        cards.append(CardSpec(CARD_AI, "AI models"))

    store = manifest.model_store
    if store is not None:
        cards.append(
            CardSpec(CARD_MODEL, store.title or "Model files", config_key=store.config_key)
        )

    return cards


def _reserved_top_level_keys(cards: list[CardSpec]) -> set[str]:
    """The ``config_json`` top-level keys owned by the ai/model cards (plus the
    runtime sentinels) - everything else belongs to the config card."""
    reserved = {MODEL_LOCK_SENTINEL, AI_LOCK_SENTINEL}
    for c in cards:
        if c.card_id == CARD_AI:
            reserved.add(AI_KEY)
        elif c.card_id == CARD_MODEL and c.config_key:
            reserved.add(c.config_key)
    return reserved


def card_owned_keys(card: CardSpec, cards: list[CardSpec], cfg: dict[str, Any]) -> set[str]:
    """The top-level keys of *cfg* that *card* owns (its stripping/merge slice)."""
    if card.card_id == CARD_AI:
        return {AI_KEY} & set(cfg)
    if card.card_id == CARD_MODEL:
        return {card.config_key or CARD_MODEL} & set(cfg)
    # config card: everything not claimed by the ai/model cards nor the sentinel.
    reserved = _reserved_top_level_keys(cards)
    return {k for k in cfg if k not in reserved}


async def resolve_card_overlays(
    session: AsyncSession,
    manifest: Manifest,
    base_cfg: dict[str, Any],
    user_id: str,
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Apply each admin-locked card's shared value onto the per-user *base_cfg*.

    Returns ``(effective_config, {card_id: locked})`` for every card the module
    exposes. The per-user base is never replaced wholesale - a locked card
    overlays only the slice it owns, so an unlocked card's per-user value always
    survives (this is what makes locking the config card *not* drop the user's
    AI/model selection). Each card resolves defensively: one failing lookup
    degrades to the per-user value instead of wiping the whole config.

    The ``__model_admin_locked__`` sentinel is intentionally **not** added here -
    it is a loader-runtime concern the caller injects, so the effective config
    returned to the settings API stays free of the marker.
    """
    cfg = dict(base_cfg)
    controlled: dict[str, bool] = {}
    for card in derive_cards(manifest):
        try:
            admin_val, locked = await resolve(
                session, SCOPE_CARD, card_key(manifest.id, card.card_id), user_id
            )
        except Exception:
            admin_val, locked = None, False
        controlled[card.card_id] = bool(locked)
        if locked and isinstance(admin_val, dict):
            cfg.update(admin_val)
    return cfg, controlled
