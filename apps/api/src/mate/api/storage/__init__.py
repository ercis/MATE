"""Storage backend: local disk (default) or a connected S3-compatible bucket.

``config`` resolves the env-only backend selection (``STORAGE_*`` vars);
``s3`` wraps boto3; and ``sync`` mirrors the local working dirs to the bucket
on write / hydrates them on read-miss. See docs/S3_OFFLOAD.md for operations.
"""

from __future__ import annotations

from mate.api.storage.config import (
    StorageSettings,
    get_storage_settings,
    invalidate,
    is_s3,
)

__all__ = [
    "StorageSettings",
    "get_storage_settings",
    "invalidate",
    "is_s3",
]
