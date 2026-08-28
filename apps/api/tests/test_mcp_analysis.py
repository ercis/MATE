"""Tests for the MCP analysis toolset + the structural data wall.

The wall test is the core deliverable: a module whose ``guidance_payload``
(or dataset route) reaches for raw event rows must fail with a clean
``[conflict]`` tool error - never leak rows. Exercised through the REAL path
(the process loader's ``_make_context`` with ``restrict_event_log=True``), not
by monkeypatching ``build_payload``.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from httpx import AsyncClient

from mate.api.auth.dependencies import CurrentUser
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import EventLog, ModuleConfig, ModuleInstall, UserSetting
from mate.api.mcp.auth import MCPPrincipal
from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.scopes import ALL_SCOPES, SCOPE_MODULES_READ
from mate.api.mcp.toolsets import analysis
from mate.api.modules.cache import ResultCache
from mate.sdk.decorators import route as sdk_route
from mate.sdk.manifest import DatasetEntry, Manifest

from .conftest import TEST_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _FakeRequest:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.scope: dict[str, object] = {} if principal is None else {"mate_principal": principal}


class _FakeRequestContext:
    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request = _FakeRequest(principal)


class _FakeCtx:
    """Stand-in for FastMCP's Context - tools only read request_context.request.scope."""

    def __init__(self, principal: MCPPrincipal | None) -> None:
        self.request_context = _FakeRequestContext(principal)


def _principal(user_id: str = TEST_USER_ID, scopes: tuple[str, ...] = ALL_SCOPES) -> MCPPrincipal:
    cu = CurrentUser(id=user_id, email=None, preferred_username=None, name=None, roles=())
    return MCPPrincipal(user=cu, token_id="tok", scopes=scopes, auth_type="pat")


def _ctx(principal: MCPPrincipal | None) -> Any:
    """Typed-as-Any fake ctx so tool signatures (MCPContext) accept it."""
    return _FakeCtx(principal)


async def _seed_log(log_id: str, user_id: str = TEST_USER_ID) -> None:
    """Owned EventLog row + egress consent for the caller."""
    sm = get_sessionmaker()
    async with sm() as session:
        if await session.get(EventLog, log_id) is None:
            session.add(
                EventLog(id=log_id, user_id=user_id, name=log_id, status="ready", created_at=_now())
            )
        consent = await session.get(UserSetting, (user_id, MCP_EGRESS_CONSENT_KEY))
        if consent is None:
            session.add(UserSetting(user_id=user_id, key=MCP_EGRESS_CONSENT_KEY, value_json=True))
        else:
            consent.value_json = True
        await session.commit()


@contextlib.asynccontextmanager
async def _fake_module(
    module_id: str,
    instance: Any,
    *,
    manifest: Manifest | None = None,
    install_for: str | None = TEST_USER_ID,
) -> AsyncGenerator[Any]:
    """Register a fake loaded module on the REAL loader (+ a ModuleInstall row).

    A SimpleNamespace with .id/.manifest/.instance satisfies every read path the
    analysis tools take. The offload-meta cache is pre-seeded to None so
    ``_make_context`` never dereferences the (absent) discovered folder.
    Restores loader + DB state on exit.
    """
    from mate.api.modules import get_module_loader

    loader = cast(Any, get_module_loader())
    man = manifest or Manifest(id=module_id, name=module_id, version="0.0.1", category="other")
    loader.loaded[module_id] = SimpleNamespace(id=module_id, manifest=man, instance=instance)
    loader._offload_meta_cache[module_id] = None
    sm = get_sessionmaker()
    if install_for is not None:
        async with sm() as session:
            if await session.get(ModuleInstall, (install_for, module_id)) is None:
                session.add(
                    ModuleInstall(user_id=install_for, module_id=module_id, source="upload")
                )
            await session.commit()
    try:
        yield loader
    finally:
        loader.loaded.pop(module_id, None)
        loader._offload_meta_cache.pop(module_id, None)
        async with sm() as session:
            if install_for is not None:
                install = await session.get(ModuleInstall, (install_for, module_id))
                if install is not None:
                    await session.delete(install)
                cfg = await session.get(ModuleConfig, (install_for, module_id))
                if cfg is not None:
                    await session.delete(cfg)
            await session.commit()


