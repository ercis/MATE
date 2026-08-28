"""boto3 S3 / Ceph-RGW client and the object operations the sync layer needs.

boto3/botocore are imported lazily so the storage package stays light when the
platform runs in local mode (the common dev/test case). Every public op raises
:class:`StorageError` with a readable message instead of leaking botocore
exception types to callers.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mate.api.storage.config import StorageSettings, get_storage_settings

# Per-dir transfer fan-out. boto3 clients are thread-safe and S3 I/O releases the
# GIL, so parallel put/get genuinely overlaps - a many-file OCEL log uploads /
# hydrates in a fraction of the serial wall-clock.
_TRANSFER_CONCURRENCY = 8


class StorageError(RuntimeError):
    """An S3 operation failed (wraps the underlying botocore exception)."""


def _run_parallel(tasks: list[Callable[[], None]]) -> None:
    """Run per-object transfer thunks across a small thread pool.

    Re-raises the first error after the in-flight transfers settle. Used by
    ``upload_dir`` / ``download_prefix``; a single task runs inline (no pool).
    """
    if not tasks:
        return
    if len(tasks) == 1:
        tasks[0]()
        return
    first_error: Exception | None = None
    with ThreadPoolExecutor(max_workers=min(_TRANSFER_CONCURRENCY, len(tasks))) as pool:
        for future in as_completed([pool.submit(t) for t in tasks]):
            exc = future.exception()
            if exc is not None and first_error is None:
                first_error = exc  # type: ignore[assignment]
    if first_error is not None:
        raise first_error


def make_client(s: StorageSettings | None = None) -> Any:
    s = s or get_storage_settings()
    if not s.bucket:
        raise StorageError("S3 is not fully configured (bucket required).")
    import boto3
    from botocore.config import Config

    addressing = "path" if s.path_style else "auto"
    return boto3.client(
        "s3",
        # None = AWS S3 proper (boto3 derives the regional endpoint); every
        # non-AWS provider sets an explicit endpoint URL.
        endpoint_url=s.endpoint_url or None,
        aws_access_key_id=s.access_key,
        aws_secret_access_key=s.secret_key,
        region_name=s.region or "us-east-1",
        use_ssl=s.use_ssl,
        verify=s.verify,
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

    Files transfer in parallel; ``client.upload_file`` auto-switches to multipart
    above boto3's threshold (~8 MB), so large Parquet uploads correctly. Returns
    the number of objects written.
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/")
    files = [p for p in local_dir.rglob("*") if p.is_file()]

    def _upload(path: Path) -> None:
        rel = path.relative_to(local_dir).as_posix()
        client.upload_file(str(path), bucket, f"{base}/{rel}")

    try:
        _run_parallel([lambda p=path: _upload(p) for path in files])
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return len(files)


def download_prefix(
    key_prefix: str,
    local_dir: Path,
    s: StorageSettings | None = None,
    skip: Callable[[str], bool] | None = None,
) -> int:
    """Download objects under ``key_prefix`` into ``local_dir`` (mirroring), in
    parallel, verifying each file's byte length against the listed size.

    ``skip(rel)`` (relative POSIX path) returning True omits that object - lets a
    caller hydrate only part of a prefix (e.g. data Parquet without the large
    ``original.*`` upload). Returns the number of objects fetched.
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/") + "/"
    # List first (one pass) so each download can verify its expected size.
    objects: list[tuple[str, str, int]] = []  # (key, rel, size)
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=base):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(base) :]
                if not rel:  # the prefix "directory marker" itself
                    continue
                if skip is not None and skip(rel):
                    continue
                objects.append((key, rel, int(obj.get("Size", 0))))
    except Exception as exc:
        raise StorageError(str(exc)) from exc

    def _download(key: str, rel: str, size: int) -> None:
        dest = local_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)  # exist_ok races safely
        client.download_file(bucket, key, str(dest))
        actual = dest.stat().st_size
        if actual != size:  # truncated / partial fetch - fail so the caller retries
            raise StorageError(f"Partial download of {key}: expected {size} bytes, got {actual}")

    try:
        _run_parallel(
            [lambda k=key, r=rel, sz=size: _download(k, r, sz) for key, rel, size in objects]
        )
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return len(objects)


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
    combined with the ``STORAGE_S3_PREFIX`` setting, so a watch can point at any
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


def prefix_nonempty(key_prefix: str, s: StorageSettings | None = None) -> bool:
    """True iff at least one object exists under ``key_prefix``.

    A cheap ``MaxKeys=1`` list - used by the cache reaper to confirm a
    re-hydratable remote copy exists before deleting the local one.
    """
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    base = key_prefix.rstrip("/") + "/"
    try:
        resp = client.list_objects_v2(Bucket=bucket, Prefix=base, MaxKeys=1)
    except Exception as exc:
        raise StorageError(str(exc)) from exc
    return int(resp.get("KeyCount", 0)) > 0


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


def upload_object(local_file: Path, key: str, s: StorageSettings | None = None) -> None:
    """Upload a single file to ``key`` (auto-multipart for large files via boto3)."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    try:
        client.upload_file(str(local_file), bucket, key)
    except Exception as exc:
        raise StorageError(str(exc)) from exc


def delete_object(key: str, s: StorageSettings | None = None) -> None:
    """Delete a single object by exact key (no prefix expansion)."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise StorageError(str(exc)) from exc


def object_exists(key: str, s: StorageSettings | None = None) -> bool:
    """True iff ``key`` exists. A transient error is reported as absent (the
    callers - db restore, module restore - must not act on an uncertain HEAD)."""
    s = s or get_storage_settings()
    client = make_client(s)
    bucket = _bucket(s)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


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
