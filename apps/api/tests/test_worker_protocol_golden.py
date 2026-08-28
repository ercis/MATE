"""Wire-protocol golden tests (modules/PROTOCOL.md) - no worker process, no
toolchain: `WireConnection` + the ctx dispatcher driven over in-memory streams.

These pin the frames byte-for-byte-ish (shapes, ids, sentinel strings) so a
foreign SDK implemented against PROTOCOL.md meets exactly what this host emits.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mate.api.modules.ctx_rpc import (
    CANCEL_RPC_MSG,
    cache_envelope,
    cache_unenvelope,
    make_ctx_dispatcher,
)
from mate.api.modules.runtimes.base import WorkerLaunchSpec
from mate.api.modules.subprocess_host import (
    SUPPORTED_PROTOCOL_MAX,
    SubprocessBridge,
)
from mate.api.modules.subprocess_worker import WireConnection
from mate.sdk.errors import Cancelled
from mate.sdk.manifest import Manifest


class _FakeWriter:
    """Captures frames a WireConnection writes."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def frames(self) -> list[dict[str, Any]]:
        blob = b"".join(self.chunks)
        return [json.loads(line) for line in blob.split(b"\n") if line.strip()]


def _conn() -> tuple[WireConnection, asyncio.StreamReader, _FakeWriter]:
    reader = asyncio.StreamReader()
    writer = _FakeWriter()
    return WireConnection(reader, writer), reader, writer  # type: ignore[arg-type]


def _feed(reader: asyncio.StreamReader, *messages: dict[str, Any]) -> None:
    for message in messages:
        reader.feed_data(json.dumps(message).encode() + b"\n")
    reader.feed_eof()


async def _run(conn: WireConnection) -> None:
    await conn.run()
    # Dispatch handlers run as tasks - give them a beat to write replies.
    for _ in range(20):
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_request_dispatch_result_and_id_echo() -> None:
    conn, reader, writer = _conn()
    conn.register("ping", lambda _params: True)
    _feed(reader, {"id": 5, "method": "ping", "params": {}})
    await _run(conn)
    assert {"id": 5, "result": True} in writer.frames()


@pytest.mark.asyncio
async def test_unknown_method_error_shape() -> None:
    conn, reader, writer = _conn()
    _feed(reader, {"id": 6, "method": "nope", "params": {}})
    await _run(conn)
    frame = writer.frames()[0]
    assert frame["id"] == 6
    assert "unknown method" in frame["error"]["message"]


@pytest.mark.asyncio
async def test_handler_exception_carries_message_and_traceback() -> None:
    conn, reader, writer = _conn()

    def _boom(_params: dict[str, Any]) -> None:
        raise ValueError("kaput")

    conn.register("boom", _boom)
    _feed(reader, {"id": 7, "method": "boom", "params": {}})
    await _run(conn)
    frame = writer.frames()[0]
    assert frame["error"]["message"] == "ValueError: kaput"
    assert "Traceback" in frame["error"]["traceback"]


@pytest.mark.asyncio
async def test_cancelled_handler_reports_exact_sentinel() -> None:
    conn, reader, writer = _conn()

    def _cancelled(_params: dict[str, Any]) -> None:
        raise Cancelled()

    conn.register("job", _cancelled)
    _feed(reader, {"id": 8, "method": "job", "params": {}})
    await _run(conn)
    assert writer.frames()[0]["error"]["message"] == CANCEL_RPC_MSG


@pytest.mark.asyncio
async def test_notification_gets_null_id_reply_and_is_droppable() -> None:
    conn, reader, writer = _conn()
    seen: list[dict[str, Any]] = []

    async def _ready(params: dict[str, Any]) -> bool:
        seen.append(params)
        return True

    conn.register("ready", _ready)
    _feed(reader, {"id": None, "method": "ready", "params": {"protocol": 1}})
    await _run(conn)
    assert seen == [{"protocol": 1}]
    # The receiver replies with id null; a conformant peer must drop it.
    assert writer.frames() == [{"id": None, "result": True}]


