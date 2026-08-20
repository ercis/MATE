"""Provider model-listing proxy - shared by per-user (``routes/ai``) and admin
(``routes/admin_policies``) AI config.

Kept outside ``routes/`` for the same reason as ``ai_config.py``: importing
anything from ``routes/`` runs ``routes/__init__.py`` (which mounts every
router), so a route module importing another route module risks a cycle. The
provider HTTP logic lives here as plain functions; both route layers resolve
their own credentials (per-user vs shared admin) and call ``fetch_provider_models``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx
import structlog
from fastapi import HTTPException
from pydantic import BaseModel

from mate.api.ai_config import Provider

log = structlog.get_logger(__name__)


class ModelInfo(BaseModel):
    id: str
    display_name: str | None = None
    created: int | None = None


class FetchModelsResponse(BaseModel):
    models: list[ModelInfo]


def _iso_to_epoch(s: Any) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        return int(_dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _openai_compat_models_url(base_url: str) -> str:
    """Construct the /models endpoint URL for an OpenAI-compatible backend.

    Follows OpenAI SDK convention: base_url is the versioned API prefix (e.g.,
    https://api.openai.com/v1 or https://gpt.uni-muenster.de/v1), and only
    the endpoint path (/models) is appended.
    """
    return f"{base_url.rstrip('/')}/models"


def _parse_json(response: httpx.Response, provider: str, url: str | None = None) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        snippet = response.text[:300]
        error_detail: dict[str, Any] = {
            "provider": provider,
            "upstream": f"Non-JSON response: {snippet!r}",
        }
        if url:
            error_detail["url"] = url
        raise HTTPException(status_code=502, detail=error_detail) from exc


def _raise_provider_error(provider: str, response: httpx.Response, url: str | None = None) -> None:
    if response.is_success:
        return
    # Forward the upstream status so the frontend can distinguish auth (401),
    # rate-limit (429), etc. We always wrap the body in a string detail.
    body: Any = None
    try:
        body = response.json()
    except ValueError:
        body = response.text
    detail = (
        body if isinstance(body, str) else (body.get("error") if isinstance(body, dict) else body)
    )
    error_detail: dict[str, Any] = {"provider": provider, "upstream": detail}
    if url:
        error_detail["url"] = url
    raise HTTPException(
        status_code=response.status_code,
        detail=error_detail,
    )


async def fetch_provider_models(
    provider: Provider, api_key: str, base_url: str | None
) -> FetchModelsResponse:
    """List models for ``provider`` using the supplied credentials.

    Proxied server-side so keys stay off the browser and provider CORS is moot.
    Anthropic/OpenAI use their fixed endpoints; UniGPT/Custom hit the supplied
    OpenAI-compatible ``base_url``.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if provider == "anthropic":
                url = "https://api.anthropic.com/v1/models"
                r = await client.get(
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                _raise_provider_error(provider, r, url=url)
                data = _parse_json(r, provider=provider, url=url)
                return FetchModelsResponse(
                    models=[
                        ModelInfo(
                            id=m["id"],
                            display_name=m.get("display_name"),
                            created=_iso_to_epoch(m.get("created_at")),
                        )
                        for m in data.get("data", [])
                        if isinstance(m, dict) and "id" in m
                    ]
                )

            if provider == "openai":
                url = "https://api.openai.com/v1/models"
                r = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                _raise_provider_error(provider, r, url=url)
                data = _parse_json(r, provider=provider, url=url)
                return FetchModelsResponse(
                    models=[
                        ModelInfo(id=m["id"], created=m.get("created"))
                        for m in data.get("data", [])
                        if isinstance(m, dict) and "id" in m
                    ]
                )

            # UniGPT / LibreChat / Custom - treat as an OpenAI-compatible
            # backend at the supplied base URL.
            if not base_url:
                raise HTTPException(
                    status_code=400,
                    detail=f"{provider!r} requires a base URL in addition to the API key.",
                )
            url = _openai_compat_models_url(base_url)
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            _raise_provider_error(provider, r, url=url)
            data = _parse_json(r, provider=provider, url=url)
            items = data.get("data", []) if isinstance(data, dict) else data
            return FetchModelsResponse(
                models=[
                    ModelInfo(id=m["id"], created=m.get("created"))
                    for m in items
                    if isinstance(m, dict) and "id" in m
                ]
            )
    except httpx.HTTPError as exc:
        log.warning("ai.models.proxy_failed", provider=provider, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Provider request failed: {exc}") from exc