# ── fake module instances ────────────────────────────────────────────────────


class _RawReadingModule:
    """A module whose guidance_payload tries to read raw event rows."""

    async def guidance_payload(self, ctx: Any) -> Any:
        df = await ctx.event_log.pandas()  # must raise behind the wall
        return {"leaked_rows": df.to_dict()}


class _CuratedModule:
    """A well-behaved module: guidance_payload returns aggregates only."""

    async def guidance_payload(self, ctx: Any) -> Any:
        return {"kpi": 42}


class _DatasetModule:
    """Module with dataset routes: one walled, one reading its own cache."""

    @sdk_route.get("/wall-table")
    async def wall_table(self, ctx: Any) -> Any:
        await ctx.event_log.pandas()  # must raise behind the wall
        return {"items": [{"leak": 1}]}

    @sdk_route.get("/cached-table")
    async def cached_table(self, ctx: Any) -> Any:
        rows = await ctx.cache.get("rows")
        return {"items": rows or []}


_DS_MANIFEST = Manifest(
    id="mcpan_ds_mod",
    name="Dataset mod",
    version="0.0.1",
    category="other",
    datasets=[
        DatasetEntry(id="walled", shape="table", route="/wall-table"),
        DatasetEntry(id="cached", shape="table", route="/cached-table"),
    ],
)


# ── the data wall (core deliverable) ─────────────────────────────────────────


async def test_wall_module_output_blocks_raw_rows(client: AsyncClient) -> None:
    """REAL-path regression: get_module_output on a module whose
    guidance_payload calls ctx.event_log.pandas() fails [conflict] - the
    PermissionError from _RestrictedEventLog surfaces cleanly, no data."""
    await _seed_log("mcpan-wall-log")
    async with _fake_module("mcpan_wall_mod", _RawReadingModule()):
        with pytest.raises(MCPToolError) as ei:
            await analysis.get_module_output(
                _ctx(_principal()), log_id="mcpan-wall-log", module_id="mcpan_wall_mod"
            )
        assert ei.value.code == "conflict"
        assert "no precomputed results" in str(ei.value)
        assert "leaked_rows" not in str(ei.value)


async def test_wall_lets_curated_output_through(client: AsyncClient) -> None:
    await _seed_log("mcpan-ok-log")
    async with _fake_module("mcpan_ok_mod", _CuratedModule()):
        out = await analysis.get_module_output(
            _ctx(_principal()), log_id="mcpan-ok-log", module_id="mcpan_ok_mod"
        )
        assert out["output"] == {"kpi": 42}


async def test_wall_dataset_blocks_raw_rows_and_serves_cache(client: AsyncClient) -> None:
    """get_dataset rides resolve_dataset → run_dataset_route with the wall:
    a raw-reading route fails [conflict]; a cache-reading route works."""
    await _seed_log("mcpan-ds-log")
    async with _fake_module("mcpan_ds_mod", _DatasetModule(), manifest=_DS_MANIFEST):
        with pytest.raises(MCPToolError) as ei:
            await analysis.get_dataset(
                _ctx(_principal()),
                module_id="mcpan_ds_mod",
                dataset_id="walled",
                log_id="mcpan-ds-log",
            )
        assert ei.value.code == "conflict"
        assert "leak" not in str(ei.value)

        await ResultCache("mcpan-ds-log", "mcpan_ds_mod", TEST_USER_ID).set("rows", [{"a": 1}])
        out = await analysis.get_dataset(
            _ctx(_principal()),
            module_id="mcpan_ds_mod",
            dataset_id="cached",
            log_id="mcpan-ds-log",
        )
        assert out["dataset"]["shape"] == "table"
        assert out["dataset"]["data"]["rows"] == [{"a": 1}]


