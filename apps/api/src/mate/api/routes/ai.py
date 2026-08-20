"""/api/v1/ai - AI chat configuration and provider proxies.

The API keys and system prompt live as a single JSON blob in
``user_settings`` under the ``ai.config`` key. The provider model and
pricing endpoints proxy through the backend so that:

* keys never leave the SQLite database,
* we sidestep browser CORS restrictions on provider ``/v1/models`` endpoints, and
* the public litellm pricing catalog can be cached server-side with a TTL.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mate.api.ai_config import (
    AI_CONFIG_KEY,
    AiConfigOut,
    AiConfigPayload,
    Provider,
    ProviderConfig,
    _load_config,
    _provider_creds,
    ai_control_state,
    load_ai_config,
    mask_config,
    merge_ai_payload,
)
from mate.api.ai_models import FetchModelsResponse, ModelInfo, fetch_provider_models
from mate.api.ai_nav import (
    RoutingResult,
    build_user_destinations,
    list_user_processes,
    route_intent,
)
from mate.api.auth import CurrentUserDep
from mate.api.db.models import UserSetting
from mate.api.db.session import SessionDep

# Re-exported so any caller that previously imported these names from
# `routes.ai` still works. The real definitions live in `_ai_config`.
__all__ = [
    "AI_CONFIG_KEY",
    "AiConfigOut",
    "AiConfigPayload",
    "FetchModelsResponse",
    "ModelInfo",
    "Provider",
    "ProviderConfig",
    "_load_config",
    "_provider_creds",
    "load_ai_config",
    "router",
]

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
_PRICING_TTL_SECONDS = 3600


@router.get("/config", response_model=AiConfigOut)
async def get_config(session: SessionDep, user: CurrentUserDep) -> AiConfigOut:
    controlled = await ai_control_state(session, user.id)
    cfg = await load_ai_config(session, user.id)
    return mask_config(cfg, controlled_by_admin=controlled)


@router.put("/config", response_model=AiConfigOut)
async def put_config(
    payload: AiConfigPayload,
    session: SessionDep,
    user: CurrentUserDep,
) -> AiConfigOut:
    if await ai_control_state(session, user.id):
        raise HTTPException(
            status_code=403,
            detail="AI settings are controlled by your administrator.",
        )
    row = await session.get(UserSetting, (user.id, AI_CONFIG_KEY))
    existing = _load_config(row)
    # Masked GET means the browser sends blank keys to mean "keep the stored
    # one"; merge so a save never wipes a key the user can't see.
    merged = merge_ai_payload(payload, existing)
    data = merged.model_dump()
    if row is None:
        session.add(UserSetting(user_id=user.id, key=AI_CONFIG_KEY, value_json=data))
    else:
        row.value_json = data
    await session.commit()
    return mask_config(merged, controlled_by_admin=False)


# --------------------------------------------------------------------------
# Provider model listing - proxied so keys stay server-side and CORS is moot
# (logic lives in ``ai_models`` so the admin AI route can reuse it).
# --------------------------------------------------------------------------


@router.post("/models/{provider}", response_model=FetchModelsResponse)
async def fetch_models(
    provider: Annotated[Provider, Path()],
    session: SessionDep,
    user: CurrentUserDep,
) -> FetchModelsResponse:
    api_key, base_url = await _provider_creds(session, provider, user.id)
    return await fetch_provider_models(provider, api_key, base_url)


# --------------------------------------------------------------------------
# Pricing catalog (litellm)
# --------------------------------------------------------------------------


_pricing_cache: dict[str, Any] | None = None
_pricing_cache_at: float = 0.0
_pricing_lock = asyncio.Lock()


@router.get("/pricing")
async def get_pricing(user: CurrentUserDep) -> dict[str, Any]:
    """Return the litellm price catalog keyed by model id.

    Cached in-process for one hour. The shape is whatever litellm publishes -
    the frontend reads ``input_cost_per_token`` / ``output_cost_per_token`` /
    ``max_tokens`` / ``litellm_provider`` per entry.
    """

    global _pricing_cache, _pricing_cache_at
    now = time.time()
    if _pricing_cache is not None and now - _pricing_cache_at < _PRICING_TTL_SECONDS:
        return _pricing_cache
    async with _pricing_lock:
        if _pricing_cache is not None and now - _pricing_cache_at < _PRICING_TTL_SECONDS:
            return _pricing_cache
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(LITELLM_PRICING_URL)
                r.raise_for_status()
                fresh: dict[str, Any] = r.json()
                _pricing_cache = fresh
                _pricing_cache_at = now
                return fresh
        except httpx.HTTPError as exc:
            log.warning("ai.pricing.fetch_failed", error=str(exc))
            # If we have *any* prior payload, fall back to it rather than 502.
            if _pricing_cache is not None:
                return _pricing_cache
            raise HTTPException(
                status_code=502,
                detail=f"Could not fetch pricing catalog: {exc}",
            ) from exc


# --------------------------------------------------------------------------
# Chat - streaming endpoint
# --------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatContext(BaseModel):
    """Optional process-aware context for the MATE sidebar.

    When set, the server fetches cached outputs for the given module ids on
    the given log and prepends them to the system prompt so the chat can
    answer questions like "what's the worst bottleneck?" with grounded data.
    """

    log_id: str | None = None
    module_ids: list[str] = []
    # The route the user is currently viewing (e.g. "/dashboards",
    # "/processes/<id>/modules/performance"). Lets the assistant answer
    # location-aware ("you're already here") and lets the router drop a shortcut
    # to the page the user is already on.
    current_path: str | None = None


class NavHint(BaseModel):
    """Sent by the frontend when /route already produced a chip for this turn, so
    the chat answer stays concise and doesn't re-explain navigation."""

    label: str
    intent: Literal["navigate", "both"]


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: ChatContext | None = None
    nav_hint: NavHint | None = None


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


