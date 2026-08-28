from __future__ import annotations

from typing import Any

from mate.sdk import Module, ModuleContext, route


class SampleCardsModule(Module):
    """Fixture with all three settings cards (config_schema + ai_models +
    model_store) so tests can exercise per-card admin control end-to-end."""

    id = "sample_cards"

    @route.get("/echo-config")
    async def echo_config(self, ctx: ModuleContext) -> dict[str, Any]:
        """The effective module config the loader assembled - proves per-card
        admin overlays reach ``ModuleContext.config``."""
        return {"config": ctx.config.value}

    @route.get("/models")
    async def list_models(self, ctx: ModuleContext) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        return {
            "locked": bool(cfg.get("__model_admin_locked__")),
            "selected": cfg.get("model"),
        }
