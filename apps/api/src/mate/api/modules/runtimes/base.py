"""Runtime abstraction - how a module's toolchain is materialised and how its
worker process is launched.

A *runtime* owns the two language-specific seams of the module system:

  - ``materialize``: install/verify the module's toolchain artefacts
    (Python: ``uv venv`` + deps; JVM: validate the fat jar against the local
    JRE). Idempotent across boots, keyed on ``.installed-hash`` exactly like
    the Python installer always was.
  - ``launch_spec``: the argv/env/cwd used to spawn the module's worker
    process. The ``SubprocessBridge`` appends the two positional protocol
    arguments (socket path, module folder) - see ``modules/PROTOCOL.md``.

Everything downstream of the worker socket is runtime-agnostic: the wire
protocol, the ``SubprocessModule`` shim, route/job/event mounting, and every
``ctx.*`` service speak JSON + Parquet, never language objects.

``launch_spec`` is only ever called for bridge-mounted modules (all
non-Python runtimes, plus Python modules with ``isolation: subprocess`` -
the manifest normalises the former onto the latter).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from mate.sdk.manifest import Manifest


@dataclass(frozen=True)
class WorkerLaunchSpec:
    """How to exec one module's worker process.

    ``argv`` is the command prefix (interpreter/binary + fixed flags); the
    bridge appends ``[socket_path, module_folder]``. ``env`` is merged over
    ``os.environ``. ``cwd`` of ``None`` inherits the API's working directory.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None


class ModuleRuntime(ABC):
    """One language runtime the platform can execute modules on."""

    key: ClassVar[str]

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """Whether this runtime's toolchain exists on this host.

        Returns ``(ok, detail)`` where *detail* is a human-readable version
        string on success or the reason on failure. Cached where probing is
        expensive - callers may invoke this freely.
        """

    @abstractmethod
    async def materialize(
        self, folder: Path, manifest: Manifest, *, force: bool = False
    ) -> Path | None:
        """Install/verify the module's toolchain artefacts. Idempotent
        (``.installed-hash`` skip). Returns the venv site-packages path for
        Python modules (the import finder needs it) or ``None`` for runtimes
        without a Python import surface. Raises ``ModuleInstallError`` for
        anything that would produce a broken worker - the boot path catches
        and skips, the upload install job fails loud with the message.
        """

    @abstractmethod
    def launch_spec(self, folder: Path, manifest: Manifest) -> WorkerLaunchSpec:
        """Argv/env/cwd for the module's worker process. Only called for
        bridge-mounted modules, after a successful ``materialize``."""
