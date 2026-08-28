"""Tests for the conformance module.

Two layers:

* **Worker-direct** - call ``_conformance_worker`` against a real ``reference.bpmn``
  fixture (discovered from the clean sample log) to assert the pm4py contract:
  a clean log fits perfectly, an injected deviation is localised and surfaced,
  the label report flags model/log name mismatches, and the alignments path
  splits log- vs model-moves.
* **Module-level** - drive ``ConformanceModule.run`` / ``results`` through
  Protocol-faithful fakes (``ctx.event_log``/``cache``/``config``/``progress``/
  ``bus``/``run_in_process``) to prove the run caches and the second read does
  not recompute.

The fixture is committed (``fixtures/reference.bpmn``); it is the inductive-miner
BPMN of ``_sample_log`` so its task labels match the log exactly.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
from modules.conformance.conformance import _conformance_worker, _precision_token_worker
from modules.conformance.module import ConformanceModule

_FIXTURE = Path(__file__).parent / "fixtures" / "reference.bpmn"

_CLEAN_VARIANTS = [
    ["receive", "check", "approve", "pay", "close"],
    ["receive", "check", "reject", "close"],
    ["receive", "check", "approve", "close"],
]


def _sample_log(variants: list[list[str]], reps: int = 8) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2024-01-01")
    case_no = 0
    for v_idx, trace in enumerate(variants):
        for _rep in range(reps):
            case_no += 1
            case_id = f"c{case_no}"
            start = base + pd.Timedelta(days=case_no, hours=v_idx)
            for step, act in enumerate(trace):
                rows.append(
                    {
                        "case_id": case_id,
                        "activity": act,
                        "timestamp": start + pd.Timedelta(hours=step),
                    }
                )
    return pd.DataFrame(rows)


def _to_parquet(df: pd.DataFrame) -> str:
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    df.to_parquet(path, index=False)
    return path


# ── worker-direct ────────────────────────────────────────────────────────────


def test_clean_log_fits_perfectly() -> None:
    pq = _to_parquet(_sample_log(_CLEAN_VARIANTS))
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)

    assert out["kind"] == "conformance"
    assert out["kpis"]["log_fitness"] == 1.0
    assert out["kpis"]["total_deviations"] == 0
    assert out["kpis"]["perc_fit_traces"] == 100.0
    # Clean log + matching model → every label matched, none orphaned.
    lr = out["label_report"]
    assert lr["in_model_not_log"] == []
    assert lr["in_log_not_model"] == []
    assert all(a["deviations"] == 0 for a in out["per_activity"])


def test_injected_deviation_is_localised() -> None:
    deviant = [
        *_CLEAN_VARIANTS,
        ["receive", "approve", "close"],  # skips "check"
        ["receive", "check", "hack", "close"],  # inserts unknown "hack"
    ]
    pq = _to_parquet(_sample_log(deviant))
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)

    assert out["kpis"]["log_fitness"] < 1.0
    assert out["kpis"]["total_deviations"] > 0
    assert out["kpis"]["perc_fit_traces"] < 100.0
    # The unknown activity is reported as present in the log but not the model.
    assert "hack" in out["label_report"]["in_log_not_model"]
    # At least one activity carries a non-zero deviation count.
    assert any(a["deviations"] > 0 for a in out["per_activity"])


def test_label_mismatch_is_flagged() -> None:
    renamed = _sample_log(_CLEAN_VARIANTS)
    renamed["activity"] = renamed["activity"].replace({"approve": "approve_request"})
    pq = _to_parquet(renamed)
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)

    lr = out["label_report"]
    # The model still expects "approve"; the log now only has "approve_request".
    assert "approve" in lr["in_model_not_log"]
    assert "approve_request" in lr["in_log_not_model"]


def test_whitespace_case_variants_match_exactly() -> None:
    # Label matching is exact AFTER canonicalisation: trim + collapse whitespace
    # + casefold. "  APPROVE " in the log must bind to the model's "approve" and
    # replay cleanly - no deviations, no label-report noise.
    renamed = _sample_log(_CLEAN_VARIANTS)
    renamed["activity"] = renamed["activity"].replace({"approve": "  APPROVE "})
    pq = _to_parquet(renamed)
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)

    lr = out["label_report"]
    assert "approve" in lr["matched"]
    assert lr["in_model_not_log"] == []
    assert lr["in_log_not_model"] == []
    assert out["kpis"]["log_fitness"] == 1.0
    assert out["kpis"]["total_deviations"] == 0


def test_one_letter_typo_never_matches() -> None:
    # No fuzzy matching, ever: "aprove" (one letter off "approve") is a DIFFERENT
    # activity. It must be flagged as a label mismatch and deviate on replay.
    renamed = _sample_log(_CLEAN_VARIANTS)
    renamed["activity"] = renamed["activity"].replace({"approve": "aprove"})
    pq = _to_parquet(renamed)
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)

    lr = out["label_report"]
    assert "approve" in lr["in_model_not_log"]
    assert "aprove" in lr["in_log_not_model"]
    assert out["kpis"]["log_fitness"] < 1.0
    assert out["kpis"]["total_deviations"] > 0


def test_core_worker_leaves_token_precision_to_host() -> None:
    # The core worker no longer computes token precision (it's the OOM-prone pass,
    # now run in its own crash-isolated offload by the host). It returns the event
    # count the host needs for the budget decision.
    pq = _to_parquet(_sample_log(_CLEAN_VARIANTS))
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "token_replay", 1.0)
    finally:
        os.remove(pq)
    assert out["kpis"]["precision"] is None
    assert out["_n_events"] == len(_sample_log(_CLEAN_VARIANTS))


def test_precision_token_worker_returns_value() -> None:
    # Token precision lives in its own offload worker now; on a clean log it's a
    # real number in [0, 1]. Crash-isolation of this pass is covered module-level.
    pq = _to_parquet(_sample_log(_CLEAN_VARIANTS))
    try:
        prec = _precision_token_worker(pq, str(_FIXTURE))
    finally:
        os.remove(pq)
    assert isinstance(prec, float)
    assert 0.0 <= prec <= 1.0


def test_alignments_split_log_and_model_moves() -> None:
    deviant = [
        *_CLEAN_VARIANTS,
        ["receive", "approve", "close"],  # skips "check" → model move on check
        ["receive", "check", "hack", "close"],  # inserts "hack" → log move on hack
    ]
    pq = _to_parquet(_sample_log(deviant))
    try:
        out = _conformance_worker(pq, str(_FIXTURE), "alignments", 1.0)
    finally:
        os.remove(pq)

    by_act = {a["activity"]: a for a in out["per_activity"]}
    # "hack" was inserted → a log move; "check" was skipped → a model move.
    assert by_act["hack"]["log_moves"] > 0
    assert by_act["check"]["model_moves"] > 0
    # A per-case row carries the classified move detail.
    assert out["per_case"]
    assert "log_moves" in out["per_case"][0]["detail"]


# ── module-level (caching + routes) ──────────────────────────────────────────


class _FakeEventLog:
    def __init__(self, df: pd.DataFrame, root: Path) -> None:
        self._df = df
        self.events_path = root / "events.parquet"

    async def __aenter__(self) -> _FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pandas(self) -> pd.DataFrame:
        return self._df

    async def materialize_parquet(self) -> tuple[str, bool]:
        return _to_parquet(self._df), True

    async def duckdb_fetch(self, sql: str, params: object = None) -> list[tuple]:
        import duckdb

        con = duckdb.connect()
        con.register("events", self._df)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()


class _FakeCache:
    def __init__(self) -> None:
        self._store: dict[str, object] = {}
        self.set_calls: dict[str, int] = {}

    async def get(self, key: str) -> object:
        return self._store.get(key)

    async def set(self, key: str, value: object) -> None:
        self._store[key] = value
        self.set_calls[key] = self.set_calls.get(key, 0) + 1

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeConfig:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def get(self, key: str, default: object = None) -> object:
        return self.value.get(key, default)


class _FakeProgress:
    async def update(self, *args: object, **kwargs: object) -> None:
        return None


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, topic: str, payload: object) -> None:
        self.events.append((topic, payload))


class _FakeLogger:
    def info(self, *a: object, **k: object) -> None: ...
    def warning(self, *a: object, **k: object) -> None: ...
    def exception(self, *a: object, **k: object) -> None: ...


class _FakeCtx:
    def __init__(self, df: pd.DataFrame, root: Path, config: dict[str, object]) -> None:
        self.log_id = "log-test"
        self.module_id = "conformance"
        self.user_id = "user-1"
        self.event_log = _FakeEventLog(df, root)
        self.cache = _FakeCache()
        self.config = _FakeConfig(config)
        self.progress = _FakeProgress()
        self.bus = _FakeBus()
        self.logger = _FakeLogger()
        self.run_calls = 0

    async def run_in_process(self, fn: object, *args: object, **kwargs: object) -> object:
        self.run_calls += 1
        return fn(*args, **kwargs)  # type: ignore[operator]


class _CrashPrecisionCtx(_FakeCtx):
    """run_in_process that runs the core worker normally but simulates the
    precision offload child being OOM-killed - the exact production failure
    ("offload process exited without returning a result")."""

    async def run_in_process(self, fn: object, *args: object, **kwargs: object) -> object:
        self.run_calls += 1
        if getattr(fn, "__name__", "") == "_precision_token_worker":
            raise RuntimeError("offload process exited without returning a result")
        return fn(*args, **kwargs)  # type: ignore[operator]


def _ctx_with_model(
    tmp_path: Path,
    df: pd.DataFrame,
    config: dict[str, object],
    ctx_cls: type[_FakeCtx] = _FakeCtx,
) -> _FakeCtx:
    models_dir = tmp_path / "conformance_models"
    models_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(_FIXTURE, models_dir / "reference.bpmn")
    (models_dir / "_index.json").write_text(
        json.dumps({"active": "reference.bpmn", "models": [{"name": "reference.bpmn"}]})
    )
    return ctx_cls(df, tmp_path, config)


def test_run_then_results_served_from_cache(tmp_path: Path) -> None:
    module = ConformanceModule()
    ctx = _ctx_with_model(tmp_path, _sample_log(_CLEAN_VARIANTS), {"technique": "token_replay"})

    run_out = asyncio.run(module.run(ctx))  # type: ignore[arg-type]
    assert run_out["ran"] is True
    assert run_out["kpis"]["log_fitness"] == 1.0
    # Two offloads now: the core worker, then the isolated token-precision pass.
    assert ctx.run_calls == 2
    # The run emitted a tenant-scoped bus event.
    assert ctx.bus.events and ctx.bus.events[0][0] == "conformance.computed"
    assert ctx.bus.events[0][1]["user_id"] == "user-1"

    results = asyncio.run(module.results(ctx))  # type: ignore[arg-type]
    assert results["ran"] is True
    assert results["kpis"]["log_fitness"] == 1.0
    # results() only reads the cache → no extra compute.
    assert ctx.run_calls == 2


def test_results_without_model_reports_no_model(tmp_path: Path) -> None:
    # No conformance_models dir → no active model.
    ctx = _FakeCtx(_sample_log(_CLEAN_VARIANTS), tmp_path, {"technique": "token_replay"})
    out = asyncio.run(ctx_results(ctx))
    assert out["ran"] is False
    assert out["has_model"] is False


async def ctx_results(ctx: _FakeCtx) -> dict:
    return await ConformanceModule().results(ctx)  # type: ignore[arg-type]


def test_fitness_route_reads_cached_kpis(tmp_path: Path) -> None:
    module = ConformanceModule()
    ctx = _ctx_with_model(tmp_path, _sample_log(_CLEAN_VARIANTS), {"technique": "token_replay"})
    asyncio.run(module.run(ctx))  # type: ignore[arg-type]

    fit = asyncio.run(module.fitness(ctx))  # type: ignore[arg-type]
    assert fit["log_fitness"] == 1.0
    assert fit["technique"] == "token_replay"

    cap = asyncio.run(module.compute_fitness(ctx))  # type: ignore[arg-type]
    assert cap == 1.0


def test_token_precision_present_within_budget(tmp_path: Path) -> None:
    # Default budget keeps precision on a small log: the isolated precision
    # offload runs and its value lands in the cached KPIs.
    module = ConformanceModule()
    ctx = _ctx_with_model(tmp_path, _sample_log(_CLEAN_VARIANTS), {"technique": "token_replay"})
    asyncio.run(module.run(ctx))  # type: ignore[arg-type]
    results = asyncio.run(module.results(ctx))  # type: ignore[arg-type]
    assert results["kpis"]["precision"] is not None
    assert results["precision_skipped"] is None


def test_token_precision_skipped_over_event_budget(tmp_path: Path) -> None:
    # A 5-event budget short-circuits before the precision offload is even spawned;
    # fitness + deviations are still computed and a note explains the skip.
    module = ConformanceModule()
    ctx = _ctx_with_model(
        tmp_path,
        _sample_log(_CLEAN_VARIANTS),
        {"technique": "token_replay", "precision_max_events": 5},
    )
    asyncio.run(module.run(ctx))  # type: ignore[arg-type]
    # Only the core worker ran - the budget gate skips the precision offload.
    assert ctx.run_calls == 1
    results = asyncio.run(module.results(ctx))  # type: ignore[arg-type]
    assert results["kpis"]["precision"] is None
    assert results["precision_skipped"] and "events" in results["precision_skipped"]
    assert results["kpis"]["log_fitness"] == 1.0
    assert results["kpis"]["total_deviations"] == 0


def test_token_precision_offload_crash_degrades_gracefully(tmp_path: Path) -> None:
    # The regression: when the precision offload child is OOM-killed, the run must
    # STILL succeed and cache fitness + deviations (the old single-offload crash
    # cached nothing → the panel sat on an empty "run" state).
    module = ConformanceModule()
    ctx = _ctx_with_model(
        tmp_path,
        _sample_log(_CLEAN_VARIANTS),
        {"technique": "token_replay"},
        ctx_cls=_CrashPrecisionCtx,
    )
    run_out = asyncio.run(module.run(ctx))  # type: ignore[arg-type]
    assert run_out["ran"] is True

    results = asyncio.run(module.results(ctx))  # type: ignore[arg-type]
    assert results["ran"] is True
    # Precision degraded to None + a note; fitness + deviations are intact.
    assert results["kpis"]["precision"] is None
    assert results["precision_skipped"] and "memory" in results["precision_skipped"].lower()
    assert results["kpis"]["log_fitness"] == 1.0
    assert results["kpis"]["total_deviations"] == 0
