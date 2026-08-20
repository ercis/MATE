from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_modules_list_includes_sample(client_with_sample_mod: AsyncClient) -> None:
    resp = await client_with_sample_mod.get("/api/v1/modules")
    assert resp.status_code == 200
    data = resp.json()
    ids = [m["id"] for m in data]
    assert "sample_mod" in ids
    sample = next(m for m in data if m["id"] == "sample_mod")
    assert sample["category"] == "foundation"
    assert sample["provides"] == ["sample.ping"]


@pytest.mark.asyncio
async def test_module_route_mounted(client_with_sample_mod: AsyncClient) -> None:
    """The @route.get('/ping') on SampleModule should be mounted under
    /api/v1/modules/sample_mod/ping by the loader."""
    resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/ping")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"module_id": "sample_mod", "status": "pong"}


async def _seed_sample_log(client: AsyncClient) -> str:
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    deadline = time.time() + 5.0
    while time.time() < deadline:
        st = (await client.get(f"/api/v1/event-logs/{log_id}")).json()["status"]
        if st == "ready":
            return log_id
        if st == "failed":
            raise AssertionError("import failed")
        await asyncio.sleep(0.05)
    raise AssertionError("import did not finish")


def _encode_filter(entries: list[dict]) -> str:
    return base64.b64encode(json.dumps({"filter": entries}).encode()).decode()


@pytest.mark.asyncio
async def test_module_route_applies_event_filter_header(
    client_with_sample_mod: AsyncClient,
) -> None:
    """A dashboard's `X-FF-Event-Filter` header narrows what a module sees on
    that one request, without persisting anything on the log."""
    log_id = await _seed_sample_log(client_with_sample_mod)

    # No header → the module sees the whole log (9 events in the fixture).
    full = await client_with_sample_mod.get(f"/api/v1/modules/sample_mod/count?log_id={log_id}")
    assert full.status_code == 200, full.text
    assert full.json() == {"events": 9}

    # With the header → only the 2 'ship' events.
    header = _encode_filter([{"field": "activity", "op": "equals", "value": "ship"}])
    filtered = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/count?log_id={log_id}",
        headers={"X-FF-Event-Filter": header},
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json() == {"events": 2}

    # The override is ephemeral - a subsequent unheadered call sees the full log.
    again = await client_with_sample_mod.get(f"/api/v1/modules/sample_mod/count?log_id={log_id}")
    assert again.json() == {"events": 9}


@pytest.mark.asyncio
async def test_open_event_log_enforces_ownership(
    client_with_sample_mod: AsyncClient,
) -> None:
    """`ctx.open_event_log` is the only sanctioned cross-log accessor and it must
    honour the tenant-isolation invariant: a user can open their own second log
    but never another user's (reported the same as 'not found')."""
    import uuid

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog, User

    log_id = await _seed_sample_log(client_with_sample_mod)

    # Opening one's own log succeeds and reads its events (9 in the fixture).
    own = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/open-other?log_id={log_id}&other_id={log_id}"
    )
    assert own.status_code == 200, own.text
    assert own.json() == {"events": 9}

    # A log owned by a *different* user must be refused.
    other_user_id = "ffffffff-0000-7000-8000-000000000099"
    other_user_log = str(uuid.uuid4())
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(User, other_user_id) is None:
            session.add(User(id=other_user_id, email="other@mate.local"))
            await session.commit()
        session.add(
            EventLog(
                id=other_user_log,
                user_id=other_user_id,
                name="Someone else's log",
                status="ready",
                log_model="case_centric",
            )
        )
        await session.commit()

    denied = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/open-other?log_id={log_id}&other_id={other_user_log}"
    )
    assert denied.status_code == 200, denied.text
    assert denied.json() == {"denied": True}

    # A non-existent log id is refused identically (never confirmed).
    missing = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/open-other?log_id={log_id}&other_id={uuid.uuid4()}"
    )
    assert missing.json() == {"denied": True}


