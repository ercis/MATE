"""SQLite ``metadata.db`` → S3 snapshot for durability in S3 mode (S3_OFFLOAD.md).

In S3 mode user data (parquet, caches) lives in the bucket, but the SQLite
metadata DB - the authoritative index mapping every object key to a user/log -
is VM-local. Lose the VM and the bucket is orphaned. This ships a consistent
online snapshot of ``metadata.db`` to ``{prefix}/_system/metadata.db``
periodically and on shutdown, so S3 holds a complete restorable picture.

**Backup** uses SQLite's online-backup API (``Connection.backup``), which is safe
to run against the live WAL database - it copies a consistent snapshot even while
the app reads/writes.

**Restore is operator-invoked, NOT automatic.** ``alembic upgrade head`` runs in
the entrypoint before the app starts, so by the time the lifespan could restore,
a fresh (empty) schema DB already exists. Recovery on a new VM is a pre-boot
step, before alembic::

    STORAGE_MODE=s3 STORAGE_S3_BUCKET=... STORAGE_S3_ENDPOINT=... \\
      python -m mate.api.storage.db_backup restore

It downloads the snapshot only when the local DB is absent (never clobbers a
populated DB); the subsequent ``alembic upgrade head`` then migrates the restored
DB to head. The storage backend is configured purely via ``STORAGE_*`` env vars
(``storage.config``), so the CLI sees exactly the same bucket as the app - no
DB needed to bootstrap.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path

import structlog
from sqlalchemy.engine import make_url

from mate.api.config import get_settings
from mate.api.storage import s3
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)


def _db_path() -> Path | None:
    """Filesystem path of the SQLite metadata DB, or None for a non-SQLite URL."""
    database = make_url(get_settings().database_url).database
    return Path(database) if database else None


def _backup_key() -> str:
    prefix = get_storage_settings().prefix.strip("/")
    return f"{prefix}/_system/metadata.db" if prefix else "_system/metadata.db"


def backup_sync() -> bool:
    """Snapshot metadata.db to S3. Returns True on a successful upload.

    No-op (False) in local mode or when the DB file is missing. Best-effort:
    any failure is logged, never raised - a backup hiccup must not break a
    request or shutdown.
    """
    if not is_s3():
        return False
    path = _db_path()
    if path is None or not path.exists():
        return False
    work = Path(tempfile.mkdtemp(prefix="ff-dbbak-"))
    snapshot = work / "metadata.db"
    try:
        src = sqlite3.connect(str(path))
        try:
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)  # online backup - consistent even under WAL writes
            finally:
                dst.close()
        finally:
            src.close()
        s3.upload_object(snapshot, _backup_key())
        log.info("storage.db_backup.uploaded", key=_backup_key(), bytes=snapshot.stat().st_size)
        return True
    except Exception:
        log.warning("storage.db_backup.failed", exc_info=True)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def restore_sync() -> bool:
    """Download the S3 snapshot to the local DB path IFF the local DB is absent.

    Returns True when a snapshot was restored. Never overwrites a populated DB
    (size > 0) - so it's safe to run unconditionally before boot; on an existing
    VM it no-ops. Reads the bucket from the ``STORAGE_*`` env vars (the DB
    itself may not exist yet).
    """
    if not is_s3():
        log.info("storage.db_restore.not_s3")
        return False
    path = _db_path()
    if path is None:
        return False
    if path.exists() and path.stat().st_size > 0:
        log.info("storage.db_restore.skip_local_present", path=str(path))
        return False
    key = _backup_key()
    try:
        if not s3.object_exists(key):
            log.info("storage.db_restore.no_snapshot", key=key)
            return False
        s3.download_object(key, path)
        log.info("storage.db_restore.restored", key=key, path=str(path))
        return True
    except s3.StorageError:
        log.warning("storage.db_restore.failed", key=key, exc_info=True)
        return False


async def backup() -> None:
    await asyncio.to_thread(backup_sync)


# How often the periodic backup loop wakes (seconds). Snapshots are cheap and
# idempotent; hourly keeps the S3 copy fresh without churn.
_BACKUP_INTERVAL_SECONDS = 60 * 60


async def db_backup_loop() -> None:
    """Periodically snapshot metadata.db to S3 (no-op in local mode). Errors are
    swallowed so a transient S3 hiccup never tears the loop down."""
    while True:
        try:
            await asyncio.sleep(_BACKUP_INTERVAL_SECONDS)
            if is_s3():
                await backup()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("storage.db_backup.loop_failed", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m mate.api.storage.db_backup [restore|backup]``."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "restore"
    if cmd == "restore":
        return 0 if restore_sync() else 1
    if cmd == "backup":
        return 0 if backup_sync() else 1
    print("usage: python -m mate.api.storage.db_backup [restore|backup]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
