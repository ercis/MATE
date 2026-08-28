"""Lifecycle sync between local working dirs and the S3 primary store.

Every op no-ops unless :func:`is_s3`; in local mode the platform behaves exactly
as before (byte-for-byte). When S3 is the selected backend, a log/output dir is
uploaded to the bucket after it is written (the durable primary copy) and
hydrated back on a read-miss (fresh VM / wiped local cache). S3 keys mirror the
on-disk tree relative to ``data_dir`` (under the configured prefix), so the
local cache and the bucket share one layout.

Failures are logged, not raised: a transient S3 hiccup must not break an import
or a module read (local disk still holds the working copy).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import structlog

from mate.api.config import get_settings
from mate.api.storage import eviction, quota, s3
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)

# Dirs already hydrated (or confirmed locally present) in this process - bounds
# the S3 list/download attempts to once per dir so repeated reads of a warm
# cache don't hit the network. Only populated on a *successful* hydrate or a
# confirmed-non-empty local dir, so a failure is retried on the next read.
_hydrated: set[str] = set()
_hydrated_lock = threading.Lock()


def rel_key(local_dir: Path) -> str:
    """Map a local dir under ``data_dir`` to its mirrored S3 key prefix."""
    data_dir = get_settings().data_dir.resolve()
    rel = local_dir.resolve().relative_to(data_dir).as_posix()
    prefix = get_storage_settings().prefix.strip("/")
    base = f"{prefix}/{rel}" if prefix else rel
    return base.rstrip("/") + "/"


def forget(local_dir: Path) -> None:
    """Drop a dir's hydration short-circuit so the next read re-pulls from S3.

    Called by the cache reaper after it evicts the local copy: without this the
    ``_hydrated`` membership check would skip the re-download and the read would
    find an empty dir.
    """
    with _hydrated_lock:
        _hydrated.discard(str(local_dir.resolve()))


# --------------------------------------------------------------------------
# Sync core (safe to call from worker threads, e.g. the result cache).
# --------------------------------------------------------------------------


def _is_original(rel: str) -> bool:
    """True for a log's retained upload (``original.{ext}``) at the dir root.

    Data hydration skips it - it's large and only re-import / remap / duplicate /
    admin-download need it, fetched on demand via :func:`hydrate_original_sync`.
    """
    return rel.split("/", 1)[0].startswith("original.")


def persist_dir_sync(local_dir: Path) -> None:
    if not is_s3() or not local_dir.exists():
        return
    key = rel_key(local_dir)
    try:
        n = s3.upload_dir(local_dir, key)
        eviction.touch(local_dir)  # a write counts as an access for the reaper
        log.info("storage.persist", dir=str(local_dir), key=key, objects=n)
    except s3.StorageError as exc:
        log.error("storage.persist_failed", dir=str(local_dir), error=str(exc))


def hydrate_dir_sync(local_dir: Path) -> None:
    if not is_s3():
        return
    marker = str(local_dir.resolve())
    with _hydrated_lock:
        if marker in _hydrated:
            return
    # Local cache already warm - nothing to fetch.
    if local_dir.exists() and any(local_dir.iterdir()):
        eviction.touch(local_dir)
        with _hydrated_lock:
            _hydrated.add(marker)
        return
    key = rel_key(local_dir)
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        # Skip the (potentially large) original upload - data reads never need it.
        n = s3.download_prefix(key, local_dir, skip=_is_original)
        eviction.touch(local_dir)
        log.info("storage.hydrate", dir=str(local_dir), key=key, objects=n)
        with _hydrated_lock:
            _hydrated.add(marker)
    except s3.StorageError as exc:
        log.error("storage.hydrate_failed", dir=str(local_dir), error=str(exc))


def hydrate_original_sync(local_dir: Path) -> None:
    """Fetch only a log's retained ``original.*`` upload (S3 mode).

    Data hydration deliberately skips the original; re-import / remap / duplicate /
    admin-download call this to pull it on demand. No-op if a local ``original.*``
    already exists or in local mode. Independent of the ``_hydrated`` data marker.
    """
    if not is_s3():
        return
    if local_dir.exists() and any(local_dir.glob("original.*")):
        return
    key = rel_key(local_dir)
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        n = s3.download_prefix(key, local_dir, skip=lambda rel: not _is_original(rel))
        log.info("storage.hydrate_original", dir=str(local_dir), key=key, objects=n)
    except s3.StorageError as exc:
        log.error("storage.hydrate_original_failed", dir=str(local_dir), error=str(exc))


def delete_dir_remote_sync(local_dir: Path) -> None:
    if not is_s3():
        return
    key = rel_key(local_dir)
    try:
        s3.delete_prefix(key)
        # Freed space - drop the cached usage total so a quota-blocked user who
        # deletes to make room isn't held back by a stale count.
        quota.invalidate_usage_cache()
    except s3.StorageError as exc:
        log.error("storage.delete_failed", key=key, error=str(exc))
    with _hydrated_lock:
        _hydrated.discard(str(local_dir.resolve()))


# --------------------------------------------------------------------------
# Async wrappers (off the event loop) + per-resource helpers.
# --------------------------------------------------------------------------


async def persist_dir(local_dir: Path) -> None:
    await asyncio.to_thread(persist_dir_sync, local_dir)


async def hydrate_dir(local_dir: Path) -> None:
    await asyncio.to_thread(hydrate_dir_sync, local_dir)


async def delete_dir_remote(local_dir: Path) -> None:
    await asyncio.to_thread(delete_dir_remote_sync, local_dir)


def _log_dir(user_id: str, log_id: str) -> Path:
    return get_settings().event_logs_dir_for(user_id) / log_id


async def persist_log(user_id: str, log_id: str) -> None:
    await persist_dir(_log_dir(user_id, log_id))


async def hydrate_log(user_id: str, log_id: str) -> None:
    await hydrate_dir(_log_dir(user_id, log_id))


async def hydrate_original(user_id: str, log_id: str) -> None:
    """Pull a log's retained upload on demand (re-import / remap / duplicate /
    admin-download). Pairs with :func:`hydrate_log`, which omits the original."""
    await asyncio.to_thread(hydrate_original_sync, _log_dir(user_id, log_id))


async def delete_log(user_id: str, log_id: str) -> None:
    await delete_dir_remote(_log_dir(user_id, log_id))
