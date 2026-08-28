"""Runtime registry + JvmRuntime materialise/launch behaviour (no JRE needed -
the toolchain probe is monkeypatched; jar validation uses real zip files)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mate.api.modules.installer import ModuleInstallError
from mate.api.modules.runtimes import runtime_for
from mate.api.modules.runtimes.jvm import JvmRuntime, _jar_main_class
from mate.api.modules.runtimes.python import PythonRuntime
from mate.sdk.manifest import Manifest


def _py_manifest() -> Manifest:
    return Manifest.model_validate(
        {"id": "pymod", "name": "Py", "version": "1.0.0", "category": "other"}
    )


def _jvm_manifest(jar: str = "dist/mod.jar", requires: int = 17) -> Manifest:
    return Manifest.model_validate(
        {
            "id": "jvmmod",
            "name": "Jvm",
            "version": "1.0.0",
            "category": "other",
            "runtime": {"kind": "jvm", "jar": jar, "requires-java": requires},
        }
    )


def _write_jar(path: Path, main_class: str | None = "mate.Main") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = "Manifest-Version: 1.0\n"
    if main_class:
        manifest += f"Main-Class: {main_class}\n"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", manifest)
        zf.writestr("mate/Main.class", b"\xca\xfe\xba\xbe")


def test_runtime_for_selects_by_kind() -> None:
    assert isinstance(runtime_for(_py_manifest()), PythonRuntime)
    assert isinstance(runtime_for(_jvm_manifest()), JvmRuntime)


def test_python_launch_spec_uses_venv_worker(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    spec = PythonRuntime().launch_spec(tmp_path, _py_manifest())
    assert spec.argv[0] == str(venv_python)
    assert spec.argv[1].endswith("subprocess_worker.py")
    assert spec.env == {"PYTHONUNBUFFERED": "1"}


def test_python_launch_spec_requires_venv(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="install must run"):
        PythonRuntime().launch_spec(tmp_path, _py_manifest())


class _ProbedJvm(JvmRuntime):
    """JvmRuntime with a canned toolchain probe."""

    def __init__(self, ok: bool, feature: int = 21, detail: str = "openjdk 21") -> None:
        super().__init__()
        self._canned = (ok, detail if ok else "`java` not found on PATH", feature)

    def _probe(self) -> tuple[bool, str, int]:
        return self._canned


@pytest.mark.asyncio
async def test_jvm_materialize_missing_toolchain(tmp_path: Path) -> None:
    _write_jar(tmp_path / "dist" / "mod.jar")
    with pytest.raises(ModuleInstallError, match="no usable Java runtime"):
        await _ProbedJvm(ok=False).materialize(tmp_path, _jvm_manifest())


@pytest.mark.asyncio
async def test_jvm_materialize_version_floor(tmp_path: Path) -> None:
    _write_jar(tmp_path / "dist" / "mod.jar")
    with pytest.raises(ModuleInstallError, match="needs Java >= 21"):
        await _ProbedJvm(ok=True, feature=17).materialize(tmp_path, _jvm_manifest(requires=21))


@pytest.mark.asyncio
async def test_jvm_materialize_missing_jar(tmp_path: Path) -> None:
    with pytest.raises(ModuleInstallError, match="does not exist"):
        await _ProbedJvm(ok=True).materialize(tmp_path, _jvm_manifest())


@pytest.mark.asyncio
async def test_jvm_materialize_jar_without_main_class(tmp_path: Path) -> None:
    _write_jar(tmp_path / "dist" / "mod.jar", main_class=None)
    with pytest.raises(ModuleInstallError, match="no Main-Class"):
        await _ProbedJvm(ok=True).materialize(tmp_path, _jvm_manifest())


@pytest.mark.asyncio
async def test_jvm_materialize_writes_hash_and_skips(tmp_path: Path, monkeypatch) -> None:
    _write_jar(tmp_path / "dist" / "mod.jar")
    manifest = _jvm_manifest()
    runtime = _ProbedJvm(ok=True)
    assert await runtime.materialize(tmp_path, manifest) is None
    assert (tmp_path / ".installed-hash").read_text().strip() == manifest.dependencies_hash()

    # Second run must skip before ever probing the toolchain.
    def _explode() -> tuple[bool, str, int]:
        raise AssertionError("probe must not run on a hash-matched skip")

    runtime2 = _ProbedJvm(ok=True)
    monkeypatch.setattr(runtime2, "_probe", _explode)
    assert await runtime2.materialize(tmp_path, manifest) is None

    # `force=True` re-validates.
    assert await _ProbedJvm(ok=True).materialize(tmp_path, manifest, force=True) is None


@pytest.mark.asyncio
async def test_jvm_materialize_rechecks_when_jar_vanished(tmp_path: Path) -> None:
    _write_jar(tmp_path / "dist" / "mod.jar")
    manifest = _jvm_manifest()
    await _ProbedJvm(ok=True).materialize(tmp_path, manifest)
    (tmp_path / "dist" / "mod.jar").unlink()
    with pytest.raises(ModuleInstallError, match="does not exist"):
        await _ProbedJvm(ok=True).materialize(tmp_path, manifest)


def test_jvm_launch_spec(tmp_path: Path, monkeypatch) -> None:
    from mate.api import config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: config.Settings(java_bin="/custom/java"),
    )
    # jvm.py resolved get_settings at import time - patch its reference too.
    from mate.api.modules.runtimes import jvm as jvm_module

    monkeypatch.setattr(
        jvm_module, "get_settings", lambda: config.Settings(java_bin="/custom/java")
    )

    manifest = Manifest.model_validate(
        {
            "id": "jvmmod",
            "name": "Jvm",
            "version": "1.0.0",
            "category": "other",
            "runtime": {"kind": "jvm", "jar": "dist/mod.jar", "jvm-args": ["-Xmx256m"]},
        }
    )
    spec = JvmRuntime().launch_spec(tmp_path, manifest)
    assert spec.argv[0] == "/custom/java"
    assert "-Xmx256m" in spec.argv
    assert spec.argv[-2:] == ("-jar", str((tmp_path / "dist" / "mod.jar").resolve()))
    assert spec.cwd == tmp_path


def test_jar_main_class_honours_manifest_wrapping(tmp_path: Path) -> None:
    jar = tmp_path / "wrapped.jar"
    # 72-byte manifest line wrap: continuation lines start with a single space.
    wrapped = (
        "Manifest-Version: 1.0\r\n"
        "Main-Class: mate.averyveryveryveryveryveryverylongpackagename.submodule\r\n"
        " .Main\r\n"
    )
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", wrapped)
    assert (
        _jar_main_class(jar) == "mate.averyveryveryveryveryveryverylongpackagename.submodule.Main"
    )


def test_jar_main_class_absent(tmp_path: Path) -> None:
    jar = tmp_path / "plain.jar"
    _write_jar(jar, main_class=None)
    assert _jar_main_class(jar) is None
    assert _jar_main_class(tmp_path / "missing.jar") is None
