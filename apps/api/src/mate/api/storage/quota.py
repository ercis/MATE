"""Global storage-quota enforcement (S3_OFFLOAD Phase 3.2).

``STORAGE_S3_QUOTA_BYTES`` is an operator-set ceiling on total bucket usage
(under the configured prefix). This gates new-data writes (event-log imports) so
the bucket can't grow past it. Computing usage is an S3 LIST, so the total is
cached for a short TTL - a per-write LIST would be far too costly. Freeing space
(a delete) invalidates the cache so a user who deletes to make room isn't blocked
by a stale total.

No-op unless S3 is active AND a quota is set. Any S3 error resolves to "unknown"
(``None``), which never blocks a write - the quota is a guardrail, not a hard
fence that a transient list failure should slam shut.
"""

from __future__ import annotations

import threading
import time

from mate.api.storage import s3
from mate.api.storage.config import get_storage_settings

_TTL_SECONDS = 30.0
_lock = threading.Lock()
_cached: tuple[float, int] | None = None  # (expires_at_epoch, used_bytes)


def _usage_bytes_cached() -> int | None:
    """Total bucket bytes under the prefix, cached for ``_TTL_SECONDS``.

    None on an S3 error (treated as 'unknown' by callers). Runs an S3 LIST only
    on a cold/expired cache.
    """
    global _cached
    now = time.time()
    with _lock:
        if _cached is not None and _cached[0] > now:
            return _cached[1]
    try:
        used = s3.usage(get_storage_settings().prefix).used_bytes
    except s3.StorageError:
        return None
    with _lock:
        _cached = (now + _TTL_SECONDS, used)
    return used


def invalidate_usage_cache() -> None:
    """Drop the cached usage total (called after a delete frees space)."""
    global _cached
    with _lock:
        _cached = None


def over_quota_sync() -> bool:
    """True iff S3 is active, a quota is set, and usage is known to meet/exceed it.

    Conservative: an unknown usage (S3 error) returns False (don't block).
    """
    s = get_storage_settings()
    if not s.is_s3 or not s.quota_bytes:
        return False
    used = _usage_bytes_cached()
    return used is not None and used >= s.quota_bytes


def quota_status() -> tuple[int | None, int | None]:
    """``(used_bytes, quota_bytes)`` for display - either may be None."""
    s = get_storage_settings()
    if not s.is_s3 or not s.quota_bytes:
        return None, s.quota_bytes
    return _usage_bytes_cached(), s.quota_bytes
