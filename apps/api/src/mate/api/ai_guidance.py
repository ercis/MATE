"""Shared AI-guidance generator + schema.

Wraps the three configured providers (anthropic, openai, unigpt) behind a
single ``generate_guidance()`` call that returns a structured object matching
``GUIDANCE_SCHEMA``. Used by ``routes/ai_guidance.py`` for module-level,
process-level, and import-time guidance.

Kept out of ``routes/`` to avoid the same import cycle that ``ai_config``
sidesteps (importing from ``routes/`` triggers every router mount).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import structlog

from mate.api.ai_config import AiConfigPayload

log = structlog.get_logger(__name__)


# ── Structured schema ───────────────────────────────────────────────────────

GUIDANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["interpretation", "recommended_actions", "anomaly_flags"],
    "properties": {
        "interpretation": {
            "type": "string",
            "maxLength": 4000,
            "description": "Plain-language explanation of what the data shows for this process.",
        },
        "recommended_actions": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 280},
            "description": "Concrete next steps the user could take.",
        },
        "anomaly_flags": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "message"],
                "properties": {
                    "severity": {"enum": ["info", "warning", "critical"]},
                    "message": {"type": "string", "maxLength": 400},
                },
            },
            "description": "Data-quality or analytic warnings worth surfacing.",
        },
    },
}


_EMPTY_GUIDANCE: dict[str, Any] = {
    "interpretation": "",
    "recommended_actions": [],
    "anomaly_flags": [],
}


class GuidanceError(RuntimeError):
    """Raised when the provider returns something we can't coerce into the schema."""


# ── Public entry point ─────────────────────────────────────────────────────


async def generate_guidance(
    cfg: AiConfigPayload,
    *,
    system_prompt: str,
    payload: Any,
    user_prefix: str = "Analyse this data:",
) -> dict[str, Any]:
    """Return a structured guidance dict matching ``GUIDANCE_SCHEMA``."""
    return _coerce(
        await structured_completion(
            cfg,
            system_prompt=system_prompt,
            payload=payload,
            schema=GUIDANCE_SCHEMA,
            tool_name="emit_guidance",
            user_prefix=user_prefix,
        )
    )


async def structured_completion(
    cfg: AiConfigPayload,
    *,
    system_prompt: str,
    payload: Any,
    schema: dict[str, Any],
    tool_name: str = "emit",
    user_prefix: str = "Analyse this data:",
) -> dict[str, Any]:
    """Lower-level primitive: get a single JSON object matching ``schema``.

    Used by ``generate_guidance`` (with the canonical guidance schema) and by
    one-off endpoints like the import column-mapping suggestion route.
    Raises ``GuidanceError`` if the configured provider returns something we
    cannot parse (after one retry for OpenAI-compatible backends that fall
    back to prompted-JSON).
    """
    provider = cfg.selected_provider
    if not provider or not cfg.selected_model:
        raise GuidanceError("No AI model selected. Configure one in Settings → AI.")
    p = getattr(cfg, provider)
    if not p.api_key:
        raise GuidanceError(f"No API key configured for {provider!r}.")

    user_text = _format_user_message(user_prefix, payload)
    full_system = _combine_system(cfg.system_prompt, system_prompt)

    if provider == "anthropic":
        return await _anthropic_tool_use(cfg, full_system, user_text, schema, tool_name)

    base_url = "https://api.openai.com/v1" if provider == "openai" else (p.base_url or "")
    if not base_url:
        raise GuidanceError(f"{provider!r} requires a base URL.")
    # UniGPT / LibreChat-style proxies and most self-hosted OpenAI-compatible
    # backends don't accept `response_format: json_schema`. Skip the structured
    # attempt for them; only real OpenAI handles json_schema natively.
    if provider in ("unigpt", "custom"):
        timeout = httpx.Timeout(120.0, connect=10.0)
        url = f"{base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await _openai_compat_prompted(
                client, cfg, full_system, user_text, url, p.api_key, schema
            )
    return await _openai_compat_structured(
        cfg, full_system, user_text, base_url, p.api_key, schema, tool_name
    )


async def stream_interpretation(
    cfg: AiConfigPayload,
    *,
    system_prompt: str,
    payload: Any,
    user_prefix: str = "Analyse this data:",
) -> AsyncGenerator[str, None]:
    """Stream a plain-text interpretation only (no structured tail).

    Designed for the panel's "streaming" UX: stream the long-form text as
    soon as the model produces it, then the caller makes a second
    non-streaming call to ``generate_guidance`` for the structured fields.
    The system prompt is augmented so the model writes prose only.
    """
    provider = cfg.selected_provider
    if not provider or not cfg.selected_model:
        raise GuidanceError("No AI model selected. Configure one in Settings → AI.")
    p = getattr(cfg, provider)
    if not p.api_key:
        raise GuidanceError(f"No API key configured for {provider!r}.")

    user_text = _format_user_message(user_prefix, payload)
    full_system = (
        _combine_system(cfg.system_prompt, system_prompt)
        + "\n\nWrite a concise plain-text interpretation (no JSON, no markdown headings)."
    )

    if provider == "anthropic":
        async for chunk in _anthropic_stream_text(cfg, full_system, user_text):
            yield chunk
        return

    base_url = "https://api.openai.com/v1" if provider == "openai" else (p.base_url or "")
    async for chunk in _openai_compat_stream_text(cfg, full_system, user_text, base_url, p.api_key):
        yield chunk


# ── Anthropic - tool-use for structured output ─────────────────────────────


