"""Backend-switch data migration CLI (S3_OFFLOAD Phase 3.1).

Switching ``STORAGE_MODE`` doesn't move existing data - new writes go to the
new backend, old data stays where it was (stranded). This copies it::

    uv run python -m mate.api.storage.migration to_s3     # after local → s3
    uv run python -m mate.api.storage.migration to_local  # before s3 → local
    uv run python -m mate.api.storage.migration check     # probe the bucket

Copy-only - it NEVER deletes the source, so it's always safe to re-run. Both
data directions require S3 to be the active backend (the copy reads/writes the
bucket): run ``to_s3`` after flipping the env to s3, and ``to_local`` while the
env still says s3, then flip it back. Safe to run against a live API (uploads
are additive; the sync hooks tolerate concurrent writes), but a quiet window
avoids copying a dir mid-write.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog

from mate.api.config import get_settings
from mate.api.storage import eviction, s3
from mate.api.storage import sync as storage_sync
from mate.api.storage.config import get_storage_settings, is_s3

log = structlog.get_logger(__name__)


def _s3_cache_dirs() -> list[Path]:
    """Local dir paths that have objects under the bucket's ``users/`` prefix.

    Parses each key back to its cache-dir granularity - ``users/{uid}/event_logs/
    {lid}`` and ``users/{uid}/module_results/{lid}/{mid}`` - so a pull hydrates the
    same units ``persist``/``hydrate`` move.
    """
    data_dir = get_settings().data_dir.resolve()
    admin_prefix = get_storage_settings().prefix.strip("/")
    base = f"{admin_prefix}/users/" if admin_prefix else "users/"
    dirs: set[Path] = set()
    for obj in s3.list_objects(base):
        key = obj.key
        rel = key[len(admin_prefix) + 1 :] if admin_prefix else key
        parts = rel.split("/")
        # users/{uid}/event_logs/{lid}/...  |  users/{uid}/module_results/{lid}/{mid}/...
        if len(parts) >= 4 and parts[0] == "users" and parts[2] == "event_logs":
            dirs.add(data_dir.joinpath(*parts[:4]))
        elif len(parts) >= 5 and parts[0] == "users" and parts[2] == "module_results":
            dirs.add(data_dir.joinpath(*parts[:5]))
    return sorted(dirs)


def _push_one(local_dir: Path) -> bool:
    """Upload a local dir to S3 and confirm the remote copy exists."""
    storage_sync.persist_dir_sync(local_dir)
    return s3.prefix_nonempty(storage_sync.rel_key(local_dir))


def _pull_one(local_dir: Path) -> bool:
    """Force-download a dir from S3 (bypasses the warm-cache hydrate short-circuit)."""
    s3.download_prefix(storage_sync.rel_key(local_dir), local_dir)
    return local_dir.exists() and any(local_dir.iterdir())


def migrate(
    direction: str, on_progress: Callable[[int, int, str], None] | None = None
) -> tuple[int, int]:
    """Copy every cache dir in ``direction`` (``to_s3`` | ``to_local``).

    Returns ``(migrated, failed)``. ``on_progress(done, total, dir)`` fires after
    each dir. Raises ``RuntimeError`` unless the S3 backend is active.
    """
    if not is_s3():
        raise RuntimeError(
            "Migration requires the S3 backend to be active - set STORAGE_MODE=s3 "
            "(+ STORAGE_S3_* connection vars) in the environment first."
        )
    if direction == "to_s3":
        dirs, fn = eviction.cache_dirs(), _push_one
    elif direction == "to_local":
        dirs, fn = _s3_cache_dirs(), _pull_one
    else:
        raise ValueError(f"Unknown migration direction: {direction!r}")

    ok = 0
    failed = 0
    for i, d in enumerate(dirs, 1):
        try:
            if fn(d):
                ok += 1
            else:
                failed += 1
                log.warning("storage.migrate.dir_unverified", dir=str(d))
        except Exception:
            failed += 1
            log.warning("storage.migrate.dir_failed", dir=str(d), exc_info=True)
        if on_progress is not None:
            on_progress(i, len(dirs), str(d))
    log.info(
        "storage.migrate.complete", direction=direction, migrated=ok, failed=failed, total=len(dirs)
    )
    return ok, failed


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m mate.api.storage.migration [to_s3|to_local|check]``."""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "check":
        try:
            s3.head_bucket()
            settings = get_storage_settings()
            usage = s3.usage()
            print(
                f"OK: bucket {settings.bucket!r} reachable - "
                f"{usage.object_count} objects, {usage.used_bytes} bytes under "
                f"prefix {settings.prefix!r}"
            )
            return 0
        except (s3.StorageError, RuntimeError) as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
    if cmd in ("to_s3", "to_local"):
        verb = "Uploading" if cmd == "to_s3" else "Downloading"
        try:
            ok, failed = migrate(
                cmd, on_progress=lambda i, n, d: print(f"{verb} {i}/{n}: {d}", flush=True)
            )
        except (RuntimeError, ValueError, s3.StorageError) as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"Migrated {ok} dirs ({failed} failed)")
        return 0 if failed == 0 else 1
    print(
        "usage: python -m mate.api.storage.migration [to_s3|to_local|check]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