async def test_make_context_restrict_passthrough(client: AsyncClient) -> None:
    """Unit check: _make_context(..., restrict_event_log=True) yields a ctx
    whose event_log raises PermissionError on every raw accessor."""
    from mate.api.modules import get_module_loader

    await _seed_log("mcpan-restrict-log")
    loader = cast(Any, get_module_loader())
    ctx = await loader._make_context(
        "mcpan_probe_mod", "mcpan-restrict-log", TEST_USER_ID, restrict_event_log=True
    )
    try:
        with pytest.raises(PermissionError):
            await ctx.event_log.pandas()
        with pytest.raises(PermissionError):
            await ctx.event_log.duckdb_fetch("SELECT 1")
    finally:
        shutil.rmtree(ctx.workdir, ignore_errors=True)


# ── reads ────────────────────────────────────────────────────────────────────


async def test_list_modules_installed_with_availability(client: AsyncClient) -> None:
    await _seed_log("mcpan-list-log")
    async with _fake_module("mcpan_list_mod", _CuratedModule()):
        out = await analysis.list_modules(_ctx(_principal()), log_id="mcpan-list-log")
        entry = next(e for e in out if e["module_id"] == "mcpan_list_mod")
        assert entry["name"] == "mcpan_list_mod"
        assert entry["enabled"] is True
        assert entry["has_guidance"] is True
        assert entry["availability"] == {"status": "available", "reasons": []}


async def test_get_process_overview_concurrent_skips_failures(client: AsyncClient) -> None:
    await _seed_log("mcpan-ov-log")
    async with (
        _fake_module("mcpan_ov_ok", _CuratedModule()),
        _fake_module("mcpan_ov_raw", _RawReadingModule()),
    ):
        out = await analysis.get_process_overview(_ctx(_principal()), log_id="mcpan-ov-log")
        assert out["modules"]["mcpan_ov_ok"] == {"kpi": 42}
        assert "mcpan_ov_raw" not in out["modules"]
        assert "no precomputed results" in out["skipped"]["mcpan_ov_raw"]


async def test_get_module_results_canonical_public_keys_only(client: AsyncClient) -> None:
    await _seed_log("mcpan-res-log")
    async with _fake_module("mcpan_res_mod", SimpleNamespace()):
        cache = ResultCache("mcpan-res-log", "mcpan_res_mod", TEST_USER_ID)
        (cache.dir / "kpis.json").write_text(json.dumps({"n": 1}))
        (cache.dir / "__ai_guidance.json").write_text(json.dumps({"secret": True}))
        (cache.dir / "model.parquet").write_bytes(b"PAR1")
        variant = cache.dir / "_v_abc"
        variant.mkdir()
        (variant / "kpis.json").write_text(json.dumps({"n": 999}))

        out = await analysis.get_module_results(
            _ctx(_principal()), log_id="mcpan-res-log", module_id="mcpan_res_mod"
        )
        assert out["keys"] == ["kpis"]
        assert out["results"] == {"kpis": {"n": 1}}
        assert out["artifact_keys"] == ["model"]


async def test_get_module_results_requires_install(client: AsyncClient) -> None:
    await _seed_log("mcpan-res2-log")
    with pytest.raises(MCPToolError) as ei:
        await analysis.get_module_results(
            _ctx(_principal()), log_id="mcpan-res2-log", module_id="mcpan_ghost_mod"
        )
    assert ei.value.code == "not_found"


async def test_list_datasets_catalog(client: AsyncClient) -> None:
    await _seed_log("mcpan-cat-log")  # just for consent seeding
    async with _fake_module("mcpan_ds_mod", _DatasetModule(), manifest=_DS_MANIFEST):
        out = await analysis.list_datasets(_ctx(_principal()))
        mine = [e for e in out if e["module_id"] == "mcpan_ds_mod"]
        assert {e["dataset_id"] for e in mine} == {"walled", "cached"}
        assert all(e["shape"] == "table" for e in mine)
        assert mine[0]["log_models"] == ["case_centric"]


