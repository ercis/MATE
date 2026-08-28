"""Auto-respawn of crashed workers (modules/PROTOCOL.md §8).

A spontaneous worker exit used to strand the module until the next hard
cancel; the bridge now respawns on EOF with exponential backoff, a stable-
uptime attempt reset, and a terminal failed state after the attempt cap.
All spawns are faked - no venv, no real worker."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from mate.api.modules.runtimes.base import WorkerLaunchSpec
from mate.api.modules.subprocess_host import SubprocessBridge, SubprocessHostError
from mate.sdk.manifest import Manifest


def _manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "id": "respawntest",
            "name": "R",
            "version": "1.0.0",
            "category": "other",
            "dependencies": {"python": {"isolation": "subprocess"}},
        }
    )


@pytest.fixture
def bridge(tmp_path: Path) -> SubprocessBridge:
    b = SubprocessBridge(_manifest(), tmp_path, WorkerLaunchSpec(argv=(sys.executable,)))
    # Shrink the ladder so tests run in milliseconds.
    b._respawn_backoff_cap = 0.01
    b._respawn_max_attempts = 3
    return b


def _fake_spawn(bridge: SubprocessBridge, spawned: list[int]):
    async def spawn() -> None:
        spawned.append(1)
        bridge._ready_evt.set()  # stand-in for the worker's `ready`
        bridge._ready_error = None
        bridge._last_ready_monotonic = asyncio.get_running_loop().time()

    return spawn


async def _wait_respawn_task(bridge: SubprocessBridge) -> None:
    task = bridge._respawn_task
    if task is not None:
        await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_eof_triggers_respawn_with_attempt_count(bridge, monkeypatch) -> None:
    spawned: list[int] = []
    monkeypatch.setattr(bridge, "_spawn_worker", _fake_spawn(bridge, spawned))

    # Simulate a live worker that just died (the `_on_connect` EOF tail).
    bridge._ready_evt.clear()
    bridge._schedule_respawn()
    await _wait_respawn_task(bridge)

    assert spawned == [1]
    assert bridge._ready_evt.is_set()
    assert bridge._respawn_attempts == 1
    assert bridge._failed is None


@pytest.mark.asyncio
async def test_deliberate_cancel_respawn_counts_no_attempt(bridge, monkeypatch) -> None:
    spawned: list[int] = []
    monkeypatch.setattr(bridge, "_spawn_worker", _fake_spawn(bridge, spawned))

    await bridge.cancel_active()  # kill (no proc - noop) + immediate respawn
    await _wait_respawn_task(bridge)

    assert spawned == [1]
    assert bridge._respawn_attempts == 0  # frequent cancels never trip the cap
    assert bridge._ready_evt.is_set()


@pytest.mark.asyncio
async def test_crash_loop_lands_in_failed_state_and_fails_calls_fast(bridge, monkeypatch) -> None:
    async def bad_spawn() -> None:
        raise RuntimeError("exec failed")

    monkeypatch.setattr(bridge, "_spawn_worker", bad_spawn)

    bridge._schedule_respawn()
    await _wait_respawn_task(bridge)

    assert bridge._failed is not None
    assert "crash-looped" in bridge._failed
    assert bridge._respawn_attempts == bridge._respawn_max_attempts

    with pytest.raises(SubprocessHostError, match="crash-looped"):
        await bridge.call_handler("anything", object(), (), {})

    # Once failed, nothing respawns anymore.
    spawned: list[int] = []
    monkeypatch.setattr(bridge, "_spawn_worker", _fake_spawn(bridge, spawned))
    bridge._schedule_respawn()
    await asyncio.sleep(0.05)
    assert spawned == []


@pytest.mark.asyncio
async def test_stable_uptime_resets_attempt_counter(bridge, monkeypatch) -> None:
    spawned: list[int] = []
    monkeypatch.setattr(bridge, "_spawn_worker", _fake_spawn(bridge, spawned))

    # Pretend a long-ago incident maxed the counter, then the worker ran
    # stably for well over the reset window before crashing again.
    bridge._respawn_attempts = bridge._respawn_max_attempts
    bridge._last_ready_monotonic = asyncio.get_running_loop().time() - 3600.0

    bridge._ready_evt.clear()
    bridge._schedule_respawn()
    await _wait_respawn_task(bridge)

    assert spawned == [1]  # respawned instead of entering failed state
    assert bridge._failed is None
    assert bridge._respawn_attempts == 1  # reset to 0, then this attempt


@pytest.mark.asyncio
async def test_stop_suppresses_respawn(bridge, monkeypatch) -> None:
    spawned: list[int] = []
    monkeypatch.setattr(bridge, "_spawn_worker", _fake_spawn(bridge, spawned))
    bridge._stopping = True
    bridge._schedule_respawn()
    await asyncio.sleep(0.05)
    assert spawned == []
    assert bridge._respawn_task is None


@pytest.mark.asyncio
async def test_no_double_spawn_when_cancel_and_eof_race(bridge, monkeypatch) -> None:
    release = asyncio.Event()
    spawned: list[int] = []

    async def slow_spawn() -> None:
        spawned.append(1)
        await release.wait()
        bridge._ready_evt.set()

    monkeypatch.setattr(bridge, "_spawn_worker", slow_spawn)

    bridge._schedule_respawn(deliberate=True)  # cancel path
    await asyncio.sleep(0.02)
    bridge._schedule_respawn()  # EOF arrives while the first respawn runs
    await asyncio.sleep(0.02)

    release.set()
    await _wait_respawn_task(bridge)
    assert spawned == [1]


@pytest.mark.asyncio
async def test_protocol_mismatch_on_respawn_is_terminal(bridge, monkeypatch) -> None:
    async def mismatch_spawn() -> None:
        bridge._ready_error = "Worker for 'respawntest' speaks wire protocol 99"
        bridge._ready_evt.set()

    monkeypatch.setattr(bridge, "_spawn_worker", mismatch_spawn)
    bridge._ready_evt.clear()
    bridge._schedule_respawn()
    await _wait_respawn_task(bridge)

    assert bridge._failed is not None
    assert "protocol" in bridge._failed
