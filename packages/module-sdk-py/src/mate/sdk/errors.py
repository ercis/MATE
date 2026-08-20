"""Public exception types raised at the SDK / loader boundary."""

from __future__ import annotations


class ModuleError(Exception):
    """Base class for SDK / loader errors."""


class ModuleManifestError(ModuleError):
    """Manifest parsing, validation, or dependency-graph error."""


class Cancelled(BaseException):
    """Raised inside a module handler when its job is cancelled cooperatively.

    Derives from :class:`BaseException` (not :class:`Exception`) - mirroring
    :class:`asyncio.CancelledError` - so a handler's broad ``except Exception:``
    cannot accidentally swallow a cooperative cancel and keep the job running.
    The platform translates this into a clean ``job.cancelled`` outcome.
    """