async def test_get_cached_guidance_empty_then_cached(client: AsyncClient) -> None:
    await _seed_log("mcpan-guid-log")
    out = await analysis.get_cached_guidance(
        _ctx(_principal()), log_id="mcpan-guid-log", module_id="__platform__"
    )
    assert out["cached"] is False and out["guidance"] is None

    record = {
        "guidance": {"interpretation": "x", "recommended_actions": [], "anomaly_flags": []},
        "output_hash": "abc123",
        "generated_at": 1.0,
        "model": "m",
        "provider": "p",
    }
    await ResultCache("mcpan-guid-log", "__platform__", TEST_USER_ID).set("__ai_guidance", record)
    out = await analysis.get_cached_guidance(
        _ctx(_principal()), log_id="mcpan-guid-log", module_id="__platform__"
    )
    assert out["cached"] is True
    assert out["output_hash"] == "abc123"
    assert out["guidance"]["interpretation"] == "x"


# ── writes ───────────────────────────────────────────────────────────────────


async def test_set_module_config_not_owned_is_not_found(client: AsyncClient) -> None:
    await _seed_log("mcpan-cfg0-log")  # consent
    with pytest.raises(MCPToolError) as ei:
        await analysis.set_module_config(
            _ctx(_principal()), module_id="mcpan_ghost_mod", enabled=False
        )
    assert ei.value.code == "not_found"


async def test_set_module_config_merges_partial_updates(client: AsyncClient) -> None:
    await _seed_log("mcpan-cfg-log")  # consent
    async with _fake_module("mcpan_cfg_mod", SimpleNamespace()):
        out = await analysis.set_module_config(
            _ctx(_principal()), module_id="mcpan_cfg_mod", config={"a": 1}, enabled=False
        )
        assert out == {"module_id": "mcpan_cfg_mod", "config": {"a": 1}, "enabled": False}
        # enabled-only update keeps the stored config.
        out2 = await analysis.set_module_config(
            _ctx(_principal()), module_id="mcpan_cfg_mod", enabled=True
        )
        assert out2["config"] == {"a": 1} and out2["enabled"] is True

        sm = get_sessionmaker()
        async with sm() as session:
            row = await session.get(ModuleConfig, (TEST_USER_ID, "mcpan_cfg_mod"))
            assert row is not None and row.enabled is True and row.config_json == {"a": 1}


async def test_set_module_config_scope_denied(client: AsyncClient) -> None:
    p = _principal(scopes=(SCOPE_MODULES_READ,))
    with pytest.raises(MCPToolError) as ei:
        await analysis.set_module_config(_ctx(p), module_id="whatever", enabled=True)
    assert ei.value.code == "scope_missing"


async def test_uninstall_module_preview_then_confirm(client: AsyncClient) -> None:
    await _seed_log("mcpan-del-log")  # consent
    async with _fake_module("mcpan_del_mod", SimpleNamespace()):
        preview = await analysis.uninstall_module(_ctx(_principal()), module_id="mcpan_del_mod")
        assert preview["confirmed"] is False
        assert "[confirm_required]" in preview["message"]
        assert preview["preview"]["module_id"] == "mcpan_del_mod"
        assert preview["preview"]["last_owner"] is True
        assert preview["preview"]["removes_shared_artifacts"] is True

        sm = get_sessionmaker()
        async with sm() as session:  # preview must not mutate
            assert await session.get(ModuleInstall, (TEST_USER_ID, "mcpan_del_mod")) is not None

        done = await analysis.uninstall_module(
            _ctx(_principal()), module_id="mcpan_del_mod", confirm=True
        )
        assert done == {"uninstalled": "mcpan_del_mod", "last_owner": True}
        async with sm() as session:
            assert await session.get(ModuleInstall, (TEST_USER_ID, "mcpan_del_mod")) is None


async def test_restore_default_modules_reports_restored(client: AsyncClient) -> None:
    await _seed_log("mcpan-rest-log")  # consent
    out = await analysis.restore_default_modules(_ctx(_principal()))
    assert out == {"restored": []}  # test loader ships no default modules
