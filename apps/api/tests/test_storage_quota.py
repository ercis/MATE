"""Global storage-quota enforcement (S3_OFFLOAD Phase 3.2). S3 usage is stubbed."""

from __future__ import annotations

import pytest

from mate.api.storage import quota
from mate.api.storage.config import StorageSettings
from mate.api.storage.s3 import StorageError, Usage


def _settings(*, mode: str = "s3", quota_bytes: int | None = None) -> StorageSettings:
    return StorageSettings(
        mode=mode, endpoint_url="https://s3", bucket="b", quota_bytes=quota_bytes
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    quota.invalidate_usage_cache()
    yield
    quota.invalidate_usage_cache()


def test_local_mode_never_over_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quota, "get_storage_settings", lambda: _settings(mode="local", quota_bytes=1)
    )
    monkeypatch.setattr(quota.s3, "usage", lambda *a, **k: Usage(used_bytes=10**9, object_count=1))
    assert quota.over_quota_sync() is False


def test_no_quota_set_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "get_storage_settings", lambda: _settings(quota_bytes=None))
    assert quota.over_quota_sync() is False


def test_under_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "get_storage_settings", lambda: _settings(quota_bytes=1000))
    monkeypatch.setattr(quota.s3, "usage", lambda *a, **k: Usage(used_bytes=500, object_count=1))
    assert quota.over_quota_sync() is False


def test_at_or_over_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "get_storage_settings", lambda: _settings(quota_bytes=1000))
    monkeypatch.setattr(quota.s3, "usage", lambda *a, **k: Usage(used_bytes=1000, object_count=1))
    assert quota.over_quota_sync() is True


def test_usage_error_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "get_storage_settings", lambda: _settings(quota_bytes=1))

    def boom(*a, **k):
        raise StorageError("list failed")

    monkeypatch.setattr(quota.s3, "usage", boom)
    assert quota.over_quota_sync() is False


def test_usage_is_cached_then_invalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "get_storage_settings", lambda: _settings(quota_bytes=10**9))
    calls = {"n": 0}

    def counting_usage(*a, **k):
        calls["n"] += 1
        return Usage(used_bytes=1, object_count=1)

    monkeypatch.setattr(quota.s3, "usage", counting_usage)
    quota.over_quota_sync()
    quota.over_quota_sync()
    assert calls["n"] == 1  # second call served from cache
    quota.invalidate_usage_cache()
    quota.over_quota_sync()
    assert calls["n"] == 2  # re-listed after invalidation
