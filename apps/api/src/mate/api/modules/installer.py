"""Materialise per-module Python deps via `uv venv` + `uv pip install` (§5.4).

For each discovered module:

  - If the manifest's `dependencies.python.packages` is empty *and* the
    module folder doesn't already contain a `pyproject.toml`, we skip - the
    module imports nothing beyond stdlib + inherits + SDK.
  - Otherwise we synthesise a minimal `pyproject.toml` (when the author
    didn't supply one), create an isolated venv, and install deps into it.
  - A content-hash of the dependencies block is cached at
    `modules/<folder>/.installed-hash`. Re-runs skip when the hash matches.

We use `uv venv` + `uv pip install` rather than `uv sync` to avoid writing
a lock file, which fails on macOS Docker Desktop bind mounts due to atomic-
write restrictions on the VirtioFS layer.

When `manifest.dependencies.python.isolation == "subprocess"` the loader
spawns a worker from the module's own venv via
`mate.api.modules.subprocess_host.SubprocessBridge` (§5.4). The
installer itself doesn't care about the mode - it just guarantees the
venv exists; choosing in-process vs. subprocess is the loader's call.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)


class ModuleInstallError(RuntimeError):
    """Raised when a module's venv cannot be materialised in a way that will
    actually work - e.g. an in_process module whose ``requires-python``
    excludes the platform interpreter. The loader catches this and skips the
    module so it never gets imported in-process (an ABI mismatch would crash
    the host)."""


def _hash_path(folder: Path) -> Path:
    return folder / ".installed-hash"


def _venv_python_version(folder: Path) -> tuple[int, int] | None:
    """(major, minor) of the venv's interpreter, read from pyvenv.cfg, or None
    when there's no venv yet."""
    cfg = folder / ".venv" / "pyvenv.cfg"
    if not cfg.exists():
        return None
    for line in cfg.read_text().splitlines():
        key, _, val = line.partition("=")
        if key.strip() == "version_info":
            parts = val.strip().split(".")
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    return None


def _venv_site_packages(folder: Path) -> Path:
    """Return the site-packages path for the venv in *folder*.

    Uses the venv's own Python version (from pyvenv.cfg) so a subprocess venv
    on a different Python than the platform gets the correct path; falls back
    to the platform version when there's no venv yet.
    """
    ver = _venv_python_version(folder)
    major, minor = ver if ver is not None else (sys.version_info.major, sys.version_info.minor)
    return folder / ".venv" / "lib" / f"python{major}.{minor}" / "site-packages"


# Public alias: the loader needs a module's site-packages to build the
# `ctx.run_in_process` offload metadata (§8.3).
venv_site_packages = _venv_site_packages


def _host_satisfies(requires_python: str | None) -> tuple[bool, str]:
    """Whether the running interpreter satisfies *requires_python*.

    Returns ``(ok, host_version)``. An empty/None spec is always satisfied. A
    malformed spec raises ``InvalidSpecifier`` (the caller turns it into a
    clear manifest error). The full ``major.minor.micro`` is used so
    ``>=3.12.4``-style specs evaluate correctly, and prereleases are allowed so
    an RC host isn't spuriously rejected.
    """
    host = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if not requires_python:
        return True, host
    spec = SpecifierSet(requires_python)
    return spec.contains(Version(host), prereleases=True), host


def _sdk_project_root() -> Path:
    """Directory of the `mate-sdk` package (the one with its `pyproject.toml`) -
    installed into subprocess venvs so the worker can import the SDK + its deps
    under its own interpreter."""
    import mate.sdk

    # .../packages/module-sdk-py/src/mate/sdk/__init__.py -> packages/module-sdk-py
    return Path(mate.sdk.__file__).resolve().parents[3]


def _synthesise_pyproject(
    folder: Path, manifest: Manifest, *, requires_python: str, packages: list[str]
) -> None:
    """Create a minimal pyproject if the author didn't supply one.

    *requires_python* and *packages* are resolved per isolation mode by the
    caller (in_process pins to the host interpreter and installs only the
    private packages; subprocess keeps the manifest's spec and folds
    ``inherit`` names into the deps since there's no shared interpreter to
    inherit from across a process boundary).
    """
    target = folder / "pyproject.toml"
    if target.exists():
        return
    deps = "\n".join(f'    "{p}",' for p in packages)
    content = f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ff-mod-{manifest.id.replace("_", "-")}"
version = "{manifest.version}"
requires-python = "{requires_python}"
dependencies = [
{deps}
]