async def _anthropic_tool_use(
    cfg: AiConfigPayload,
    system: str,
    user_text: str,
    schema: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    p = cfg.anthropic
    body: dict[str, Any] = {
        "model": cfg.selected_model,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
        "tools": [
            {
                "name": tool_name,
                "description": "Emit a structured result for the user's request.",
                "input_schema": schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": p.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
    if not r.is_success:
        raise GuidanceError(f"Anthropic {r.status_code}: {r.text[:300]}")
    data = r.json()
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            result = block.get("input")
            if isinstance(result, dict):
                return result
            raise GuidanceError("Anthropic tool_use input was not an object.")
    raise GuidanceError("Anthropic response did not contain a tool_use block.")


async def _anthropic_stream_text(
    cfg: AiConfigPayload, system: str, user_text: str
) -> AsyncGenerator[str, None]:
    p = cfg.anthropic
    body: dict[str, Any] = {
        "model": cfg.selected_model,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user_text}],
        "stream": True,
    }
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
            raise GuidanceError(f"Anthropic {r.status_code}: {raw.decode()[:300]}")
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
                    yield delta["text"]


# ── OpenAI-compatible - json_schema response format, with prompted fallback ─


async def _openai_compat_structured(
    cfg: AiConfigPayload,
    system: str,
    user_text: str,
    base_url: str,
    api_key: str,
    schema: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    body_strict = {
        "model": cfg.selected_model,
        "messages": msgs,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": tool_name,
                "schema": schema,
                "strict": True,
            },
        },
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json=body_strict,
        )
        # Strict json_schema isn't universally supported. Many proxies
        # (litellm-fronted LibreChat, Azure-style deployments, etc.) return
        # 400/422/500 with a payload that mentions `response_format`. Auth
        # and rate-limit errors stay terminal - no point burning tokens to
        # retry them.
        if r.status_code in (401, 403, 429):
            raise GuidanceError(f"{r.status_code}: {r.text[:300]}")
        if not r.is_success:
            log.info(
                "ai_guidance.openai_compat.fallback_to_prompted_json",
                url=url,
                status=r.status_code,
            )
            return await _openai_compat_prompted(
                client, cfg, system, user_text, url, api_key, schema
            )
        data = r.json()
        content = _openai_content(data)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            log.info(
                "ai_guidance.openai_compat.fallback_after_non_json",
                url=url,
            )
            return await _openai_compat_prompted(
                client, cfg, system, user_text, url, api_key, schema
            )


async def _openai_compat_prompted(
    client: httpx.AsyncClient,
    cfg: AiConfigPayload,
    system: str,
    user_text: str,
    url: str,
    api_key: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Prompted-JSON fallback for backends that reject `response_format`.

    One retry: if the first response can't be parsed, append the parse error
    to the conversation and ask again.
    """
    schema_hint = (
        "Reply with ONLY a single JSON object matching this schema (no prose, "
        "no markdown fences):\n" + json.dumps(schema, indent=2)
    )
    msgs = [
        {"role": "system", "content": system + "\n\n" + schema_hint},
        {"role": "user", "content": user_text},
    ]
    for attempt in range(2):
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={"model": cfg.selected_model, "messages": msgs},
        )
        if not r.is_success:
            raise GuidanceError(f"{r.status_code}: {r.text[:300]}")
        content = _openai_content(r.json())
        parsed = _extract_json(content)
        if isinstance(parsed, dict):
            return parsed
        if attempt == 0:
            msgs.append({"role": "assistant", "content": content})
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        "Your last reply was not valid JSON. Reply with ONLY a "
                        "JSON object matching the schema - no prose, no fences."
                    ),
                }
            )
    raise GuidanceError("Provider could not produce valid JSON after one retry.")


async def _openai_compat_stream_text(
    cfg: AiConfigPayload,
    system: str,
    user_text: str,
    base_url: str,
    api_key: str,
) -> AsyncGenerator[str, None]:
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    url = f"{base_url.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(120.0, connect=10.0)
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
            raise GuidanceError(f"{r.status_code}: {raw.decode()[:300]}")
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
                    yield content


# ── Helpers ─────────────────────────────────────────────────────────────────


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> Any | None:
    """Tolerant JSON extractor: strips ```json fences before parsing."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _FENCED_JSON.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    # Last-ditch: take the substring from the first "{" to the matching "}".
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            return None
    return None


def _openai_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "")


def _coerce(obj: Any) -> dict[str, Any]:
    """Make a best-effort guarantee that the returned dict matches the schema."""
    if not isinstance(obj, dict):
        raise GuidanceError("Provider returned a non-object response.")
    out = dict(_EMPTY_GUIDANCE)
    if isinstance(obj.get("interpretation"), str):
        out["interpretation"] = obj["interpretation"][:4000]
    actions = obj.get("recommended_actions")
    if isinstance(actions, list):
        out["recommended_actions"] = [
            str(a)[:280] for a in actions if isinstance(a, (str, int, float))
        ][:6]
    flags = obj.get("anomaly_flags")
    if isinstance(flags, list):
        clean: list[dict[str, str]] = []
        for f in flags[:8]:
            if not isinstance(f, dict):
                continue
            sev = str(f.get("severity", "info")).lower()
            if sev not in ("info", "warning", "critical"):
                sev = "info"
            msg = str(f.get("message", ""))[:400]
            if msg:
                clean.append({"severity": sev, "message": msg})
        out["anomaly_flags"] = clean
    return out


def _combine_system(user_system: str, module_system: str) -> str:
    parts = [s for s in (module_system.strip(), user_system.strip()) if s]
    return "\n\n".join(parts) if parts else ""


def _format_user_message(prefix: str, payload: Any) -> str:
    try:
        body = json.dumps(payload, indent=2, default=str)
    except (TypeError, ValueError):
        body = str(payload)
    return f"{prefix}\n\n```json\n{body}\n```"
