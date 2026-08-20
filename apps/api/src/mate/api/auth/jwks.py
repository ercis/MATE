"""Async JWKS fetcher with in-process cache.

Keycloak's JWKS endpoint returns the signing keys for the realm. Tokens carry
a `kid` header that points at one of those keys. We cache the keys by `kid`
and refresh the whole bundle on:

- TTL expiry (default 1 hour),
- a cache miss on a `kid` we haven't seen (covers key rotation).

A single ``asyncio.Lock`` serialises concurrent fetches so the API doesn't
thunder Keycloak on a cold cache or after a backend restart.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from mate.api.config import get_settings


@dataclass
class _Cache:
    keys: dict[str, Any] = field(default_factory=dict)  # kid -> public key
    fetched_at: float = 0.0
    last_failure_at: float = 0.0


_cache = _Cache()
_lock = asyncio.Lock()
_FAILURE_BACKOFF_SECONDS = 5.0


async def _fetch_jwks() -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.keycloak_jwks_url)
        resp.raise_for_status()
        return resp.json()


def _load_keys(jwks: dict[str, Any]) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    for entry in jwks.get("keys", []):
        kid = entry.get("kid")
        if not kid:
            continue
        try:
            keys[kid] = RSAAlgorithm.from_jwk(entry)
        except (ValueError, jwt.InvalidKeyError):
            continue
    return keys


async def _refresh_unlocked() -> None:
    jwks = await _fetch_jwks()
    keys = _load_keys(jwks)
    if not keys:
        raise RuntimeError("Keycloak JWKS endpoint returned no usable keys")
    _cache.keys = keys
    _cache.fetched_at = time.monotonic()
    _cache.last_failure_at = 0.0


async def get_signing_key(kid: str) -> Any:
    """Return the RSA public key for `kid`, refreshing on miss or expiry."""
    settings = get_settings()
    now = time.monotonic()

    # Fast path: known kid + cache fresh.
    if kid in _cache.keys and (now - _cache.fetched_at) < settings.keycloak_jwks_ttl_seconds:
        return _cache.keys[kid]

    async with _lock:
        # Re-check after grabbing the lock (another caller may have refreshed).
        now = time.monotonic()
        if kid in _cache.keys and (now - _cache.fetched_at) < settings.keycloak_jwks_ttl_seconds:
            return _cache.keys[kid]

        # Bounded back-off so a Keycloak outage doesn't drive a retry storm.
        if _cache.last_failure_at and (now - _cache.last_failure_at) < _FAILURE_BACKOFF_SECONDS:
            raise RuntimeError("JWKS fetch recently failed; backing off")

        try:
            await _refresh_unlocked()
        except Exception:
            _cache.last_failure_at = time.monotonic()
            raise

    if kid not in _cache.keys:
        raise jwt.InvalidKeyError(f"No JWKS entry for kid={kid!r}")
    return _cache.keys[kid]


def reset_cache_for_tests() -> None:  # pragma: no cover - test helper
    _cache.keys = {}
    _cache.fetched_at = 0.0
    _cache.last_failure_at = 0.0
