"""Registry of module runtimes, keyed by the manifest's `runtime.kind`."""

from __future__ import annotations

from mate.api.modules.installer import ModuleInstallError
from mate.api.modules.runtimes.base import ModuleRuntime, WorkerLaunchSpec
from mate.api.modules.runtimes.jvm import JvmRuntime
from mate.api.modules.runtimes.python import PythonRuntime
from mate.sdk.manifest import Manifest

__all__ = ["ModuleRuntime", "WorkerLaunchSpec", "runtime_for"]

_RUNTIMES: dict[str, ModuleRuntime] = {
    PythonRuntime.key: PythonRuntime(),
    JvmRuntime.key: JvmRuntime(),
}


def runtime_for(manifest: Manifest) -> ModuleRuntime:
    """The runtime implementation for *manifest*. Unreachable for kinds the
    manifest schema doesn't accept (its `Literal` union is the gate), but kept
    defensive so a schema/registry drift fails with a clear message."""
    runtime = _RUNTIMES.get(manifest.runtime.kind)
    if runtime is None:  # pragma: no cover - schema and registry in lockstep
        raise ModuleInstallError(
            f"Module {manifest.id!r} declares runtime {manifest.runtime.kind!r}, which this "
            "platform build has no implementation for."
        )
    return runtime
