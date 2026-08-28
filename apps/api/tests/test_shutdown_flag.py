"""The cooperative shutdown flag that lets SSE streams self-close.

Uvicorn drains open connections *before* the lifespan teardown and never sends
`http.disconnect` to a streaming response, so a `while True` SSE generator has
to learn about shutdown some other way or it blocks the drain until the grace
timeout, gets force-cancelled, and dumps a CancelledError ASGI traceback (plus
a 500 on the stream) - which is what happened on every `uvicorn --reload`
restart in dev, where no `should_exit` probe is registered.
"""

from __future__ import annotations

import asyncio
import signal
import threading

import pytest

from mate.api import shutdown as sd
from mate.api.modules.subprocess_worker import WireConnection


@pytest.fixture(autouse=True)
def _isolate_flag_state():
    """Isolate the module-global flag/probe from the rest of the suite.

    The flag is deliberately sticky - one process, one shutdown - and every
    other test's app lifespan sets it on teardown, so it must be cleared going
    IN as well as restored coming out, or these assertions depend on test order.
    """
    original = (sd._probe, sd._signalled)
    prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    sd._probe, sd._signalled = sd._never, False
    yield
    sd._probe, sd._signalled = original
    for sig, handler in prior.items():
        signal.signal(sig, handler)


def test_probe_drives_the_flag() -> None:
    """The programmatic runner's path: `server.should_exit` polled directly."""
    exiting = False
    sd.set_shutdown_probe(lambda: exiting)
    assert sd.is_shutting_down() is False
    exiting = True
    assert sd.is_shutting_down() is True


def test_signal_observer_flips_flag_and_delegates() -> None:
    """The dev path: chain onto uvicorn's handler instead of replacing it."""
    seen: list[int] = []
    signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))

    restore = sd.install_signal_observer()
    assert sd.is_shutting_down() is False

    # Invoke the installed handler directly - delivering a real SIGTERM would
    # take the test process down with it.
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)
    handler(signal.SIGTERM, None)

    assert sd.is_shutting_down() is True
    # Uvicorn's own handler MUST still run, or the server never exits.
    assert seen == [signal.SIGTERM]

    restore()
    assert signal.getsignal(signal.SIGTERM) is not handler


def test_install_clears_a_stale_flag() -> None:
    """A fresh app start never inherits a previous run's flag (matters in tests
    and under the reloader, where one process hosts many app lifecycles)."""
    sd.mark_shutting_down()
    assert sd.is_shutting_down() is True
    sd.install_signal_observer()()
    assert sd.is_shutting_down() is False


def test_install_off_main_thread_is_a_noop() -> None:
    """`signal.signal` raises off the main thread - degrade, don't explode."""
    result: list[object] = []

    def _run() -> None:
        try:
            result.append(sd.install_signal_observer())
        except Exception as exc:  # pragma: no cover - the regression this guards
            result.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()

    assert callable(result[0])
    result[0]()  # type: ignore[operator]


@pytest.mark.asyncio
async def test_app_lifespan_marks_shutdown() -> None:
    """End to end: the flag is set by the time the lifespan teardown finishes,
    even without a signal (programmatic stop / test harness)."""
    from mate.api.main import app

    async with app.router.lifespan_context(app):
        assert sd.is_shutting_down() is False
    assert sd.is_shutting_down() is True


@pytest.mark.asyncio
async def test_fail_all_pending_consumes_the_exception() -> None:
    """A worker dying during shutdown must not leave an unretrieved future.

    The awaiting task is usually already cancelled by then, so nobody reads the
    exception and asyncio logs a bare "Future exception was never retrieved" at
    GC - the noise seen on every reload alongside the SSE traceback.
    """
    reader = asyncio.StreamReader()
    writer = object()  # never touched by fail_all_pending
    conn = WireConnection(reader, writer)  # type: ignore[arg-type]

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    conn._pending[1] = fut

    conn.fail_all_pending(RuntimeError("Worker for 'x' exited."))

    assert fut.done()
    # asyncio's own "was it retrieved?" bookkeeping - False means it will not be
    # reported at GC. A task still awaiting the future gets the exception raised.
    assert getattr(fut, "_log_traceback", False) is False
    with pytest.raises(RuntimeError):
        fut.result()
