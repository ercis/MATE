"""Strong references for fire-and-forget asyncio tasks.

`asyncio.create_task()` only keeps a *weak* reference to the task it returns, so
a task nobody awaits can be garbage-collected mid-flight and simply never
finish. That is exactly the failure mode behind dropped `module.log.*` bus
events and lost bridge notifications: the coroutine is scheduled, the caller
returns, the last reference dies, and the work silently disappears under load.

`spawn()` parks the task in a module-level set and clears it on completion, so
the reference lives exactly as long as the task does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

# Cleared by the done-callback below - membership is bounded by the number of
# in-flight fire-and-forget tasks, not by how many have ever been spawned.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
    """Schedule *coro* and hold a strong reference until it completes."""
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task
