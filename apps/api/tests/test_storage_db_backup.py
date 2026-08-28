"""metadata.db → S3 backup/restore (S3_OFFLOAD Phase 2.1). S3 ops are stubbed."""

from __future__ import annotations

import shutil
import sqlite3
import types
from pathlib import Path

import pytest

from mate.api.storage import db_backup


def _make_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('hello')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "metadata.db"
    monkeypatch.setattr(
        db_backup,
        "get_settings",
        lambda: types.SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db}"),
    )
    monkeypatch.setattr(db_backup, "is_s3", lambda: True)
    return db


def test_backup_local_mode_is_noop(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_backup, "is_s3", lambda: False)
    _make_sqlite(db_env)
    assert db_backup.backup_sync() is False


def test_backup_uploads_valid_snapshot(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_sqlite(db_env)
    captured: dict[str, Path] = {}

    def fake_upload(local_file: Path, key: str, s=None) -> None:
        dest = db_env.parent / "uploaded.db"
        shutil.copy(local_file, dest)
        captured["key"] = key
        captured["file"] = dest

    monkeypatch.setattr(db_backup.s3, "upload_object", fake_upload)
    assert db_backup.backup_sync() is True
    assert captured["key"].endswith("_system/metadata.db")
    # The uploaded snapshot is a readable SQLite carrying the source row.
    conn = sqlite3.connect(str(captured["file"]))
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
    finally:
        conn.close()


def test_restore_skips_when_local_present(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_sqlite(db_env)  # local DB already populated
    monkeypatch.setattr(db_backup.s3, "object_exists", lambda *a, **k: True)
    assert db_backup.restore_sync() is False  # never clobber a populated DB


def test_restore_downloads_when_local_absent(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not db_env.exists()
    snapshot = db_env.parent / "remote.db"
    _make_sqlite(snapshot)

    monkeypatch.setattr(db_backup.s3, "object_exists", lambda *a, **k: True)

    def fake_download(key: str, dest: Path, s=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(snapshot, dest)

    monkeypatch.setattr(db_backup.s3, "download_object", fake_download)
    assert db_backup.restore_sync() is True
    assert db_env.exists()


def test_restore_no_snapshot(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not db_env.exists()
    monkeypatch.setattr(db_backup.s3, "object_exists", lambda *a, **k: False)
    assert db_backup.restore_sync() is False
