"""Bounded in-memory ring buffer of the most recent structlog lines.

The platform has **no on-disk log file** - ``main._configure_logging`` wires
structlog to render each event as JSON straight to stdout via the default
``PrintLoggerFactory``. To let the *Settings → About → Copy diagnostics* button
ship a recent-log tail without a logfile, the log config installs
``ring_buffer_renderer`` as structlog's *terminal* processor: it renders the
event to JSON, tees the line into a bounded deque, and returns the same string
so stdout output is byte-for-byte unchanged.

The buffer holds interleaved activity from **every** user, so the diagnostics
route only serves the tail to admins (``routes/system.diagnostics``).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

import structlog
from structlog.typing import EventDict, WrappedLogger

# ~500 lines is enough context for a support thread; the deque discards the
# oldest line on overflow so memory stays bounded regardless of process uptime.
LOG_RING_MAXLEN = 500
# Second guard applied when *serialising* the tail: a single event can carry a
# large payload, so the reader also caps the joined tail's total bytes.
LOG_TAIL_MAX_BYTES = 256 * 1024

_json_renderer = structlog.processors.JSONRenderer()
# Appended from any logging thread (structlog runs its processors inline on the
# caller's thread, incl. ``asyncio.to_thread`` workers) and snapshotted from the
# event loop - guard both with a lock so a concurrent append can't corrupt the
# read. ``deque(maxlen=...)`` drops the oldest entry on overflow; ``_appended``
# counts every line ever rendered so the reader can tell that older lines were
# silently discarded (``list(_ring)`` alone can never reveal a deque drop).
_lock = threading.Lock()
_ring: deque[str] = deque(maxlen=LOG_RING_MAXLEN)
_appended = 0


def ring_buffer_renderer(logger: WrappedLogger, name: str, event_dict: EventDict) -> str:
    """structlog terminal processor: render to JSON, tee into the ring, return it.

    Drop-in replacement for ``structlog.processors.JSONRenderer()`` - it renders
    with that same renderer, so stdout output is unchanged; the only side effect
    is capturing the line for the diagnostics tail.
    """
    global _appended
    rendered = _json_renderer(logger, name, event_dict)
    line = rendered.decode("utf-8", "replace") if isinstance(rendered, bytes) else rendered
    with _lock:
        _ring.append(line)
        _appended += 1
    return line


@dataclass(frozen=True)
class LogTail:
    """A serialisable snapshot of the buffered log tail, already bounded."""

    lines: list[str] = field(default_factory=list)
    byte_count: int = 0
    # True when older lines were dropped (deque overflow, line-cap, or byte-cap):
    # this is only a tail, not the full log since start.
    truncated: bool = False
    capacity: int = LOG_RING_MAXLEN


def recent_log_lines(
    *, max_lines: int = LOG_RING_MAXLEN, max_bytes: int = LOG_TAIL_MAX_BYTES
) -> LogTail:
    """Newest-last snapshot of the log tail, bounded by both line count and bytes.

    Trims from the **oldest** end so the most recent lines always survive both
    caps. The returned lists are private copies - safe to serialise on the event
    loop with no further locking.
    """
    with _lock:
        buffered = list(_ring)
        appended = _appended

    # Lines the deque itself discarded on overflow (never present in `buffered`).
    dropped_by_ring = appended - len(buffered)
    lines = buffered[-max_lines:]
    dropped_by_lines = len(buffered) - len(lines)

    kept: list[str] = []
    byte_count = 0
    dropped_by_bytes = False
    for line in reversed(lines):
        size = len(line.encode("utf-8", "replace")) + 1  # +1 for the joining newline
        if kept and byte_count + size > max_bytes:
            dropped_by_bytes = True
            break
        byte_count += size
        kept.append(line)
    kept.reverse()

    return LogTail(
        lines=kept,
        byte_count=byte_count,
        truncated=dropped_by_ring > 0 or dropped_by_lines > 0 or dropped_by_bytes,
        capacity=max_lines,
    )
