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
from mate.api.storage import s3
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)

# Dirs already hydrated (or confirmed locally present) in this process - bounds
# the S3 list/download attempts to once per dir so repeated reads of a warm
# cache don't hit the network. Only populated on a *successful* hydrate or a
# confirmed-non-empty local dir, so a failure is retried on the next read.
_hydrated: set[str] = set()
_hydrated_lock = threading.Lock()


def _rel_key(local_dir: Path) -> str:
    """Map a local dir under ``data_dir`` to its mirrored S3 key prefix."""
    data_dir = get_settings().data_dir.resolve()
    rel = local_dir.resolve().relative_to(data_dir).as_posix()
    prefix = get_storage_settings().prefix.strip("/")
    base = f"{prefix}/{rel}" if prefix else rel
    return base.rstrip("/") + "/"


# --------------------------------------------------------------------------
# Sync core (safe to call from worker threads, e.g. the result cache).
# --------------------------------------------------------------------------


def persist_dir_sync(local_dir: Path) -> None:
    if not is_s3() or not local_dir.exists():
        return
    key = _rel_key(local_dir)
    try:
        n = s3.upload_dir(local_dir, key)
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
        with _hydrated_lock:
            _hydrated.add(marker)
        return
    key = _rel_key(local_dir)
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        n = s3.download_prefix(key, local_dir)
        log.info("storage.hydrate", dir=str(local_dir), key=key, objects=n)
        with _hydrated_lock:
            _hydrated.add(marker)
    except s3.StorageError as exc:
        log.error("storage.hydrate_failed", dir=str(local_dir), error=str(exc))


def delete_dir_remote_sync(local_dir: Path) -> None:
    if not is_s3():
        return
    key = _rel_key(local_dir)
    try:
        s3.delete_prefix(key)
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


async def delete_log(user_id: str, log_id: str) -> None:
    await delete_dir_remote(_log_dir(user_id, log_id))
