"""Local-cache eviction for S3 mode - bound the VM working set (see S3_OFFLOAD.md).

In S3 mode the bucket is the authoritative copy and local disk is a *cache*: a
log/output dir is written locally, uploaded to S3 (``storage.sync``), and may
later be reclaimed here once it goes cold. This module bounds that cache so
turning S3 on actually shrinks the VM instead of keeping a second forever-copy.

Two cooperating pieces:

* **Leases** - every read/write of a cache dir takes a refcount lease for its
  duration (``EventLogAccess``/``ObjectCentricLogAccess``/``ResultCache``). The
  reaper never evicts a leased dir, and a reader that races an in-flight
  eviction of the same dir blocks until it finishes - so a tree can't be deleted
  out from under a running DuckDB scan or a Parquet rewrite.
* **Reaper** - ``eviction_loop`` periodically sweeps; when the cache exceeds the
  configured budget (``LOCAL_CACHE_MAX_BYTES``) it deletes the least-recently-
  used *unleased* dirs locally (the bytes survive on S3 and re-hydrate on the
  next read).

Hard invariants:

* Eviction runs ONLY in S3 mode. In local mode the local copy is the only copy;
  deleting it is data loss, so the reaper no-ops.
* A dir is deleted locally only after its S3 prefix is confirmed non-empty
  (a remote copy provably exists to re-hydrate from).
* ``dry_run`` (the default) logs what it would evict and deletes nothing - soak
  first, enable deletes once the candidate set looks right.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from mate.api.config import get_settings
from mate.api.storage.config import is_s3

log = structlog.get_logger(__name__)

# Lease + eviction coordination. One Condition guards every map below; a reader
# waits on it to out-last an in-flight eviction of the same dir (sub-second)
# rather than racing the rmtree. All keyed by the resolved dir path.
_cv = threading.Condition(threading.Lock())
_leases: dict[str, int] = {}  # dir -> active reader/writer count
_evicting: set[str] = set()  # dirs the reaper is mid-delete on
_atime: dict[str, float] = {}  # dir -> last-access epoch (this process)


def _marker(local_dir: Path) -> str:
    return str(local_dir.resolve())


# --------------------------------------------------------------------------
# Bookkeeping + leases (called from the read/write hot paths).
# --------------------------------------------------------------------------


def touch(local_dir: Path) -> None:
    """Record an access (read or write) so the LRU reaper sees this dir as warm.

    Cheap and lock-guarded; called from the sync hooks so a persist/hydrate that
    doesn't take a full lease (e.g. ingest) still refreshes the dir's atime.
    """
    m = _marker(local_dir)
    with _cv:
        _atime[m] = time.time()


def acquire_lease(local_dir: Path) -> str:
    """Pin a cache dir for the duration of a read/write.

    Blocks (briefly) if the reaper is mid-eviction of this exact dir, then takes
    the lease. Returns the marker to pass back to :func:`release_lease`.
    """
    m = _marker(local_dir)
    with _cv:
        while m in _evicting:
            _cv.wait()
        _leases[m] = _leases.get(m, 0) + 1
        _atime[m] = time.time()
    return m


def release_lease(marker: str) -> None:
    with _cv:
        n = _leases.get(marker, 0) - 1
        if n <= 0:
            _leases.pop(marker, None)
        else:
            _leases[marker] = n


async def acquire_lease_async(local_dir: Path) -> str:
    """Async wrapper: take the lease off the event loop (it may briefly block)."""
    return await asyncio.to_thread(acquire_lease, local_dir)


class LeaseDir:
    """Sync context manager pinning ``local_dir`` against eviction for its body."""

    def __init__(self, local_dir: Path) -> None:
        self._dir = local_dir
        self._marker: str | None = None

    def __enter__(self) -> None:
        self._marker = acquire_lease(self._dir)

    def __exit__(self, *exc: object) -> None:
        if self._marker is not None:
            release_lease(self._marker)
            self._marker = None


def _begin_evict(marker: str) -> bool:
    """Reserve a dir for eviction. False if it's leased or already being evicted."""
    with _cv:
        if _leases.get(marker, 0) > 0 or marker in _evicting:
            return False
        _evicting.add(marker)
        return True


def _end_evict(marker: str) -> None:
    with _cv:
        _evicting.discard(marker)
        _atime.pop(marker, None)
        _cv.notify_all()


# --------------------------------------------------------------------------
# Config (env-only: LOCAL_CACHE_MAX_BYTES / CACHE_EVICT_* in Settings).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvictionConfig:
    max_bytes: int  # 0 disables the reaper (keep every synced copy)
    dry_run: bool  # log candidates, delete nothing
    min_age_seconds: float  # never evict a dir accessed within this window
    low_water_ratio: float = 0.9  # evict down to this fraction of max_bytes


def load_eviction_config() -> EvictionConfig:
    s = get_settings()
    return EvictionConfig(
        max_bytes=max(0, s.local_cache_max_bytes),
        dry_run=s.cache_evict_dry_run,
        min_age_seconds=float(s.cache_evict_min_age_seconds),
    )


# --------------------------------------------------------------------------
# Cache-dir enumeration + sizing.
# --------------------------------------------------------------------------


def _iter_subdirs(parent: Path) -> list[Path]:
    if not parent.exists():
        return []
    out: list[Path] = []
    try:
        with os.scandir(parent) as it:
            for entry in it:
                if entry.is_dir():
                    out.append(Path(entry.path))
    except OSError:
        return []
    return out


