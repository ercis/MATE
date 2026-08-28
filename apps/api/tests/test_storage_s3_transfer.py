"""Parallel + integrity transfers and lazy-original hydration (S3_OFFLOAD Phase 4).

A fake boto3 client backs ``s3.upload_dir`` / ``download_prefix`` so the parallel
fan-out, the ``skip`` filter, and the size-integrity check are exercised without
a real bucket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mate.api.storage import s3
from mate.api.storage import sync as storage_sync
from mate.api.storage.config import StorageSettings

_S = StorageSettings(mode="s3", endpoint_url="https://s3", bucket="b")


class _FakeClient:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.uploaded: dict[str, bytes] = {}
        self._truncate = False

    def get_paginator(self, _name: str):
        objects = self.objects

        class _Pag:
            def paginate(self, Bucket: str, Prefix: str):  # noqa: N803 - mirror boto3 kwargs
                yield {
                    "Contents": [
                        {"Key": k, "Size": len(v)}
                        for k, v in objects.items()
                        if k.startswith(Prefix)
                    ]
                }

        return _Pag()

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        data = self.objects[key]
        Path(dest).write_bytes(data[:-1] if self._truncate else data)

    def upload_file(self, src: str, bucket: str, key: str) -> None:
        self.uploaded[key] = Path(src).read_bytes()


def _use_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(s3, "make_client", lambda *a, **k: client)


# --------------------------------------------------------------------------
# _run_parallel
# --------------------------------------------------------------------------


def test_run_parallel_runs_all() -> None:
    ran: list[int] = []
    s3._run_parallel([lambda i=i: ran.append(i) for i in range(5)])
    assert sorted(ran) == [0, 1, 2, 3, 4]


def test_run_parallel_raises_first_error() -> None:
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        s3._run_parallel([lambda: None, boom, lambda: None])


# --------------------------------------------------------------------------
# download_prefix: parallel + skip + integrity
# --------------------------------------------------------------------------


def test_download_prefix_fetches_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            "p/events.parquet": b"DATA",
            "p/meta.json": b"{}",
            "p/original.csv": b"a,b,c",
        }
    )
    _use_client(monkeypatch, client)
    n = s3.download_prefix("p", tmp_path, s=_S)
    assert n == 3
    assert (tmp_path / "events.parquet").read_bytes() == b"DATA"
    assert (tmp_path / "original.csv").read_bytes() == b"a,b,c"


def test_download_prefix_skip_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({"p/events.parquet": b"DATA", "p/original.csv": b"a,b,c"})
    _use_client(monkeypatch, client)
    n = s3.download_prefix("p", tmp_path, s=_S, skip=lambda rel: rel.startswith("original."))
    assert n == 1
    assert (tmp_path / "events.parquet").exists()
    assert not (tmp_path / "original.csv").exists()


def test_download_prefix_integrity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient({"p/events.parquet": b"DATADATA"})
    client._truncate = True  # writes one byte short → size mismatch
    _use_client(monkeypatch, client)
    with pytest.raises(s3.StorageError, match="Partial download"):
        s3.download_prefix("p", tmp_path, s=_S)


# --------------------------------------------------------------------------
# upload_dir: parallel + multipart-capable upload_file
# --------------------------------------------------------------------------


def test_upload_dir_uploads_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ocel").mkdir()
    (tmp_path / "events.parquet").write_bytes(b"E")
    (tmp_path / "meta.json").write_bytes(b"{}")
    (tmp_path / "ocel" / "objects.parquet").write_bytes(b"O")
    client = _FakeClient({})
    _use_client(monkeypatch, client)
    n = s3.upload_dir(tmp_path, "pre", s=_S)
    assert n == 3
    assert client.uploaded["pre/events.parquet"] == b"E"
    assert client.uploaded["pre/ocel/objects.parquet"] == b"O"


# --------------------------------------------------------------------------
# Lazy original hydration (sync layer)
# --------------------------------------------------------------------------


def test_is_original() -> None:
    assert storage_sync._is_original("original.csv") is True
    assert storage_sync._is_original("original.xes.gz") is True
    assert storage_sync._is_original("events.parquet") is False
    assert storage_sync._is_original("ocel/events.parquet") is False
    assert storage_sync._is_original("meta.json") is False


def test_hydrate_original_skips_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_sync, "is_s3", lambda: True)
    d = tmp_path / "log"
    d.mkdir()
    (d / "original.csv").write_text("x")
    called: list[int] = []
    monkeypatch.setattr(storage_sync.s3, "download_prefix", lambda *a, **k: called.append(1))
    storage_sync.hydrate_original_sync(d)
    assert called == []  # already local - no fetch


def test_hydrate_original_downloads_only_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage_sync, "is_s3", lambda: True)
    monkeypatch.setattr(storage_sync, "rel_key", lambda d: "k/")
    captured: dict[str, object] = {}

    def fake_dl(key, local_dir, s=None, skip=None):
        captured["skip"] = skip
        (local_dir / "original.csv").write_bytes(b"x")
        return 1

    monkeypatch.setattr(storage_sync.s3, "download_prefix", fake_dl)
    d = tmp_path / "log"
    storage_sync.hydrate_original_sync(d)
    assert (d / "original.csv").exists()
    skip = captured["skip"]
    assert skip("events.parquet") is True  # non-original skipped
    assert skip("original.csv") is False  # original kept
