"""Global storage-backend configuration + S3 secret encryption.

The single ``storage_config`` row (:class:`mate.api.db.models.StorageConfig`)
selects where event logs and module outputs are durably stored. This module
caches the resolved settings in-process and decrypts the stored S3 secret, so
the per-operation sync hooks (:mod:`mate.api.storage.sync`) can cheaply ask
``is_s3()`` without a DB round-trip on the hot path.

Reads use a short-lived raw ``sqlite3`` connection (the row is a tiny singleton
and SQLite WAL handles concurrent readers) so the cache can be warmed from any
thread - including the DuckDB/ingest worker threads that hold no async session.
Writes go through the async ORM in ``routes/admin_storage.py``, which calls
:func:`invalidate` afterwards.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading
from dataclasses import dataclass

import structlog
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import make_url

from mate.api.config import get_settings
from mate.api.db.models import StorageConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StorageSettings:
    """Resolved, in-process view of the storage config (secret decrypted)."""

    mode: str = "local"
    endpoint_url: str | None = None
    bucket: str | None = None
    region: str | None = None
    access_key: str | None = None
    # Decrypted plaintext - kept in-process only, never serialised back out.
    secret_key: str | None = None
    path_style: bool = True
    use_ssl: bool = True
    prefix: str = ""
    quota_bytes: int | None = None

    @property
    def is_s3(self) -> bool:
        return self.mode == "s3" and bool(self.bucket) and bool(self.endpoint_url)


_DEFAULT = StorageSettings()
_cache: StorageSettings | None = None
_lock = threading.Lock()


def _fernet() -> Fernet:
    settings = get_settings()
    # Falls back to the DB URL so local dev works with no extra env; prod MUST
    # set STORAGE_ENCRYPTION_KEY (and keep it stable - see config/.env.example).
    secret = settings.storage_encryption_key or settings.database_url
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(enc: str | None) -> str | None:
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError):
        # Wrong/rotated STORAGE_ENCRYPTION_KEY - admin must re-enter the secret.
        log.warning("storage_secret_decrypt_failed")
        return None


def _sqlite_path() -> str | None:
    url = make_url(get_settings().database_url)
    return url.database


def _load_from_db() -> StorageSettings:
    path = _sqlite_path()
    if not path:
        return _DEFAULT
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error:
        return _DEFAULT
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT mode, endpoint_url, bucket, region, access_key, secret_key_enc, "
            "path_style, use_ssl, prefix, quota_bytes FROM storage_config WHERE id = ?",
            (StorageConfig.SINGLETON_ID,),
        ).fetchone()
    except sqlite3.Error:
        # Table not created yet (pre-migration) → safe local default.
        return _DEFAULT
    finally:
        conn.close()
    if row is None:
        return _DEFAULT
    return StorageSettings(
        mode=row["mode"] or "local",
        endpoint_url=row["endpoint_url"],
        bucket=row["bucket"],
        region=row["region"],
        access_key=row["access_key"],
        secret_key=decrypt_secret(row["secret_key_enc"]),
        path_style=bool(row["path_style"]),
        use_ssl=bool(row["use_ssl"]),
        prefix=row["prefix"] or "",
        quota_bytes=row["quota_bytes"],
    )


def get_storage_settings() -> StorageSettings:
    """Return the cached storage settings, loading them from the DB on first use."""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = _load_from_db()
    return _cache


def invalidate() -> None:
    """Drop the cache so the next read reflects a just-saved config."""
    global _cache
    with _lock:
        _cache = None


def is_s3() -> bool:
    return get_storage_settings().is_s3