def cache_dirs() -> list[Path]:
    """Every reclaimable cache dir, at the granularity the sync layer manages.

    A log dir (``event_logs/{lid}``) and a module-output dir
    (``module_results/{lid}/{mid}``) - exactly the units ``persist_dir`` /
    ``hydrate_dir`` move, so each is safe to evict and re-hydrate as a whole
    (a module dir's ``_v_*`` filter variants ride along inside it).
    """
    users = get_settings().users_dir
    out: list[Path] = []
    for user in _iter_subdirs(users):
        out.extend(_iter_subdirs(user / "event_logs"))
        for log_dir in _iter_subdirs(user / "module_results"):
            out.extend(_iter_subdirs(log_dir))
    return out


def _dir_stat(d: Path) -> tuple[int, float]:
    """(total bytes, newest file mtime) for a cache dir, via one walk."""
    total = 0
    newest = 0.0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                st = os.stat(os.path.join(root, f))
            except OSError:
                continue
            total += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
    return total, newest


def local_cache_bytes() -> int:
    """Total bytes the reclaimable cache currently occupies on local disk."""
    return sum(_dir_stat(d)[0] for d in cache_dirs())


def _effective_atime(marker: str, mtime: float) -> float:
    """LRU key: the more recent of this process's tracked atime and file mtime.

    mtime is the restart-surviving fallback (the in-process ``_atime`` map is
    empty after a boot), so a dir cold to this process still sorts by real age.
    """
    with _cv:
        a = _atime.get(marker, 0.0)
    return max(a, mtime)


# --------------------------------------------------------------------------
# The reaper.
# --------------------------------------------------------------------------


@dataclass
class EvictionReport:
    total_bytes: int
    max_bytes: int
    candidates_considered: int
    dry_run: bool
    evicted: list[str] = field(default_factory=list)
    reclaimed_bytes: int = 0


def _remote_has_copy(local_dir: Path) -> bool:
    """True iff the dir's S3 prefix holds at least one object (a re-hydratable
    copy provably exists). Failure is treated as 'no' - never delete on doubt."""
    try:
        from mate.api.storage import s3, sync

        return s3.prefix_nonempty(sync.rel_key(local_dir))
    except Exception:
        log.warning("storage.eviction.remote_check_failed", dir=str(local_dir), exc_info=True)
        return False


def _evict_dir(local_dir: Path, marker: str) -> bool:
    """Delete a cold dir locally after confirming its S3 copy. False if skipped."""
    if not _begin_evict(marker):
        return False  # got leased between selection and now
    try:
        if not _remote_has_copy(local_dir):
            return False
        shutil.rmtree(local_dir, ignore_errors=True)
        # Drop the hydration short-circuit so the next read re-pulls from S3.
        from mate.api.storage import sync

        sync.forget(local_dir)
        log.info("storage.eviction.evicted", dir=str(local_dir))
        return True
    finally:
        _end_evict(marker)


def evict_once(cfg: EvictionConfig) -> EvictionReport | None:
    """One reaper pass (runs in a worker thread). None when eviction is inert."""
    if not is_s3() or cfg.max_bytes <= 0:
        return None  # never evict in local mode or with no budget

    now = time.time()
    stats: list[tuple[Path, str, int, float]] = []  # dir, marker, bytes, eff_atime
    total = 0
    for d in cache_dirs():
        size, mtime = _dir_stat(d)
        total += size
        m = _marker(d)
        stats.append((d, m, size, _effective_atime(m, mtime)))

    report = EvictionReport(
        total_bytes=total,
        max_bytes=cfg.max_bytes,
        candidates_considered=len(stats),
        dry_run=cfg.dry_run,
    )
    if total <= cfg.max_bytes:
        return report

    target = int(cfg.max_bytes * cfg.low_water_ratio)
    stats.sort(key=lambda t: t[3])  # LRU: oldest effective-atime first
    freed = 0
    for d, m, size, atime in stats:
        if total - freed <= target:
            break
        if now - atime < cfg.min_age_seconds:
            continue  # too recently used to be safe
        with _cv:
            if _leases.get(m, 0) > 0:
                continue  # pinned by an active read/write
        if cfg.dry_run:
            report.evicted.append(m)
            freed += size
            continue
        if _evict_dir(d, m):
            report.evicted.append(m)
            freed += size

    report.reclaimed_bytes = freed
    return report


async def eviction_loop() -> None:
    """Background reaper: wake every ``cache_evict_interval_seconds`` and, in S3
    mode with a budget set, sweep the local cache back under it. Errors are
    swallowed so a transient hiccup never tears the loop down."""
    interval = max(5.0, float(get_settings().cache_evict_interval_seconds))
    while True:
        try:
            await asyncio.sleep(interval)
            if not is_s3():
                continue  # cheap guard - no scan in local mode
            cfg = load_eviction_config()
            if cfg.max_bytes <= 0:
                continue
            report = await asyncio.to_thread(evict_once, cfg)
            if report is not None and (report.evicted or report.total_bytes > report.max_bytes):
                log.info(
                    "storage.eviction.swept",
                    total_bytes=report.total_bytes,
                    max_bytes=report.max_bytes,
                    evicted=len(report.evicted),
                    reclaimed_bytes=report.reclaimed_bytes,
                    dry_run=report.dry_run,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("storage.eviction.tick_failed", exc_info=True)