_CONTEXT_CHAR_BUDGET = 12_000

# Always prepended to the chat system prompt so the model behaves as MATE's
# in-app assistant. The platform separately renders clickable navigation
# shortcuts beneath replies, so the model must NOT disclaim ("I can't open the
# settings") - but it also must NOT mention those shortcuts in its text (it would
# echo "Jump to" even when no shortcut is shown). The reply must read naturally
# on its own; the UI handles navigation.
BASE_CHAT_SYSTEM_PROMPT = (
    "You are MATE AI, the built-in assistant for MATE, a local process-mining "
    "platform. You are inside the app and the user can reach any module, dashboard, "
    "process, or settings page from here.\n\n"
    "Guidelines:\n"
    "- Do NOT state or guess which page the user is currently on, and NEVER tell them "
    "they are 'already' on or at a page/module - you cannot reliably know their current "
    "location. Just help with what they asked.\n"
    "- Never claim you are unable to navigate, open settings/modules, or act inside "
    "the app, and never ask which app or platform the user means - you are already "
    "inside MATE.\n"
    "- Answer concisely and helpfully. When the user wants to go somewhere they are "
    "not, briefly describe the destination and give one useful tip about what they can "
    "do there; do not write step-by-step 'how to find it' instructions.\n"
    "- Do NOT mention navigation buttons, shortcuts, links, or 'Jump to', and do not "
    "tell the user to click anything below. The interface adds clickable shortcuts "
    "automatically - your text must read naturally on its own and never reference them.\n"
    "- For analytical questions, answer concisely and use any process data or "
    "context provided below. If a 'Your processes' list is given, use it to answer "
    "questions like how many variants/cases/events a named process has. If it is not "
    "given, you do not have access to process data - say so briefly instead of guessing."
)


def _process_summary_block(processes: list[Any]) -> str:
    """A compact, sensitive process list for the chat prompt (opt-in only)."""
    if not processes:
        return ""
    lines = ["Your processes (event logs) and key stats:"]
    for p in processes:
        stats: list[str] = []
        if p.cases_count is not None:
            stats.append(f"{p.cases_count} cases")
        if p.variants_count is not None:
            stats.append(f"{p.variants_count} variants")
        if p.events_count is not None:
            stats.append(f"{p.events_count} events")
        if p.object_types_count is not None:
            stats.append(f"{p.object_types_count} object types")
        if p.date_min and p.date_max:
            stats.append(f"{p.date_min[:10]} → {p.date_max[:10]}")
        suffix = f" - {', '.join(stats)}" if stats else ""
        lines.append(f'- "{p.name}" (id {p.id}, {p.log_model}){suffix}')
    return "\n".join(lines)