@pytest.mark.asyncio
async def test_send_request_resolution_and_unknown_id_drop() -> None:
    conn, reader, writer = _conn()
    run_task = asyncio.create_task(conn.run())
    request = asyncio.create_task(conn.send_request("call", {"handler": "x"}))
    await asyncio.sleep(0.01)

    sent = writer.frames()[0]
    assert sent["method"] == "call" and isinstance(sent["id"], int)

    # A reply to an unknown id must be dropped, not matched.
    reader.feed_data(json.dumps({"id": 424242, "result": "wrong"}).encode() + b"\n")
    await asyncio.sleep(0.01)
    assert not request.done()

    reader.feed_data(json.dumps({"id": sent["id"], "result": "right"}).encode() + b"\n")
    assert await asyncio.wait_for(request, timeout=1.0) == "right"

    reader.feed_eof()
    await run_task


@pytest.mark.asyncio
async def test_inbound_cancel_sentinel_reconstructs_cancelled() -> None:
    conn, reader, writer = _conn()
    run_task = asyncio.create_task(conn.run())
    request = asyncio.create_task(conn.send_request("ctx.cancel.check", {}))
    await asyncio.sleep(0.01)
    sent = writer.frames()[0]
    reader.feed_data(
        json.dumps(
            {"id": sent["id"], "error": {"message": f"RuntimeError: {CANCEL_RPC_MSG}"}}
        ).encode()
        + b"\n"
    )
    with pytest.raises(Cancelled):
        await asyncio.wait_for(request, timeout=1.0)
    reader.feed_eof()
    await run_task


def test_cache_envelopes(tmp_path) -> None:
    inline = cache_envelope({"a": [1, 2]}, str(tmp_path))
    assert inline == {"kind": "json", "value": {"a": [1, 2]}}
    assert cache_unenvelope(inline) == {"a": [1, 2]}

    pickled = cache_envelope(b"\x00\x01", str(tmp_path))
    assert pickled["kind"] == "pickle"
    assert pickled["path"].startswith(str(tmp_path))
    assert cache_unenvelope(pickled) == b"\x00\x01"


@pytest.mark.asyncio
async def test_ctx_dispatcher_guard_raises_sentinel_when_cancelled() -> None:
    dispatcher = make_ctx_dispatcher(lambda _token: object(), lambda _token: True)
    with pytest.raises(RuntimeError, match=CANCEL_RPC_MSG):
        await dispatcher["ctx.cancel.check"]({"ctx_token": "tok"})


def _bridge() -> SubprocessBridge:
    manifest = Manifest.model_validate(
        {
            "id": "prototest",
            "name": "P",
            "version": "1.0.0",
            "category": "other",
            "dependencies": {"python": {"isolation": "subprocess"}},
        }
    )
    import sys

    return SubprocessBridge(
        manifest, __import__("pathlib").Path("."), WorkerLaunchSpec(argv=(sys.executable,))
    )


@pytest.mark.asyncio
async def test_ready_protocol_negotiation() -> None:
    bridge = _bridge()
    try:
        # Missing protocol field == 1 (pre-versioning worker) - accepted.
        assert await bridge._on_ready({"handlers": [], "guidance": None}) is True
        assert bridge._ready_error is None

        # Current version - accepted.
        bridge._ready_evt.clear()
        assert await bridge._on_ready({"protocol": SUPPORTED_PROTOCOL_MAX, "handlers": []}) is True
        assert bridge._ready_error is None

        # Newer than supported - rejected with an actionable error.
        bridge._ready_evt.clear()
        assert (
            await bridge._on_ready({"protocol": SUPPORTED_PROTOCOL_MAX + 1, "handlers": []})
            is False
        )
        assert bridge._ready_error is not None
        assert "protocol" in bridge._ready_error
        assert bridge._ready_evt.is_set()  # waiters must wake to see the error

        # Garbage - rejected.
        bridge._ready_evt.clear()
        bridge._ready_error = None
        assert await bridge._on_ready({"protocol": "banana", "handlers": []}) is False
        assert bridge._ready_error is not None
    finally:
        await bridge.stop()
