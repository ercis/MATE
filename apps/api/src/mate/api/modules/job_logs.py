"""Per-job module-log ring buffer.

A module's ``ctx.logger`` calls - both in-process modules and subprocess ones
(both funnel through the loader's ``_BusForwardingLogger``) - are mirrored here,
keyed by job id, so the admin Jobs tab can show what a still-running (or wedged)
precompute is actually doing. The bus ``module.log.*`` events are fire-and-forget
(dropped when nobody's subscribed, and silently skipped from a worker thread
where there's no running loop), so they can't power an "open the row and see the
backlog" view; this bounded buffer can.

Bounded on both axes - the last N lines per job, the last M jobs (LRU) - so a
long-lived deployment can't grow it without limit. In-memory only: it dies with
the process, like the asyncio job queue itself. Admin-only by convention (the
read endpoint is gated); the per-tenant ``user_id`` isolation that governs the
bus does not apply to this deliberately cross-user operator surface.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any


def _json_safe(value: Any) -> Any:
    """Coerce a log field into something the REST layer can always serialise.

    Module authors pass arbitrary kwargs (`ctx.logger.info("x", df=frame)`); keep
    JSON primitives as-is, recurse into lists/dicts, and stringify anything else
    so one exotic field can never 500 the admin logs endpoint.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


@dataclass(frozen=True)
class JobLogLine:
    ts: float
    level: str
    event: str
    fields: dict[str, Any]


class JobLogBuffer:
    """Thread-safe, bounded per-job log store.

    `append` is called from the event loop *and* from module worker threads
    (in-process modules log from inside `asyncio.to_thread`), so every mutation
    holds a lock. `get` returns a snapshot copy so the caller can iterate without
    holding it.
    """

    def __init__(self, max_jobs: int = 256, max_lines_per_job: int = 500) -> None:
        self._jobs: OrderedDict[str, deque[JobLogLine]] = OrderedDict()
        # Jobs whose oldest lines were evicted (per-job cap hit) - surfaced so the
        # UI can show a "earlier lines dropped" hint instead of implying the start.
        self._dropped: set[str] = set()
        self._max_jobs = max_jobs
        self._max_lines = max_lines_per_job
        self._lock = threading.Lock()

    def append(self, job_id: str, level: str, event: str, fields: dict[str, Any]) -> None:
        line = JobLogLine(
            ts=time.time(),
            level=level,
            event=event,
            fields={str(k): _json_safe(v) for k, v in fields.items()},
        )
        with self._lock:
            dq = self._jobs.get(job_id)
            if dq is None:
                dq = deque(maxlen=self._max_lines)
                self._jobs[job_id] = dq
                while len(self._jobs) > self._max_jobs:
                    old, _ = self._jobs.popitem(last=False)
                    self._dropped.discard(old)
            else:
                self._jobs.move_to_end(job_id)
            if len(dq) == self._max_lines:
                self._dropped.add(job_id)  # this append will evict the oldest line
            dq.append(line)

    def get(self, job_id: str, *, limit: int = 500) -> list[JobLogLine]:
        with self._lock:
            dq = self._jobs.get(job_id)
            lines = list(dq) if dq is not None else []
        return lines[-limit:] if limit and len(lines) > limit else lines

    def truncated(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._dropped


_buffer: JobLogBuffer | None = None


def get_job_log_buffer() -> JobLogBuffer:
    global _buffer
    if _buffer is None:
        _buffer = JobLogBuffer()
    return _buffer
