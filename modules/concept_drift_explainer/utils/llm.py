"""Module-AI helper - fully isolated from the platform's global AI config.

Unlike most modules, the Concept Drift Explainer does **not** read the
platform's Settings → AI keys. It owns its own OpenAI credentials, persisted
under ``cfg["ai"]``::

    cfg["ai"] = {
        "api_key": "sk-...",
        "llm_model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,   # optional; null ⇒ model native
    }

Only OpenAI is supported - the settings card offers a key + model picker with a
"Check" button (see ``module.py::ai_check``). The clients are built directly
here with no platform-database dependency.

Falls back to ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` env vars when the module
config has no ``ai.api_key`` - covers the pytest suite, which doesn't mount the
full platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass(frozen=True)
class ModuleAiClients:
    chat: BaseChatModel
    embeddings: Embeddings


def _missing_key() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=(
            "No OpenAI API key set for the Concept Drift Explainer. Open "
            "Settings → Modules → Concept Drift Explainer, paste your OpenAI "
            "key, click Check, pick the models, and Save."
        ),
    )


def _coerce_dimensions(raw: object) -> int | None:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = int(raw)
        except ValueError:
            return None
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


async def load_module_ai_clients(cfg: dict) -> ModuleAiClients:
    """Build chat + embedding clients from this module's own OpenAI config."""
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    ai = (cfg or {}).get("ai") or {}
    api_key = (ai.get("api_key") or "").strip() or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise _missing_key()

    base_url = os.getenv("OPENAI_BASE_URL")
    llm_model = (ai.get("llm_model") or "").strip() or DEFAULT_LLM_MODEL
    embedding_model = (ai.get("embedding_model") or "").strip() or DEFAULT_EMBEDDING_MODEL
    emb_dim = _coerce_dimensions(ai.get("embedding_dimensions"))

    chat_kwargs: dict = {"model": llm_model, "temperature": 0, "api_key": api_key}
    emb_kwargs: dict = {"model": embedding_model, "api_key": api_key}
    if base_url:
        chat_kwargs["base_url"] = base_url
        emb_kwargs["base_url"] = base_url
    if emb_dim is not None:
        emb_kwargs["dimensions"] = emb_dim

    return ModuleAiClients(
        chat=ChatOpenAI(**chat_kwargs),
        embeddings=OpenAIEmbeddings(**emb_kwargs),
    )