async def _build_context_block(context: ChatContext | None, user_id: str) -> str:
    if context is None or not context.log_id:
        return ""
    # Local imports keep `routes/ai.py` free of module-loader symbols at import
    # time (which would otherwise tie this module to the loader's lifecycle).
    from mate.api.modules import get_module_loader
    from mate.api.modules.cache import ResultCache

    try:
        loader = get_module_loader()
    except HTTPException:
        return ""

    # An empty module_ids list means "every module the loader knows about that
    # exposes guidance_payload" - the natural default when the frontend just
    # detected it's on a process page.
    module_ids = context.module_ids or [
        mid
        for mid, loaded in loader.loaded.items()
        if callable(getattr(loaded.instance, "guidance_payload", None))
    ]
    if not module_ids:
        return ""

    parts: list[str] = ["Current process context (cached module outputs):"]
    remaining = _CONTEXT_CHAR_BUDGET
    for mid in module_ids:
        loaded = loader.loaded.get(mid)
        if loaded is None:
            continue
        fn = getattr(loaded.instance, "guidance_payload", None)
        if not callable(fn):
            continue
        # Prefer the module's curated payload over scanning raw cache keys.
        try:
            ctx = await loader._make_context(mid, context.log_id, user_id)
        except Exception:
            continue
        try:
            data = (
                await fn(ctx)
                if asyncio.iscoroutinefunction(fn)
                else await asyncio.to_thread(fn, ctx)
            )
        except Exception:
            data = None
        finally:
            import shutil as _shutil

            _shutil.rmtree(ctx.workdir, ignore_errors=True)
        if data is None:
            # Fall back to dumping every JSON cache entry the module wrote.
            cache = ResultCache(context.log_id, mid, user_id)
            try:
                files = list(cache.dir.glob("*.json"))
            except OSError:
                files = []
            data = {}
            for path in files:
                if path.name.startswith("__"):
                    continue
                try:
                    data[path.stem] = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
            if not data:
                continue
        body = json.dumps(data, default=str)[:remaining]
        parts.append(f"### Module: {mid}\n```json\n{body}\n```")
        remaining -= len(body)
        if remaining <= 0:
            break
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)


class RouteRequest(BaseModel):
    """Last user message + current page context for navigation routing."""

    message: str
    context: ChatContext | None = None


@router.post("/route")
async def route(payload: RouteRequest, session: SessionDep, user: CurrentUserDep) -> RoutingResult:
    """Classify a chat message and return navigation suggestions (if any).

    Runs in parallel with ``/chat`` from the frontend: the chat answer streams
    while this resolves, and the suggestions are rendered additively. On any
    failure (no AI configured, provider error) we return an empty, no-op result
    so navigation never blocks or breaks the chat.
    """
    cfg = await load_ai_config(session, user.id)

    # If the user hasn't configured a usable provider, the LLM leg can't run -
    # but the deterministic pre-filter still can, so we proceed regardless and
    # let route_intent degrade gracefully when it needs the model.
    log_id = payload.context.log_id if payload.context else None
    current_path = payload.context.current_path if payload.context else None
    destinations = await build_user_destinations(session, user.id)
    # Navigation to a named process is always allowed - the classifier only sees
    # process names+ids (build_process_catalog strips stats). The sensitive
    # analytical data (variant/case counts) is gated separately, in /chat.
    processes = await list_user_processes(session, user.id)
    return await route_intent(
        cfg,
        message=payload.message,
        destinations=destinations,
        log_id=log_id,
        current_path=current_path,
        processes=processes,
    )


