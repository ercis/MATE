"""`Module` base class - the only thing a module's `module.py` must subclass."""

from __future__ import annotations

from typing import ClassVar

from mate.sdk.errors import ModuleError


class Module:
    """Base class for module implementations.

    A subclass must declare ``id`` matching the manifest ``id``. Methods
    decorated with `@route.*`, `@on_event`, or `@job` are bound by the
    platform loader at startup - there is no manual registration.

    Authors should not instantiate Module subclasses themselves; the loader
    does that exactly once per module.
    """

    id: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.id:
            raise ModuleError(
                f"{cls.__name__} must declare an `id` class attribute matching its manifest id."
            )
        if not cls.id.replace("_", "").isalnum() or not cls.id.islower():
            raise ModuleError(
                f"Module id {cls.id!r} on {cls.__name__} must be lowercase snake_case."
            )