@pytest.mark.asyncio
async def test_event_filter_header_bypasses_stale_result_cache(
    client_with_sample_mod: AsyncClient,
) -> None:
    """A cached endpoint must not serve the unfiltered cache to a filtered
    request. The ephemeral filter gets its own cache namespace, so the filtered
    call recomputes (2 'ship' events) even though the full result (9) was cached
    first - and the unfiltered cache is still returned afterwards."""
    log_id = await _seed_sample_log(client_with_sample_mod)

    # Warm the cache with the full log.
    full = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/cached-count?log_id={log_id}"
    )
    assert full.json() == {"events": 9}, full.text

    # Filtered request: must reflect the filter, not the cached full count.
    header = _encode_filter([{"field": "activity", "op": "equals", "value": "ship"}])
    filtered = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/cached-count?log_id={log_id}",
        headers={"X-FF-Event-Filter": header},
    )
    assert filtered.json() == {"events": 2}, filtered.text

    # The unfiltered cache is untouched by the ephemeral variant.
    again = await client_with_sample_mod.get(
        f"/api/v1/modules/sample_mod/cached-count?log_id={log_id}"
    )
    assert again.json() == {"events": 9}, again.text


@pytest.mark.asyncio
async def test_module_route_ignores_malformed_filter_header(
    client_with_sample_mod: AsyncClient,
) -> None:
    """A garbage / stale header degrades to 'no filter' rather than 500."""
    log_id = await _seed_sample_log(client_with_sample_mod)
    for bad in ("not-base64!!", base64.b64encode(b"{not json").decode()):
        resp = await client_with_sample_mod.get(
            f"/api/v1/modules/sample_mod/count?log_id={log_id}",
            headers={"X-FF-Event-Filter": bad},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"events": 9}


@pytest.mark.asyncio
async def test_module_manifest_endpoint(client_with_sample_mod: AsyncClient) -> None:
    resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/manifest")
    assert resp.status_code == 200
    m = resp.json()
    assert m["id"] == "sample_mod"
    assert "duckdb" in m["dependencies"]["python"]["inherit"]


@pytest.mark.asyncio
async def test_module_config_get_put(client_with_sample_mod: AsyncClient) -> None:
    initial = await client_with_sample_mod.get("/api/v1/modules/sample_mod/config")
    assert initial.status_code == 200
    assert initial.json() == {"config": {}, "enabled": True, "controlled_by_admin": False}

    payload = {"config": {"threshold": 0.5}, "enabled": True}
    put = await client_with_sample_mod.put("/api/v1/modules/sample_mod/config", json=payload)
    assert put.status_code == 200
    assert put.json() == {**payload, "controlled_by_admin": False}

    again = await client_with_sample_mod.get("/api/v1/modules/sample_mod/config")
    assert again.json() == {**payload, "controlled_by_admin": False}


@pytest.mark.asyncio
async def test_module_assets_served_and_traversal_rejected(
    client_with_sample_mod: AsyncClient, tmp_path: Path
) -> None:
    """`/api/v1/modules/{id}/assets/<path>` serves files from modules/<id>/.dist/
    (the bundler's output dir). Path traversal must be rejected. We synthesise a
    fake `panel.js` under the fixture module's `.dist/` so we can exercise the
    route without running esbuild from a test."""
    from mate.api.config import get_settings

    settings = get_settings()
    dist_dir = settings.modules_dir / "sample_mod" / ".dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    panel = dist_dir / "panel.js"
    panel.write_text("module.exports = { Panel: () => null };\n")
    secret = settings.modules_dir / "sample_mod" / "secret.txt"
    secret.write_text("nope")

    ok = await client_with_sample_mod.get("/api/v1/modules/sample_mod/assets/panel.js")
    assert ok.status_code == 200
    assert "module.exports" in ok.text
    assert ok.headers.get("content-type", "").startswith("application/javascript")

    missing = await client_with_sample_mod.get("/api/v1/modules/sample_mod/assets/nope.js")
    assert missing.status_code == 404

    # Traversal - try to escape .dist/ via ../. resolve() collapses it, then
    # the relative_to() check fails.
    escape = await client_with_sample_mod.get("/api/v1/modules/sample_mod/assets/..%2Fsecret.txt")
    assert escape.status_code in (400, 404)


