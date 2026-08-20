"""Storage backend: local disk (default) or a connected S3/Ceph bucket.

``config`` resolves the global ``storage_config`` row; ``s3`` wraps boto3; and
``sync`` mirrors the local working dirs to the bucket on write / hydrates them on
read-miss. See ``routes/admin_storage.py`` for the admin API and the project
DEPLOY notes for operational guidance.
"""

from __future__ import annotations

from mate.api.storage.config import (
    StorageSettings,
    decrypt_secret,
    encrypt_secret,
    get_storage_settings,
    invalidate,
    is_s3,
)

__all__ = [
    "StorageSettings",
    "decrypt_secret",
    "encrypt_secret",
    "get_storage_settings",
    "invalidate",
    "is_s3",
]
