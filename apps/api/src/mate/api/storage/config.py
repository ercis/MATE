"""Storage-backend configuration, resolved once from the environment.

The backend is selected by ``STORAGE_MODE`` (+ ``STORAGE_S3_*`` connection
details) in the platform env (.env / compose) - see :class:`mate.api.config.
Settings` and docs/S3_OFFLOAD.md. There is no DB row and no admin UI: the env
is the single source of truth, which also means the pre-boot ``db_backup
restore`` CLI on a fresh VM reads exactly the same configuration as the
running app.

The resolved settings are cached in-process so the per-operation sync hooks
(:mod:`mate.api.storage.sync`) can cheaply ask ``is_s3()`` on the hot path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from mate.api.config import get_settings


@dataclass(frozen=True)
class StorageSettings:
    """Resolved, in-process view of the storage config."""

    mode: str = "local"
    endpoint_url: str | None = None
    bucket: str | None = None
    region: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    path_style: bool = True
    use_ssl: bool = True
    # TLS verification for the S3 endpoint: None = default CA verification,
    # False = disabled, str = path to a CA bundle (on-prem internal CAs).
    verify: bool | str | None = None
    prefix: str = ""
    quota_bytes: int | None = None

    @property
    def is_s3(self) -> bool:
        # Endpoint is optional: empty means AWS S3 proper (boto3 derives the
        # regional endpoint); non-AWS providers must set one.
        return self.mode == "s3" and bool(self.bucket)


_cache: StorageSettings | None = None
_lock = threading.Lock()


def _parse_verify(raw: str) -> bool | str | None:
    val = raw.strip()
    if not val or val.lower() in ("1", "true", "yes", "on"):
        return None  # boto3 default: verify against system CAs
    if val.lower() in ("0", "false", "no", "off"):
        return False
    return val  # a CA bundle path


def _resolve() -> StorageSettings:
    s = get_settings()
    if s.storage_mode != "s3":
        return StorageSettings()
    return StorageSettings(
        mode="s3",
        endpoint_url=s.storage_s3_endpoint.strip() or None,
        bucket=s.storage_s3_bucket.strip() or None,
        region=s.storage_s3_region.strip() or None,
        access_key=s.storage_s3_access_key.strip() or None,
        secret_key=s.storage_s3_secret_key or None,
        path_style=s.storage_s3_path_style,
        use_ssl=s.storage_s3_use_ssl,
        verify=_parse_verify(s.storage_s3_verify),
        prefix=s.storage_s3_prefix.strip().strip("/"),
        quota_bytes=s.storage_s3_quota_bytes or None,
    )


def get_storage_settings() -> StorageSettings:
    """Return the cached storage settings, resolving them from env on first use."""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = _resolve()
    return _cache


def invalidate() -> None:
    """Drop the cache so the next read re-resolves (tests re-point the env)."""
    global _cache
    with _lock:
        _cache = None


def is_s3() -> bool:
    return get_storage_settings().is_s3
