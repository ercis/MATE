"""Public SDK surface for Mate module authors.

A module author writes:

    from mate.sdk import Module, ModuleContext, on_event, route, job

    class MyModule(Module):
        id = "my_module"

        @route.get("/things")
        async def list_things(self, ctx: ModuleContext): ...

        @on_event("log.imported")
        async def on_log(self, ctx: ModuleContext, payload): ...

        @route.post("/heavy")
        @job(progress=True, title="Heavy compute")
        async def heavy(self, ctx: ModuleContext): ...

The decorators only attach metadata; the platform's module loader (in
``mate.api.modules``) reads it at startup and binds the right
machinery - there is no SDK-side runtime.
"""

from mate.sdk.context import (
    CancellationProtocol,
    EventBusProtocol,
    EventLogAccessProtocol,
    ModuleConfigProtocol,
    ModuleContext,
    ModuleRegistryProtocol,
    OpenEventLogProtocol,
    ProgressReporterProtocol,
    ResultCacheProtocol,
)
from mate.sdk.decorators import job, on_event, route
from mate.sdk.errors import Cancelled, ModuleError, ModuleManifestError
from mate.sdk.manifest import (
    DependenciesPython,
    EventLogRequirements,
    Manifest,
    ManifestFrontend,
    ModuleCategory,
    OptionalModuleDep,
    Requirements,
)
from mate.sdk.module import Module

__version__ = "0.2.0"

__all__ = [
    "CancellationProtocol",
    "Cancelled",
    "DependenciesPython",
    "EventBusProtocol",
    "EventLogAccessProtocol",
    "EventLogRequirements",
    "Manifest",
    "ManifestFrontend",
    "Module",
    "ModuleCategory",
    "ModuleConfigProtocol",
    "ModuleContext",
    "ModuleError",
    "ModuleManifestError",
    "ModuleRegistryProtocol",
    "OpenEventLogProtocol",
    "OptionalModuleDep",
    "ProgressReporterProtocol",
    "Requirements",
    "ResultCacheProtocol",
    "job",
    "on_event",
    "route",
]