@pytest.mark.asyncio
async def test_module_install_from_upload(client_with_sample_mod: AsyncClient) -> None:
    """Upload a zipped module with a different id, wait for the install job to
    complete, and confirm the new module is loaded and routable."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "uploaded_mod/manifest.yaml",
            "id: uploaded_mod\nname: Uploaded\nversion: 0.0.1\ncategory: foundation\n"
            "requirements:\n  event_log:\n    required_columns: [case_id, activity, timestamp]\n"
            "    min_events: 1\n    min_cases: 1\n"
            "provides: []\nconsumes: []\n"
            "dependencies:\n  python:\n    inherit: []\n    isolation: in_process\n",
        )
        zf.writestr(
            "uploaded_mod/module.py",
            (
                "from mate.sdk import Module, ModuleContext, route\n\n"
                "class UploadedModule(Module):\n"
                '    id = "uploaded_mod"\n\n'
                '    @route.get("/ping")\n'
                "    async def ping(self, ctx: ModuleContext) -> dict[str, str]:\n"
                '        return {"id": ctx.module_id}\n'
            ),
        )
    buf.seek(0)

    resp = await client_with_sample_mod.post(
        "/api/v1/modules/install",
        files={"file": ("uploaded_mod.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    # Wait for the install job to finish.
    for _ in range(50):
        d = await client_with_sample_mod.get(f"/api/v1/jobs/{job_id}")
        if d.status_code == 200 and d.json()["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert d.json()["status"] == "completed", d.json()

    # New module should now be listed and routable.
    listing = await client_with_sample_mod.get("/api/v1/modules")
    ids = [m["id"] for m in listing.json()]
    assert "uploaded_mod" in ids
    ping = await client_with_sample_mod.get("/api/v1/modules/uploaded_mod/ping")
    assert ping.status_code == 200
    assert ping.json() == {"id": "uploaded_mod"}


@pytest.mark.asyncio
async def test_module_install_upload_rejects_bad_suffix(
    client_with_sample_mod: AsyncClient,
) -> None:
    resp = await client_with_sample_mod.post(
        "/api/v1/modules/install",
        files={"file": ("not-archive.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_module_install_registry_npm_rejected(
    client_with_sample_mod: AsyncClient,
) -> None:
    """npm source has no Python entry point to bind to - the job must surface
    a clear error rather than silently no-op."""
    resp = await client_with_sample_mod.post(
        "/api/v1/modules/install/registry",
        json={"source": "npm", "id": "@scope/pkg"},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    for _ in range(30):
        d = await client_with_sample_mod.get(f"/api/v1/jobs/{job_id}")
        if d.status_code == 200 and d.json()["status"] in {"completed", "failed"}:
            break
        await asyncio.sleep(0.1)
    body = d.json()
    assert body["status"] == "failed"
    msg = (body.get("message") or "") + (body.get("error") or "")
    assert "npm" in msg.lower()


@pytest.mark.asyncio
async def test_entry_point_discovery(client_with_sample_mod: AsyncClient) -> None:
    """Register an in-process entry point pointing at a fake package; verify
    the discovery layer picks it up. We don't `pip install` anything here -
    we use `importlib.metadata`'s test hooks via a stub Distribution.
    """
    import importlib.metadata
    import sys
    from pathlib import Path

    from mate.api.modules.discovery import discover_entry_points

    # Build an in-memory package with a manifest.yaml alongside __init__.py.
    pkg_root = Path(tempfile.mkdtemp(prefix="ff-ep-test-"))
    (pkg_root / "ff_ep_test_mod").mkdir()
    (pkg_root / "ff_ep_test_mod" / "__init__.py").write_text("")
    (pkg_root / "ff_ep_test_mod" / "manifest.yaml").write_text(
        "id: ep_mod\nname: EP\nversion: 0.0.1\ncategory: foundation\n"
        "requirements:\n  event_log:\n    required_columns: [case_id, activity, timestamp]\n"
        "    min_events: 1\n    min_cases: 1\n"
        "provides: []\nconsumes: []\n"
        "dependencies:\n  python:\n    inherit: []\n    isolation: in_process\n"
    )
    sys.path.insert(0, str(pkg_root))

    # Register a fake distribution exposing the entry point.
    class _StubDist(importlib.metadata.Distribution):
        def read_text(self, filename):  # type: ignore[override]
            if filename == "METADATA":
                return "Metadata-Version: 1.0\nName: ff-ep-test-mod\nVersion: 0.0.1\n"
            if filename == "entry_points.txt":
                return "[mate.modules]\nep_mod = ff_ep_test_mod\n"
            return None

        def locate_file(self, path):  # type: ignore[override]
            return pkg_root / path

    original_distributions = importlib.metadata.distributions

    def _patched_distributions(*args, **kwargs):
        yield from original_distributions(*args, **kwargs)
        yield _StubDist()

    importlib.metadata.distributions = _patched_distributions  # type: ignore[assignment]
    try:
        cache = getattr(importlib.metadata, "_ep_cache", None)
        if cache is not None:
            cache.clear()
        found = {d.id: d for d in discover_entry_points()}
        assert "ep_mod" in found, list(found.keys())
        assert found["ep_mod"].source == "entry_point"
        assert (found["ep_mod"].folder / "manifest.yaml").exists()
    finally:
        importlib.metadata.distributions = original_distributions  # type: ignore[assignment]
        sys.path.remove(str(pkg_root))


def test_sweep_stale_workdirs_removes_old_dirs(tmp_path: Path) -> None:
    """`sweep_stale_workdirs` deletes leftover `ff-mod-*` temp dirs older than
    the cutoff. Belt-and-braces for the rare crash where the per-invocation
    cleanup in the loader's `_invoke_handler` didn't run.
    """
    import os
    import tempfile as _tempfile
    from unittest.mock import patch

    from mate.api.modules.hot_reload import sweep_stale_workdirs

    # Run in an isolated tmp_root so we don't touch real system temp dirs.
    with patch.object(_tempfile, "gettempdir", return_value=str(tmp_path)):
        old = tmp_path / "ff-mod-discovery-abc"
        old.mkdir()
        recent = tmp_path / "ff-mod-discovery-xyz"
        recent.mkdir()
        unrelated = tmp_path / "other-temp"
        unrelated.mkdir()
        # Backdate `old` by 48 hours.
        past = time.time() - 48 * 3600
        os.utime(old, (past, past))

        removed = sweep_stale_workdirs(max_age_hours=24)

    assert removed == 1
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_subprocess_wire_protocol_bidirectional() -> None:
    """Exercise the JSON-RPC framing without spawning a real subprocess.

    Two ``WireConnection``s plumbed to opposite ends of an in-memory socket
    pair stand in for host ↔ worker. We register an `add` method on one side
    and call it from the other, confirming requests and responses cross
    correctly and that simultaneous requests in both directions don't
    interleave wrongly.
    """
    import socket

    from mate.api.modules.subprocess_worker import WireConnection

    sa, sb = socket.socketpair()
    reader_a, writer_a = await asyncio.open_connection(sock=sa)
    reader_b, writer_b = await asyncio.open_connection(sock=sb)
    conn_a = WireConnection(reader_a, writer_a)
    conn_b = WireConnection(reader_b, writer_b)

    conn_b.register("add", lambda p: p["x"] + p["y"])
    conn_a.register("greet", lambda p: f"hello {p['name']}")

    task_a = asyncio.create_task(conn_a.run())
    task_b = asyncio.create_task(conn_b.run())

    # Host → worker (A calls B).
    result = await conn_a.send_request("add", {"x": 2, "y": 40})
    assert result == 42

    # Worker → host (B calls A) - exercises the reverse direction so we know
    # the duplex framing doesn't deadlock.
    greeting = await conn_b.send_request("greet", {"name": "world"})
    assert greeting == "hello world"

    # Unknown method must surface a clear error rather than hang.
    with pytest.raises(RuntimeError, match="unknown method"):
        await conn_a.send_request("nope", {})

    writer_a.close()
    writer_b.close()
    for t in (task_a, task_b):
        try:
            await asyncio.wait_for(t, timeout=1.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            t.cancel()


@pytest.mark.asyncio
async def test_handler_workdir_cleaned_up(client_with_sample_mod: AsyncClient) -> None:
    """Each handler invocation gets a fresh `ctx.workdir` from mkdtemp; the
    loader's `_invoke_handler` must rmtree it once the handler returns so per-
    call scratch dirs don't accumulate under the system tmp."""
    tmp_root = Path(tempfile.gettempdir())
    pattern = "ff-mod-sample_mod-*"

    before = set(tmp_root.glob(pattern))
    for _ in range(3):
        resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/ping")
        assert resp.status_code == 200
    after = set(tmp_root.glob(pattern))

    leaked = after - before
    assert not leaked, f"Workdir leak after handler calls: {leaked}"


