"""Per-job module-log ring buffer (`mate.api.modules.job_logs`).

The buffer backs the admin Jobs tab's per-job "Module logs" panel: every
`ctx.logger` line (in-process and subprocess modules both funnel through the
loader's bus-forwarding logger) is mirrored here, keyed by job id. It's bounded
on both axes so a long-lived process can't grow it without limit.
"""

from __future__ import annotations

from mate.api.modules.job_logs import JobLogBuffer, get_job_log_buffer


def test_append_and_get_preserve_order() -> None:
    buf = JobLogBuffer()
    buf.append("job-1", "info", "first", {"i": 1})
    buf.append("job-1", "warning", "second", {"i": 2})

    lines = buf.get("job-1")
    assert [(ln.level, ln.event, ln.fields) for ln in lines] == [
        ("info", "first", {"i": 1}),
        ("warning", "second", {"i": 2}),
    ]
    # Jobs are isolated; an unknown id is empty, not an error.
    assert buf.get("nope") == []
    assert buf.truncated("job-1") is False


def test_per_job_cap_evicts_oldest_and_flags_truncated() -> None:
    buf = JobLogBuffer(max_lines_per_job=3)
    for n in range(5):
        buf.append("job-1", "info", f"e{n}", {})

    events = [ln.event for ln in buf.get("job-1")]
    assert events == ["e2", "e3", "e4"]  # oldest two dropped by the deque cap
    assert buf.truncated("job-1") is True


def test_get_limit_returns_most_recent() -> None:
    buf = JobLogBuffer(max_lines_per_job=100)
    for n in range(10):
        buf.append("job-1", "info", f"e{n}", {})
    assert [ln.event for ln in buf.get("job-1", limit=3)] == ["e7", "e8", "e9"]


def test_job_lru_eviction_drops_oldest_job() -> None:
    buf = JobLogBuffer(max_jobs=2)
    buf.append("a", "info", "x", {})
    buf.append("b", "info", "x", {})
    buf.append("c", "info", "x", {})  # over the 2-job cap → "a" evicted

    assert buf.get("a") == []
    assert [ln.event for ln in buf.get("b")] == ["x"]
    assert [ln.event for ln in buf.get("c")] == ["x"]


def test_touching_a_job_keeps_it_warm() -> None:
    # Appending to an existing job moves it to the MRU end, so a steadily-logging
    # job isn't evicted by newer but quieter ones.
    buf = JobLogBuffer(max_jobs=2)
    buf.append("a", "info", "x", {})
    buf.append("b", "info", "x", {})
    buf.append("a", "info", "y", {})  # refresh "a"
    buf.append("c", "info", "x", {})  # evicts the now-oldest, "b"

    assert buf.get("b") == []
    assert [ln.event for ln in buf.get("a")] == ["x", "y"]


def test_fields_are_coerced_json_safe() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-obj"

    buf = JobLogBuffer()
    buf.append("job-1", "info", "e", {"obj": Weird(), "nested": {"k": Weird()}, "ok": 3})

    fields = buf.get("job-1")[0].fields
    assert fields == {"obj": "weird-obj", "nested": {"k": "weird-obj"}, "ok": 3}


def test_get_job_log_buffer_is_singleton() -> None:
    assert get_job_log_buffer() is get_job_log_buffer()