[tool.hatch.build.targets.wheel]
bypass-selection = true
"""
    target.write_text(content)


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode("utf-8", errors="replace") if out else ""
    return proc.returncode or 0, text


async def install_module(folder: Path, manifest: Manifest, *, force: bool = False) -> Path | None:
    """Create an isolated venv and install module deps if needed.

    Returns the venv site-packages path, or None when no Python deps are
    declared or installation failed.
    """
    py = manifest.dependencies.python
    is_subprocess = py.isolation == "subprocess"
    # in_process with no private deps just uses the platform's packages via the
    # import finder - no venv needed. A subprocess module ALWAYS needs a venv
    # (at minimum the SDK, to run its worker in its own interpreter).
    if not is_subprocess and not py.packages and not (folder / "pyproject.toml").exists():
        return None

    # Resolve the interpreter + synthesized metadata per isolation mode.
    # in_process modules are imported into THIS interpreter, so their venv must
    # be ABI-identical to it - pin to sys.executable and treat requires-python
    # as a validation gate. subprocess modules run in their own process, so
    # requires-python selects their (possibly different) interpreter and the
    # `inherit` names must be installed into the venv (no shared interpreter to
    # inherit from across a process boundary).
    host_tag = f"{sys.version_info.major}.{sys.version_info.minor}"
    if py.isolation == "in_process":
        try:
            ok, host = _host_satisfies(py.requires_python)
        except InvalidSpecifier as exc:
            raise ModuleInstallError(
                f"Module {manifest.id!r} has a malformed "
                f"dependencies.python.requires-python ({py.requires_python!r}): {exc}. "
                "Fix the specifier in manifest.yaml."
            ) from exc
        if not ok:
            raise ModuleInstallError(
                f"Module {manifest.id!r} declares requires-python {py.requires_python!r} but "
                f"the platform runs Python {host}, which does not satisfy it. in_process "
                "modules share the platform interpreter and cannot run on a different Python. "
                f"Either relax requires-python to include {host} (e.g. drop the upper bound), "
                "or set dependencies.python.isolation: subprocess to run on your own interpreter."
            )
        python_arg = sys.executable
        synth_requires = f"=={sys.version_info.major}.{sys.version_info.minor}.*"
        synth_packages = list(py.packages)
    else:  # subprocess
        python_arg = py.requires_python or ">=3.12"
        synth_requires = py.requires_python or ">=3.12"
        synth_packages = list(py.packages) + list(py.inherit)

    expected = manifest.dependencies_hash()
    if py.isolation == "in_process":
        # Fold the host interpreter into the cache key so a venv built under a
        # different platform Python (e.g. a host-mode 3.13 venv bind-mounted
        # into a 3.12 container) rebuilds instead of being wrongly reused.
        expected = f"{expected}:{host_tag}"
    hash_file = _hash_path(folder)
    venv_dir = folder / ".venv"
    if not force and hash_file.exists() and hash_file.read_text().strip() == expected:
        site = _venv_site_packages(folder)
        venv_python = venv_dir / "bin" / "python3"
        # Belt-and-braces: an in_process venv must match the running Python.
        version_ok = py.isolation != "in_process" or _venv_python_version(folder) == (
            sys.version_info.major,
            sys.version_info.minor,
        )
        # A subprocess venv must also actually carry the SDK: an interrupted
        # prior build can leave the hash + a venv missing `mate.sdk`, which makes
        # the worker crash on import and never signal ready (the loader then
        # skips the module). Rebuild instead of skipping into a broken worker.
        sdk_ok = not is_subprocess or (site / "mate" / "sdk").exists()
        if site.exists() and venv_python.exists() and version_ok and sdk_ok:
            log.debug("modules.installer.skip_unchanged", module_id=manifest.id)
            return site

    _synthesise_pyproject(folder, manifest, requires_python=synth_requires, packages=synth_packages)

    # Wipe a stale venv so `uv venv` starts clean (broken symlinks from a
    # previous container won't confuse it).
    if venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)

    log.info("modules.installer.start", module_id=manifest.id, packages=synth_packages)

    # Step 1 - create the venv. in_process pins to the host interpreter path;
    # subprocess passes the requires-python spec so uv picks/auto-downloads it.
    # `--allow-existing` so a stray file lingering in the target (e.g. macOS
    # Finder/Spotlight re-creating `.DS_Store` inside a bind-mounted module
    # dir between the rmtree above and this call) doesn't make `uv venv` bail
    # with "directory exists, but it's not a virtual environment".
    rc, out = await _run(["uv", "venv", str(venv_dir), "--python", python_arg, "--allow-existing"])
    if rc != 0:
        log.error("modules.installer.venv_failed", module_id=manifest.id, output=out)
        return None

    # Step 2 - install the project (and all its declared deps) into the venv.
    # `uv pip install <dir>` reads pyproject.toml and installs dependencies
    # without creating or requiring a lock file.
    rc, out = await _run(["uv", "pip", "install", "--python", str(venv_dir), str(folder)])
    if rc != 0:
        log.error("modules.installer.failed", module_id=manifest.id, output=out)
        return None

    if is_subprocess:
        # The worker runs in its own interpreter and imports `mate.sdk` (+ its
        # deps pydantic/pyyaml/structlog), which it can't borrow from the
        # platform across the process/ABI boundary - install them natively into
        # the worker venv (the SDK requires-python is >=3.12, so it installs on
        # any selected interpreter).
        rc, out = await _run(
            ["uv", "pip", "install", "--python", str(venv_dir), str(_sdk_project_root())]
        )
        if rc != 0:
            log.error("modules.installer.sdk_install_failed", module_id=manifest.id, output=out)
            return None

    hash_file.write_text(expected)
    site = _venv_site_packages(folder)
    if not site.exists():
        log.warning(
            "modules.installer.site_packages_missing",
            module_id=manifest.id,
            expected=str(site),
        )
        return None
    log.info("modules.installer.complete", module_id=manifest.id, site=str(site))
    return site


def remove_module_artifacts(folder: Path) -> None:
    """Wipe `.venv/`, `.dist/`, `.installed-hash`, `node_modules/`. The
    manifest and `module.py` are kept - only build artefacts go.
    """
    for name in (".venv", ".dist", "node_modules", ".installed-hash"):
        target = folder / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
