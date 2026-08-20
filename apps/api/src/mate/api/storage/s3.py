"""boto3 S3 / Ceph-RGW client and the object operations the sync layer needs.

boto3/botocore are imported lazily so the storage package stays light when the
platform runs in local mode (the common dev/test case). Every public op raises
:class:`StorageError` with a readable message instead of leaking botocore
exception types to callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mate.api.storage.config import StorageSettings, get_storage_settings


class StorageError(RuntimeError):
    """An S3 operation failed (wraps the underlying botocore exception)."""


def make_client(s: StorageSettings | None = None) -> Any:
    s = s or get_storage_settings()
    if not s.endpoint_url or not s.bucket:
        raise StorageError("S3 is not fully configured (endpoint and bucket required).")
    import boto3
    from botocore.config import Config

    addressing = "path" if s.path_style else "auto"
    return boto3.client(
        "s3",
        endpoint_url=s.endpoint_url,
        aws_access_key_id=s.access_key,
        aws_secret_access_key=s.secret_key,
        region_name=s.region or "us-east-1",
        use_ssl=s.use_ssl,
        config=Config(signature_version="s3v4", s3={"addressing_style": addressing}),
    )


def _bucket(s: StorageSettings) -> str:
    assert s.bucket is not None  # guarded by make_client
    return s.bucket


def head_bucket(s: StorageSettings | None = None) -> None:
    """Raise StorageError unless the configured bucket exists and is reachable."""
    s = s or get_storage_settings()
    client = make_client(s)
    try:
        client.head_bucket(Bucket=_bucket(s))
    except Exception as exc:
        raise StorageError(str(exc)) from exc


def upload_dir(local_dir: Path, key_prefix: str, s: StorageSettings | None = None) -> int:
    """Upload every file under ``local_dir`` to ``key_prefix`` (mirroring the tree).

    Returns the number of objects written.
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/")
    count = 0
    try:
        for path in sorted(local_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(local_dir).as_posix()
            client.upload_file(str(path), bucket, f"{base}/{rel}")
            count += 1
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return count


def download_prefix(key_prefix: str, local_dir: Path, s: StorageSettings | None = None) -> int:
    """Download every object under ``key_prefix`` into ``local_dir`` (mirroring).

    Returns the number of objects fetched (0 means the prefix was empty).
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/") + "/"
    count = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=base):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(base) :]
                if not rel:  # the prefix "directory marker" itself
                    continue
                dest = local_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                client.download_file(bucket, key, str(dest))
                count += 1
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return count


def delete_prefix(key_prefix: str, s: StorageSettings | None = None) -> int:
    """Delete every object under ``key_prefix``. Returns the count removed."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/") + "/"
    count = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=base):
            batch = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if batch:
                client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
                count += len(batch)
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return count


@dataclass(frozen=True)
class S3Object:
    """A single object under a prefix (used by the watched-folder scanner)."""

    key: str
    size: int
    etag: str | None
    last_modified: float | None  # epoch seconds


def list_objects(prefix: str, s: StorageSettings | None = None) -> list[S3Object]:
    """List every object directly relevant under ``prefix`` (recursive).

    ``prefix`` is used literally against the configured bucket - it is NOT
    combined with the admin ``prefix`` setting, so a watch can point at any
    location an upstream pipeline writes to. Directory markers (keys ending in
    ``/``) are skipped.
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = prefix.strip("/")
    base = f"{base}/" if base else ""
    out: list[S3Object] = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=base):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                lm = obj.get("LastModified")
                out.append(
                    S3Object(
                        key=key,
                        size=int(obj.get("Size", 0)),
                        etag=(obj.get("ETag") or "").strip('"') or None,
                        last_modified=lm.timestamp() if lm is not None else None,
                    )
                )
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return out


def download_object(key: str, dest: Path, s: StorageSettings | None = None) -> None:
    """Download a single object to ``dest`` (parent dirs created)."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, key, str(dest))
    except Exception as exc:
        raise StorageError(str(exc)) from exc


@dataclass(frozen=True)
class Usage:
    used_bytes: int
    object_count: int


def usage(prefix: str = "", s: StorageSettings | None = None) -> Usage:
    """Sum object sizes + count under ``prefix`` (the configured prefix by default)."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    scope = (prefix or s.prefix).strip("/")
    scope = f"{scope}/" if scope else ""
    total = 0
    objects = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=scope):
            for obj in page.get("Contents", []):
                total += int(obj.get("Size", 0))
                objects += 1
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return Usage(used_bytes=total, object_count=objects)
