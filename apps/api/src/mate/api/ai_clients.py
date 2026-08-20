"""Provider-agnostic LangChain client factories.

Modules that need a chat LLM or an embedding model call into here so the
provider/model/key plumbing lives in one place. Keys are pulled from the
global ``ai.config`` settings via [ai_config.py](ai_config.py) - modules only
persist the ``(provider, model)`` pair they want.

Supported providers (matching the global picker in
[apps/web/app/(platform)/settings/ai/page.tsx](../../../../apps/web/app/(platform)/settings/ai/page.tsx)):

* ``anthropic`` - Claude via ``langchain-anthropic``. No embeddings endpoint.
* ``openai``   - GPT/o-series via ``langchain-openai``.
* ``unigpt``   - OpenAI-compatible endpoint (LibreChat / university deploys).
* ``custom``   - Any other OpenAI-compatible endpoint at ``p.base_url``.

LangChain itself is *not* a platform dependency - each module ships its own
venv with the langchain packages it needs. To keep this module importable in
the bare platform process, the langchain imports are deferred to call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.ai_config import Provider, _provider_creds

if TYPE_CHECKING:  # pragma: no cover - only for type checkers
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models import BaseChatModel


async def build_chat_model(
    session: AsyncSession,
    provider: Provider,
    model: str,
    user_id: str,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float = 90.0,
) -> BaseChatModel:
    """Return a LangChain chat model for ``(provider, model)``.

    Raises 422 if no API key is stored for ``provider``.
    """
    api_key, base_url = await _provider_creds(session, provider, user_id)

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "api_key": api_key,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs["default_request_timeout"] = timeout
        return ChatAnthropic(**kwargs)

    if provider in ("unigpt", "custom") and not base_url:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{provider!r} requires a base URL in Settings → AI before it "
                "can be used as a module model provider."
            ),
        )

    from langchain_openai import ChatOpenAI

    if base_url:
        kwargs["base_url"] = base_url
    kwargs["timeout"] = timeout
    return ChatOpenAI(**kwargs)


async def build_embeddings(
    session: AsyncSession,
    provider: Provider,
    model: str,
    user_id: str,
    *,
    dimensions: int | None = None,
) -> Embeddings:
    """Return a LangChain embeddings client for ``(provider, model)``.

    Anthropic has no native embeddings endpoint - picking it here is a config
    mistake the user should fix in the module's AI-models picker.

    ``dimensions`` controls Matryoshka-style server-side truncation. Only
    OpenAI's ``text-embedding-3``-class models support it, and so only the
    official ``openai`` provider forwards it to the API. For ``unigpt`` /
    ``custom`` providers the value is recorded by the caller for local use
    (e.g. sizing the Pinecone index) but is **not** sent on the HTTP request,
    since most OpenAI-compatible proxies reject unknown parameters with a 400.
    """
    if provider == "anthropic":
        raise HTTPException(
            status_code=422,
            detail=(
                "Anthropic does not provide an embeddings endpoint - pick "
                "OpenAI / UniGPT / Custom for the embedding model."
            ),
        )

    api_key, base_url = await _provider_creds(session, provider, user_id)

    if provider in ("unigpt", "custom") and not base_url:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{provider!r} requires a base URL in Settings → AI before it "
                "can be used as a module embedding provider."
            ),
        )

    from langchain_openai import OpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if dimensions is not None and provider == "openai":
        kwargs["dimensions"] = dimensions
    return OpenAIEmbeddings(**kwargs)