@pytest.mark.asyncio
async def test_availability_evaluated_against_log_schema(
    client_with_sample_mod: AsyncClient,
) -> None:
    """Upload a small log, then list modules with ?log_id=… and confirm the
    sample module is reported `available` (it requires case_id/activity/timestamp)."""
    with (FIXTURES / "sample.xes").open("rb") as f:
        upload = await client_with_sample_mod.post(
            "/api/v1/event-logs",
            files={"file": ("sample.xes", f, "application/xml")},
        )
    log_id = upload.json()["log_id"]

    # Wait until ready.
    for _ in range(50):
        d = await client_with_sample_mod.get(f"/api/v1/event-logs/{log_id}")
        if d.json()["status"] == "ready":
            break
        await asyncio.sleep(0.05)

    listing = await client_with_sample_mod.get("/api/v1/modules", params={"log_id": log_id})
    assert listing.status_code == 200
    sample = next(m for m in listing.json() if m["id"] == "sample_mod")
    assert sample["availability"]["status"] == "available", sample


@pytest.mark.asyncio
async def test_job_progress_adapter_fraction_vs_counter() -> None:
    """`_JobProgressAdapter` maps a 0-1 *float* fraction onto 0-100 (determinate
    bar + ETA), but leaves explicit counts and integer running counters alone -
    so `update(current=1)` stays "1 processed", never "100%"."""
    from mate.api.modules.loader import _JobProgressAdapter

    calls: list[tuple[int, int | None, str | None, str | None]] = []

    class _FakeHandle:
        async def progress(
            self,
            current: int,
            total: int | None = None,
            *,
            stage: str | None = None,
            message: str | None = None,
            force: bool = False,
        ) -> None:
            calls.append((current, total, stage, message))

        def raise_if_cancelled(self) -> None:
            # Mirror the real JobHandle surface - the adapter polls it for a
            # cooperative cancel before reporting. Never cancelled in this test.
            return None

    adapter = _JobProgressAdapter(_FakeHandle())  # type: ignore[arg-type]

    # Fraction → 0-100 with a synthetic total of 100.
    await adapter.update(0.42, "Computing fitness")
    assert calls[-1] == (42, 100, None, "Computing fitness")

    # Explicit counts pass through unchanged.
    await adapter.update(current=4200, total=10000, stage="replay")
    assert calls[-1] == (4200, 10000, "replay", None)

    # Integer running counter stays a counter (no total), even for 0/1.
    await adapter.update(current=1)
    assert calls[-1] == (1, None, None, None)
    await adapter.update(current=0)
    assert calls[-1] == (0, None, None, None)

    # Float endpoints of the fraction range still map onto the 0-100 scale.
    await adapter.update(1.0)
    assert calls[-1] == (100, 100, None, None)


def test_discover_skips_duplicate_id_across_roots(tmp_path: Path) -> None:
    """A leftover upload colliding with a bundled default must not abort the
    whole load. Regression: a stray ``uploaded_modules/discovery`` next to the
    default ``modules/discovery`` made ``discover()`` raise, which the boot path
    swallowed - leaving the platform with zero modules and no log. discover()
    now keeps the first-seen (defaults) copy and skips the duplicate.
    """
    from mate.api.modules.discovery import discover

    def _write_module(root: Path, module_id: str) -> Path:
        folder = root / module_id
        folder.mkdir(parents=True)
        (folder / "manifest.yaml").write_text(
            f"id: {module_id}\nname: Dup\nversion: 0.1.0\ncategory: foundation\n"
        )
        return folder

    defaults_root = tmp_path / "modules"
    uploads_root = tmp_path / "uploaded_modules"
    kept = _write_module(defaults_root, "discovery")
    _write_module(uploads_root, "discovery")

    # Defaults root is scanned first, so its copy wins and the upload is skipped
    # - without raising.
    found = discover(defaults_root, uploads_root)
    discovery_mods = [d for d in found if d.id == "discovery"]
    assert len(discovery_mods) == 1
    assert discovery_mods[0].folder == kept
