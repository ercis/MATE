"""Cooperative soft-cancel surface shared by every job + module handler.

The runtime turns `JobHandle.progress()` into a free cancel poll point, the cancel
exceptions derive from `BaseException` so a broad `except Exception:` can't swallow
them, and the SDK exposes a no-op cancellation by default (backward compatible).
The subprocess kill+escalation path is covered in `test_subprocess_cancel.py`.
"""

from __future__ import annotations

import inspect
import time

import pytest


@pytest.mark.asyncio
async def test_progress_raises_when_cancel_token_set() -> None:
    """A set cancel token makes the very next progress tick raise `JobCancelled`
    before it even touches the bus - the zero-touch poll point every reporting
    handler (and every module calling `ctx.progress`) gets for free."""
    from mate.api.config import get_settings
    from mate.api.db.engine import get_sessionmaker
    from mate.api.events.bus import EventBus
    from mate.api.jobs.runtime import CancelToken, JobCancelled, JobHandle

    from .conftest import TEST_USER_ID

    token = CancelToken()
    token.cancel()
    handle = JobHandle(
        id="job-x",
        user_id=TEST_USER_ID,
        type="t",
        title="T",
        subtitle=None,
        module_id=None,
        payload={},
        sessionmaker=get_sessionmaker(),
        settings=get_settings(),
        bus=EventBus(),
        cancel_token=token,
        started_at=time.monotonic(),
    )
    with pytest.raises(JobCancelled):
        await handle.progress(1)


def test_cancel_exceptions_subclass_base_exception() -> None:
    """Both cancel exceptions derive from `BaseException` (like
    `asyncio.CancelledError`) so a handler's broad `except Exception:` cannot
    accidentally swallow a cooperative cancel and keep the job running."""
    from mate.api.jobs.runtime import JobCancelled
    from mate.sdk import Cancelled

    for exc in (JobCancelled, Cancelled):
        assert issubclass(exc, BaseException)
        assert not issubclass(exc, Exception)


def test_sdk_cancellation_surface_is_backward_compatible() -> None:
    """The SDK exposes a defaulted, no-op cancellation on `ModuleContext` (so
    existing modules construct unchanged) and bumped to 0.2.0."""
    import dataclasses

    import mate.sdk as sdk
    from mate.sdk.context import ModuleContext

    assert sdk.__version__ == "0.2.0"

    fields = {f.name: f for f in dataclasses.fields(ModuleContext)}
    assert "cancellation" in fields
    factory = fields["cancellation"].default_factory
    assert factory is not dataclasses.MISSING  # has a default → construction unaffected

    noop = factory()
    assert noop.is_cancelled() is False
    assert inspect.iscoroutinefunction(noop.check_cancelled)
