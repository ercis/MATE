"""Unit tests for the S3-mode local-cache reaper (``storage.eviction``).

No S3 / DB needed: ``is_s3``, ``get_settings`` and the remote-copy check are
stubbed so we exercise the selection + safety logic directly.
"""

from __future__ import annotations

import os
import time
import types
from pathlib import Path

import pytest

from mate.api.storage import eviction
from mate.api.storage.eviction import EvictionConfig

_OLD = 100_000  # seconds in the past - older than any test's min_age window


def _make_dir(parent: Path, user: str, log_id: str, size: int, age_s: float) -> Path:
    """Create ``{parent}/{user}/event_logs/{log_id}`` with one file of ``size``
    bytes whose mtime is ``age_s`` seconds in the past."""
    d = parent / user / "event_logs" / log_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / "events.parquet"
    f.write_bytes(b"\0" * size)
    ts = time.time() - age_s
    os.utime(f, (ts, ts))
    return d


@pytest.fixture
def evict_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point eviction at a tmp data root, force S3 mode, stub the remote check,
    and reset module-global lease/atime state around each test."""
    eviction._leases.clear()
    eviction._evicting.clear()
    eviction._atime.clear()
    monkeypatch.setattr(eviction, "get_settings", lambda: types.SimpleNamespace(users_dir=tmp_path))
    monkeypatch.setattr(eviction, "is_s3", lambda: True)
    # A remote copy always "exists", so a real eviction may delete locally.
    monkeypatch.setattr(eviction, "_remote_has_copy", lambda _d: True)
    yield tmp_path
    eviction._leases.clear()
    eviction._evicting.clear()
    eviction._atime.clear()


def _cfg(max_bytes: int, *, dry_run: bool = False, min_age: float = 3600.0) -> EvictionConfig:
    return EvictionConfig(max_bytes=max_bytes, dry_run=dry_run, min_age_seconds=min_age)


def test_local_mode_is_a_noop(evict_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Even wildly over a tiny budget, local mode must never evict (data loss).
    monkeypatch.setattr(eviction, "is_s3", lambda: False)
    _make_dir(evict_env, "u1", "log1", size=1000, age_s=_OLD)
    assert eviction.evict_once(_cfg(1)) is None


def test_disabled_budget_is_a_noop(evict_env: Path) -> None:
    _make_dir(evict_env, "u1", "log1", size=1000, age_s=_OLD)
    assert eviction.evict_once(_cfg(0)) is None


def test_under_budget_evicts_nothing(evict_env: Path) -> None:
    d = _make_dir(evict_env, "u1", "log1", size=1000, age_s=_OLD)
    report = eviction.evict_once(_cfg(10_000))
    assert report is not None
    assert report.evicted == []
    assert d.exists()


def test_dry_run_reports_but_keeps_files(evict_env: Path) -> None:
    d1 = _make_dir(evict_env, "u1", "log1", size=1000, age_s=_OLD + 2)
    d2 = _make_dir(evict_env, "u1", "log2", size=1000, age_s=_OLD + 1)
    report = eviction.evict_once(_cfg(1500, dry_run=True))
    assert report is not None
    assert report.dry_run and report.evicted  # candidates listed
    assert d1.exists() and d2.exists()  # nothing deleted


def test_evicts_lru_first_and_respects_min_age(evict_env: Path) -> None:
    # Three 1000-byte dirs, total 3000 > budget 1500 (target = 1350). The two
    # oldest are evicted; the fresh one is protected by min_age.
    old1 = _make_dir(evict_env, "u1", "old1", size=1000, age_s=_OLD + 10)
    old2 = _make_dir(evict_env, "u1", "old2", size=1000, age_s=_OLD + 5)
    fresh = _make_dir(evict_env, "u1", "fresh", size=1000, age_s=1)
    report = eviction.evict_once(_cfg(1500, min_age=3600))
    assert report is not None
    assert not old1.exists() and not old2.exists()
    assert fresh.exists()  # too recent to evict
    assert report.reclaimed_bytes == 2000


def test_leased_dir_is_never_evicted(evict_env: Path) -> None:
    old1 = _make_dir(evict_env, "u1", "old1", size=1000, age_s=_OLD + 10)
    old2 = _make_dir(evict_env, "u1", "old2", size=1000, age_s=_OLD + 5)
    marker = eviction.acquire_lease(old1)  # pin the LRU victim
    try:
        report = eviction.evict_once(_cfg(1500, min_age=3600))
    finally:
        eviction.release_lease(marker)
    assert report is not None
    assert old1.exists()  # leased - skipped despite being the oldest
    assert not old2.exists()  # next candidate evicted instead


def test_lease_refcount_release(evict_env: Path) -> None:
    d = _make_dir(evict_env, "u1", "log1", size=10, age_s=_OLD)
    m1 = eviction.acquire_lease(d)
    m2 = eviction.acquire_lease(d)
    assert eviction._leases[m1] == 2
    eviction.release_lease(m1)
    assert eviction._leases[m2] == 1
    eviction.release_lease(m2)
    assert m1 not in eviction._leases  # fully released, key dropped


def test_lease_dir_context_manager(evict_env: Path) -> None:
    d = _make_dir(evict_env, "u1", "log1", size=10, age_s=_OLD)
    with eviction.LeaseDir(d):
        marker = eviction._marker(d)
        assert eviction._leases.get(marker, 0) == 1
    assert eviction._marker(d) not in eviction._leases
