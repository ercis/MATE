"""Cooperative shutdown flag for long-lived SSE streams.

Uvicorn drains open connections *before* the lifespan shutdown runs and never
sends `http.disconnect` to an in-flight streaming response, so a `while True`
SSE generator (`/events`, `/jobs/{id}/stream`, the AI token stream) blocks the
drain until `--timeout-graceful-shutdown`, gets force-cancelled, and dumps a
`CancelledError` ASGI traceback plus a 500 on that stream's access-log line.

Two independent ways to learn shutdown has begun, because neither covers every
launcher:

* **Signal observer** (`install_signal_observer`) - uvicorn installs its
  `handle_exit` through `signal.signal` (deliberately, "always use
  signal.signal, even if loop.add_signal_handler is available") *before* the
  lifespan runs, so the app can chain onto it at startup and flip the flag the
  instant SIGINT/SIGTERM lands. This is the path that covers **dev**: under
  `uvicorn --reload` the reloader restarts the serving child with SIGTERM, and
  every reload used to force-cancel the open `/events` stream.
* **Server probe** (`set_shutdown_probe`) - the programmatic runner (`cli.py` /
  the `mate-api` script) owns the `uvicorn.Server` and registers its
  `should_exit` flag. Covers a shutdown that begins without a signal.

SSE loops poll `is_shutting_down()` and bow out within one poll interval, so
the drain completes cleanly and no force-cancel happens.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Callable
from typing import Any

# The signals that mean "start shutting down" - the set uvicorn itself handles.
_HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def _never() -> bool:
    return False


_probe: Callable[[], bool] = _never
_signalled = False


def set_shutdown_probe(probe: Callable[[], bool]) -> None:
    """Register the process-wide shutdown probe (called once by the runner)."""
    global _probe
    _probe = probe


def mark_shutting_down() -> None:
    """Flip the flag directly (signal observer, lifespan teardown, tests)."""
    global _signalled
    _signalled = True


def is_shutting_down() -> bool:
    """True once the server has begun graceful shutdown; drives SSE self-close."""
    return _signalled or _probe()


def install_signal_observer() -> Callable[[], None]:
    """Chain a flag-setter onto the SIGINT/SIGTERM handlers already installed.

    Called from the lifespan startup, i.e. *after* uvicorn's `capture_signals`
    put `Server.handle_exit` in place - so the previous handler is uvicorn's and
    we must delegate to it, or the server would never actually exit.

    Returns a callable restoring the previous handlers. Signals can only be
    touched from the main thread; off it (a test harness driving the lifespan on
    a worker thread) this is a no-op and the flag stays probe-driven.
    """
    global _signalled
    _signalled = False  # fresh process / app start - never inherit a stale flag

    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous: dict[signal.Signals, Any] = {}
    for sig in _HANDLED_SIGNALS:
        try:
            prior = signal.getsignal(sig)
        except (ValueError, OSError):  # pragma: no cover - platform quirk
            continue

        def _observer(signum: int, frame: Any, _prior: Any = prior) -> None:
            mark_shutting_down()
            # Delegate so uvicorn still sees the signal and exits. SIG_DFL /
            # SIG_IGN / None aren't callable - nothing to chain to.
            if callable(_prior):
                _prior(signum, frame)

        try:
            signal.signal(sig, _observer)
        except (ValueError, OSError):  # pragma: no cover - platform quirk
            continue
        previous[sig] = prior

    def _restore() -> None:
        for sig, prior in previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):  # pragma: no cover
                signal.signal(sig, prior)

    return _restore
