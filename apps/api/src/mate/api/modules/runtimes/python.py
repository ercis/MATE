"""The default (Python) runtime - wraps the historical installer behaviour.

``materialize`` delegates verbatim to ``installer.install_module`` (uv venv +
uv pip, ``.installed-hash`` skip, in_process ABI gate vs subprocess
interpreter selection). ``launch_spec`` reproduces the worker argv the bridge
used to hardcode: the module venv's own interpreter running
``subprocess_worker.py`` by file path (not ``-m``), so the ``mate.api``
package chain is never imported under the module's venv Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mate.api.modules import subprocess_worker
from mate.api.modules.installer import install_module
from mate.api.modules.runtimes.base import ModuleRuntime, WorkerLaunchSpec
from mate.sdk.manifest import Manifest


class PythonRuntime(ModuleRuntime):
    key = "python"

    def available(self) -> tuple[bool, str]:
        # The platform itself is the toolchain (uv ships in the image / dev env).
        return True, f"platform CPython {sys.version.split()[0]}"

    async def materialize(
        self, folder: Path, manifest: Manifest, *, force: bool = False
    ) -> Path | None:
        return await install_module(folder, manifest, force=force)

    def launch_spec(self, folder: Path, manifest: Manifest) -> WorkerLaunchSpec:
        return WorkerLaunchSpec(
            argv=(str(_worker_python(folder)), str(Path(subprocess_worker.__file__))),
            env={"PYTHONUNBUFFERED": "1"},
        )


def _worker_python(folder: Path) -> Path:
    """Path to the module's venv python. For subprocess isolation the venv
    interpreter is used directly (it's fully isolated - the installer put the
    SDK and all deps in it natively)."""
    candidates = [folder / ".venv" / "bin" / "python3", folder / ".venv" / "bin" / "python"]
    for c in candidates:
        if c.exists():
            return c
    raise RuntimeError(
        f"No .venv/bin/python3 under {folder} - install must run before starting the subprocess."
    )
