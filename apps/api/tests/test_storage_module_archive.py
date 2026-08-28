"""Uploaded-module archive → S3 + boot restore + GC (S3_OFFLOAD Phase 2.3/2.4).

S3 ops stubbed; archives are real tarballs round-tripped through a fake bucket dir.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from mate.api.modules.maintenance import gc_orphaned_uploaded_modules
from mate.api.storage import module_archive
from mate.api.storage.s3 import S3Object


def _make_module(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.yaml").write_text("id: demo\n")
    (folder / "module.py").write_text("# code\n")
    (folder / ".venv").mkdir()
    (folder / ".venv" / "marker").write_text("x")  # build artifact - must be excluded
    (folder / ".installed-hash").write_text("abc")  # build artifact - must be excluded


@pytest.fixture
def s3_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fake bucket backed by a local dir; module_archive's S3 calls hit it."""
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    monkeypatch.setattr(module_archive, "is_s3", lambda: True)

    def fake_upload(local_file: Path, key: str, s=None) -> None:
        dest = bucket / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(local_file).read_bytes())

    def fake_download(key: str, dest: Path, s=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((bucket / key).read_bytes())

    def fake_list(prefix: str, s=None) -> list[S3Object]:
        root = bucket / prefix
        out: list[S3Object] = []
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    out.append(
                        S3Object(
                            key=str(p.relative_to(bucket)),
                            size=p.stat().st_size,
                            etag=None,
                            last_modified=None,
                        )
                    )
        return out

    def fake_delete(key: str, s=None) -> None:
        (bucket / key).unlink(missing_ok=True)

    monkeypatch.setattr(module_archive.s3, "upload_object", fake_upload)
    monkeypatch.setattr(module_archive.s3, "download_object", fake_download)
    monkeypatch.setattr(module_archive.s3, "list_objects", fake_list)
    monkeypatch.setattr(module_archive.s3, "delete_object", fake_delete)
    return bucket


def test_archive_excludes_build_artifacts(s3_env: Path, tmp_path: Path) -> None:
    folder = tmp_path / "uploaded" / "demo"
    _make_module(folder)
    assert module_archive.archive_module_sync(folder, "demo") is True

    archive = s3_env / "_system" / "modules" / "demo.tar.gz"
    assert archive.exists()
    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
    assert "demo/manifest.yaml" in names
    assert "demo/module.py" in names
    assert not any(".venv" in n for n in names)
    assert not any(".installed-hash" in n for n in names)


def test_archive_local_mode_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module_archive, "is_s3", lambda: False)
    folder = tmp_path / "demo"
    _make_module(folder)
    assert module_archive.archive_module_sync(folder, "demo") is False


def test_restore_only_owned_missing_modules(s3_env: Path, tmp_path: Path) -> None:
    # Archive two modules, then restore into a fresh (empty) uploads dir.
    src = tmp_path / "src"
    for mid in ("demo", "other"):
        f = src / mid
        _make_module(f)
        module_archive.archive_module_sync(f, mid)

    uploaded = tmp_path / "uploaded"
    # Only 'demo' is owned → only it is restored; 'other' is left in the bucket.
    n = module_archive.restore_missing_modules_sync(uploaded, {"demo"})
    assert n == 1
    assert (uploaded / "demo" / "manifest.yaml").exists()
    assert not (uploaded / "other").exists()


def test_restore_skips_present_dir(s3_env: Path, tmp_path: Path) -> None:
    src = tmp_path / "src" / "demo"
    _make_module(src)
    module_archive.archive_module_sync(src, "demo")
    uploaded = tmp_path / "uploaded"
    (uploaded / "demo").mkdir(parents=True)  # already present locally
    assert module_archive.restore_missing_modules_sync(uploaded, {"demo"}) == 0


def test_delete_archive(s3_env: Path, tmp_path: Path) -> None:
    folder = tmp_path / "demo"
    _make_module(folder)
    module_archive.archive_module_sync(folder, "demo")
    assert (s3_env / "_system" / "modules" / "demo.tar.gz").exists()
    module_archive.delete_module_archive_sync("demo")
    assert not (s3_env / "_system" / "modules" / "demo.tar.gz").exists()


def test_gc_removes_only_unowned_dirs(tmp_path: Path) -> None:
    uploaded = tmp_path / "uploaded"
    for mid in ("keep", "orphan_a", "orphan_b"):
        (uploaded / mid).mkdir(parents=True)
    removed = gc_orphaned_uploaded_modules(uploaded, {"keep"})
    assert set(removed) == {"orphan_a", "orphan_b"}
    assert (uploaded / "keep").exists()
    assert not (uploaded / "orphan_a").exists()
    assert not (uploaded / "orphan_b").exists()


def test_gc_missing_dir_is_safe(tmp_path: Path) -> None:
    assert gc_orphaned_uploaded_modules(tmp_path / "nope", set()) == []