@router.post("/chat")
async def chat(
    payload: ChatRequest, session: SessionDep, user: CurrentUserDep
) -> StreamingResponse:
    cfg = await load_ai_config(session, user.id)

    if not cfg.selected_provider or not cfg.selected_model:
        raise HTTPException(
            status_code=400,
            detail="No AI model selected. Configure one in Settings → AI.",
        )
    p = getattr(cfg, cfg.selected_provider)
    if not p.api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for {cfg.selected_provider!r}. Go to Settings → AI.",
        )
    if cfg.selected_provider in ("unigpt", "custom") and not p.base_url:
        raise HTTPException(
            status_code=400,
            detail=f"{cfg.selected_provider!r} requires a base URL. Go to Settings → AI.",
        )

    # Assemble the system prompt for this call only (never persisted): the base
    # platform prompt, the user's current location, any cached process context,
    # then the user's own saved system prompt.
    parts = [BASE_CHAT_SYSTEM_PROMPT]

    # The frontend only calls /chat for "both"/"chat" turns (pure navigation is
    # answered client-side without an LLM). When a chip was produced, keep the
    # reply tight and don't re-explain the navigation the chip already handles.
    if payload.nav_hint is not None:
        parts.append(
            f'A clickable shortcut to "{payload.nav_hint.label}" is already shown to the '
            "user. Do not describe how to navigate and do not mention the shortcut; answer "
            "the substance of the question in 1-2 short sentences."
        )
    # Sensitive: only share process data with the provider when the user opted in.
    if cfg.allow_process_data:
        processes = await list_user_processes(session, user.id)
        summary = _process_summary_block(processes)
        if summary:
            parts.append(summary)

    context_block = await _build_context_block(payload.context, user.id)
    if context_block:
        parts.append(context_block)
    if cfg.system_prompt:
        parts.append(cfg.system_prompt)
    cfg = cfg.model_copy(update={"system_prompt": "\n\n".join(parts)})

    return StreamingResponse(
        _stream_chat(cfg, payload.messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_chat(
    cfg: AiConfigPayload, messages: list[ChatMessage]
) -> AsyncGenerator[str, None]:
    provider = cfg.selected_provider
    p = getattr(cfg, provider)
    try:
        if provider == "anthropic":
            async for chunk in _stream_anthropic(cfg, messages):
                yield chunk
        else:
            base_url = "https://api.openai.com/v1" if provider == "openai" else (p.base_url or "")
            async for chunk in _stream_openai_compat(cfg, messages, base_url, p.api_key):
                yield chunk
    except httpx.HTTPError as exc:
        log.warning("ai.chat.stream_failed", provider=provider, error=str(exc))
        yield _sse({"error": str(exc)})


async def _stream_anthropic(
    cfg: AiConfigPayload, messages: list[ChatMessage]
) -> AsyncGenerator[str, None]:
    p = cfg.anthropic
    body: dict[str, Any] = {
        "model": cfg.selected_model,
        "max_tokens": 8096,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "stream": True,
    }
    if cfg.system_prompt:
        body["system"] = cfg.system_prompt

    timeout = httpx.Timeout(120.0, connect=10.0)
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": p.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        ) as r,
    ):
        if not r.is_success:
            raw = await r.aread()
            yield _sse({"error": f"Anthropic {r.status_code}: {raw.decode()[:300]}"})
            return
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "content_block_delta":
                delta = evt.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield _sse({"delta": delta["text"]})
    yield _sse({"done": True})


async def _stream_openai_compat(
    cfg: AiConfigPayload,
    messages: list[ChatMessage],
    base_url: str,
    api_key: str,
) -> AsyncGenerator[str, None]:
    msgs: list[dict[str, str]] = []
    if cfg.system_prompt:
        msgs.append({"role": "system", "content": cfg.system_prompt})
    msgs.extend({"role": m.role, "content": m.content} for m in messages)

    timeout = httpx.Timeout(120.0, connect=10.0)
    url = f"{base_url.rstrip('/')}/chat/completions"
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={"model": cfg.selected_model, "messages": msgs, "stream": True},
        ) as r,
    ):
        if not r.is_success:
            raw = await r.aread()
            yield _sse({"error": f"{r.status_code}: {raw.decode()[:300]}"})
            return
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw_str = line[5:].strip()
            if raw_str == "[DONE]":
                break
            try:
                evt = json.loads(raw_str)
            except json.JSONDecodeError:
                continue
            choices = evt.get("choices", [])
            if choices:
                content = choices[0].get("delta", {}).get("content") or ""
                if content:
                    yield _sse({"delta": content})
    yield _sse({"done": True})
