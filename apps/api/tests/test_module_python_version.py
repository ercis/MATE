"""Per-module Python-version handling (installer pinning + validation) and the
subprocess job/event metadata round-trip.

These are pure unit tests - they mock `installer._run` so no real `uv` runs, and
exercise `SubprocessModule._install_stubs` directly so no worker is spawned. The
end-to-end subprocess path (real venv + worker + DataFrame materialise) is left
to a manual/Docker smoke test since it needs uv and a real event log.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from packaging.specifiers import InvalidSpecifier

from mate.api.modules import installer
from mate.api.modules.installer import ModuleInstallError, _host_satisfies, install_module
from mate.sdk.manifest import Manifest


def _manifest(**python: object) -> Manifest:
    return Manifest.model_validate(
        {
            "id": "testmod",
            "name": "Test",
            "version": "0.1.0",
            "category": "foundation",
            "dependencies": {"python": python},
        }
    )


def _fake_run_factory(calls: list[list[str]]):
    """Stand in for `installer._run`: record the argv and fake a venv layout so
    the installer's post-checks (`_venv_site_packages(...).exists()` etc.) pass.
    The faked pyvenv.cfg always reports the *host* version, matching how
    `uv venv --python <sys.executable>` would resolve in_process."""

    async def fake_run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python3").write_text("")
            mj, mn = sys.version_info.major, sys.version_info.minor
            (venv / "pyvenv.cfg").write_text(f"version_info = {mj}.{mn}.0\n")
            (venv / "lib" / f"python{mj}.{mn}" / "site-packages").mkdir(parents=True, exist_ok=True)
        return 0, ""

    return fake_run


def test_host_satisfies_relational() -> None:
    ok_none, host = _host_satisfies(None)
    assert ok_none is True
    assert host.startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    # A spec that includes the running interpreter passes; one that excludes it fails.
    assert _host_satisfies(f">={host}")[0] is True
    assert _host_satisfies(f"<{host}")[0] is False
    # Malformed specifier surfaces as InvalidSpecifier (caller maps to a clear error).
    with pytest.raises(InvalidSpecifier):
        _host_satisfies(">=not-a-version")


@pytest.mark.asyncio
async def test_install_in_process_pins_host_interpreter(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", _fake_run_factory(calls))
    m = _manifest(
        **{"requires-python": ">=3.12", "packages": ["leftpad"], "isolation": "in_process"}
    )

    site = await install_module(tmp_path, m)

    assert site is not None
    venv_cmd = next(c for c in calls if c[:2] == ["uv", "venv"])
    # Pinned to the exact running interpreter, NOT the open-ended spec.
    assert sys.executable in venv_cmd
    assert ">=3.12" not in venv_cmd
    # Synthesised pyproject pins requires-python to the host minor.
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert f'requires-python = "=={sys.version_info.major}.{sys.version_info.minor}.*"' in pyproject
    # Cache key is salted with the host tag so a different platform Python rebuilds.
    tag = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert (tmp_path / ".installed-hash").read_text().strip().endswith(f":{tag}")


@pytest.mark.asyncio
async def test_install_in_process_rejects_incompatible_requires_python(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", _fake_run_factory(calls))
    m = _manifest(**{"requires-python": ">=99.0", "packages": ["x"], "isolation": "in_process"})

    with pytest.raises(ModuleInstallError) as ei:
        await install_module(tmp_path, m)

    msg = str(ei.value)
    assert "isolation: subprocess" in msg
    assert "does not satisfy" in msg
    # Validation fails before any uv invocation.
    assert calls == []


@pytest.mark.asyncio
async def test_install_in_process_rejects_malformed_requires_python(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(installer, "_run", _fake_run_factory([]))
    m = _manifest(**{"requires-python": ">=abc", "packages": ["x"], "isolation": "in_process"})

    with pytest.raises(ModuleInstallError) as ei:
        await install_module(tmp_path, m)

    assert "malformed" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_install_subprocess_uses_spec_and_folds_inherit(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", _fake_run_factory(calls))
    m = _manifest(
        **{
            "requires-python": ">=3.13",
            "packages": ["foo"],
            "inherit": ["pandas"],
            "isolation": "subprocess",
        }
    )

    site = await install_module(tmp_path, m)

    assert site is not None
    venv_cmd = next(c for c in calls if c[:2] == ["uv", "venv"])
    # The requested spec selects the interpreter; we do NOT pin to the host.
    assert ">=3.13" in venv_cmd
    assert sys.executable not in venv_cmd
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.13"' in pyproject
    # `inherit` names are installed into the venv (no shared interpreter to inherit from).
    assert '"foo"' in pyproject
    assert '"pandas"' in pyproject
    # The SDK is installed into the worker venv so the worker can import
    # mate.sdk (+ its deps) under its own interpreter.
    assert any("module-sdk-py" in part for c in calls for part in c)
    # subprocess cache key is not host-salted.
    assert ":" not in (tmp_path / ".installed-hash").read_text().strip()


def test_subprocess_stubs_carry_job_event_route_metadata() -> None:
    """The v1 'Sperre' is gone: a SubprocessModule rebuilds @route/@job/@on_event
    decorator metadata on its stubs so the loader binds them like an in_process
    module."""
    from mate.api.modules.subprocess_host import SubprocessModule
    from mate.sdk.decorators import get_event_sub, get_job_spec, get_route_spec

    class _DummyBridge:
        async def call_handler(self, *args, **kwargs):
            return None

    handlers_meta = [
        {
            "attr": "analyze",
            "route": {"method": "POST", "path": "/run", "name": None},
            "job": {
                "progress": True,
                "priority": 2,
                "cancellable": False,
                "result_url": None,
                "title": "Run",
                "subtitle": None,
            },
        },
        {
            "attr": "on_import",
            "on_event": {"topic": "log.imported"},
            "job": {
                "progress": False,
                "priority": 0,
                "cancellable": True,
                "result_url": None,
                "title": None,
                "subtitle": None,
            },
        },
    ]

    mod = SubprocessModule("testmod", handlers_meta, _DummyBridge())  # type: ignore[arg-type]

    route = get_route_spec(type(mod).analyze)
    assert route is not None and route.method == "POST" and route.path == "/run"
    job = get_job_spec(type(mod).analyze)
    assert job is not None
    assert job.progress is True and job.priority == 2 and job.cancellable is False
    assert job.title == "Run"

    sub = get_event_sub(type(mod).on_import)
    assert sub is not None and sub.topic == "log.imported"
    # A None title means the author used a callable; the loader falls back to a
    # static label, so the JobSpec is still attached.
    event_job = get_job_spec(type(mod).on_import)
    assert event_job is not None and event_job.title is None
