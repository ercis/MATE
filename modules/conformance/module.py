"""Conformance Checking - replay a log against an uploaded reference BPMN.

Routes (mounted at /api/v1/modules/conformance):
  GET    /model              → active reference BPMN as {kind:"bpmn", xml, name}
  GET    /models             → list uploaded reference models
  POST   /model              → upload one reference BPMN (multipart), make it active
  DELETE /model/{name}       → remove a reference model
  POST   /run                → replay the log against the active model (job)
  GET    /results            → cached run payload for the active model + technique
  GET    /fitness            → thin {log_fitness, precision, …} cross-module read

There is no `log.imported` precompute: a reference model only exists after the
user uploads one, so conformance is on-demand. The run job therefore never gates
the log's processing→ready transition.

The reference model is stored per-(user, log) on disk next to the log's
events.parquet - it is specific to one log, unlike cv4cdd's platform-shared
`model_store`. Results are cached per (model_hash, technique) so swapping the
model or the technique recomputes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from mate.sdk import Module, ModuleContext, job, route

from .conformance import _conformance_worker, _precision_token_worker, validate_bpmn_file

MODELS_SUBDIR = "conformance_models"
_VALID_SUFFIXES = {".bpmn", ".xml"}


# ── per-(user, log) model storage ────────────────────────────────────────────


def _models_dir(events_path: Path) -> Path:
    return events_path.parent / MODELS_SUBDIR


def _index_path(models_dir: Path) -> Path:
    return models_dir / "_index.json"


def _read_index(models_dir: Path) -> dict[str, Any]:
    """Durable sidecar of {active, models:[{name, uploaded_at}]}.

    Lives on disk (not ctx.cache) so the active-model pointer survives the
    cache invalidation that fires on re-import / config change.
    """
    p = _index_path(models_dir)
    if not p.exists():
        return {"active": None, "models": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("active", None)
            data.setdefault("models", [])
            return data
    except (OSError, ValueError):
        pass
    return {"active": None, "models": []}


def _write_index(models_dir: Path, index: dict[str, Any]) -> None:
    _index_path(models_dir).write_text(json.dumps(index, indent=2), encoding="utf-8")


def _safe_name(filename: str) -> str:
    base = Path(filename).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "model.bpmn"
    return cleaned


def _upsert_index(models_dir: Path, name: str) -> None:
    index = _read_index(models_dir)
    models = [m for m in index.get("models", []) if m.get("name") != name]
    models.append({"name": name, "uploaded_at": datetime.now(UTC).isoformat()})
    _write_index(models_dir, {"active": name, "models": models})


def _remove_from_index(models_dir: Path, name: str) -> str | None:
    index = _read_index(models_dir)
    models = [m for m in index.get("models", []) if m.get("name") != name]
    active = index.get("active")
    if active == name:
        active = models[-1]["name"] if models else None
    _write_index(models_dir, {"active": active, "models": models})
    return active


def _hash_file(path: Path) -> str:
    return hashlib.blake2b(path.read_bytes(), digest_size=8).hexdigest()


def _results_key(model_hash: str, technique: str) -> str:
    return f"results__{model_hash}__{technique}"


def _norm_technique(value: Any) -> str:
    """Only two techniques exist; anything else falls back to token replay."""
    return "alignments" if value == "alignments" else "token_replay"


# ── process-pool offload (mirrors discovery) ─────────────────────────────────


async def _offload(ctx: ModuleContext, worker: Any, *args: Any) -> Any:
    """Run ``worker(parquet_path, *args)`` on a pool core, handing it the current
    (filtered) view as a Parquet path and removing any temp file afterwards.

    Returns whatever the worker returns - a result dict for ``_conformance_worker``,
    a bare float for ``_precision_token_worker`` - hence the ``Any`` return."""
    async with ctx.event_log as log:
        path, is_temp = await log.materialize_parquet()
    try:
        return await ctx.run_in_process(worker, path, *args)
    finally:
        if is_temp:
            await asyncio.to_thread(os.remove, path)


class ConformanceModule(Module):
    id = "conformance"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting conformance-checking "
        "results: fitness, precision and where the event log deviates from an "
        "uploaded reference BPMN. Distinguish genuine deviations from "
        "activity-label mismatches between the model and the log."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        data = await self._cached_results(ctx)
        if data is None:
            return None
        return {"kpis": data.get("kpis"), "label_report": data.get("label_report")}

    # ── reference model storage ──────────────────────────────────────────────

    @route.get("/models")
    async def list_models(self, ctx: ModuleContext) -> dict[str, Any]:
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        index = _read_index(models_dir)
        active = index.get("active")
        out: list[dict[str, Any]] = []
        for m in index.get("models", []):
            name = m.get("name")
            p = models_dir / name
            out.append(
                {
                    "name": name,
                    "size_bytes": p.stat().st_size if p.exists() else 0,
                    "uploaded_at": m.get("uploaded_at"),
                    "active": name == active,
                }
            )
        return {"models": out, "active": active}

    @route.get("/model")
    async def get_model(self, ctx: ModuleContext) -> dict[str, Any]:
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        active = _read_index(models_dir).get("active")
        if not active:
            raise HTTPException(status_code=404, detail="No reference model uploaded yet.")
        p = models_dir / active
        if not p.exists():
            raise HTTPException(status_code=404, detail="Reference model file is missing.")
        return {
            "kind": "bpmn",
            "version": 1,
            "name": active,
            "xml": p.read_text(encoding="utf-8"),
        }

    @route.post("/model")
    async def upload_model(self, ctx: ModuleContext, file: UploadFile) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=422, detail="filename missing")
        if Path(file.filename).suffix.lower() not in _VALID_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail="Upload a BPMN 2.0 file with a .bpmn or .xml extension.",
            )
        name = _safe_name(file.filename)

        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        models_dir.mkdir(parents=True, exist_ok=True)
        dest = models_dir / name
        dest.write_bytes(await file.read())

        # Validate it parses + yields a Petri net before we keep it.
        try:
            n_tasks = await asyncio.to_thread(validate_bpmn_file, str(dest))
        except Exception as exc:  # pm4py raises a variety of parse errors
            dest.unlink(missing_ok=True)
            ctx.logger.warning("conformance.bpmn_invalid", name=name, error=str(exc))
            raise HTTPException(
                status_code=422, detail=f"Not a readable BPMN model: {exc}"
            ) from exc
        if n_tasks == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail="This BPMN has no labelled tasks to check the log against.",
            )

        _upsert_index(models_dir, name)
        ctx.logger.info("conformance.model_uploaded", name=name, tasks=n_tasks)
        return {"name": name, "size_bytes": dest.stat().st_size, "tasks": n_tasks}

    @route.delete("/model/{name}")
    async def delete_model(self, ctx: ModuleContext, name: str) -> dict[str, Any]:
        safe = _safe_name(name)
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        p = models_dir / safe
        if p.exists():
            p.unlink()
        new_active = _remove_from_index(models_dir, safe)
        return {"deleted": safe, "active": new_active}

    @route.post("/model/{name}/activate")
    async def activate_model(self, ctx: ModuleContext, name: str) -> dict[str, Any]:
        safe = _safe_name(name)
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        if not (models_dir / safe).exists():
            raise HTTPException(status_code=404, detail="Reference model not found.")
        index = _read_index(models_dir)
        if not any(m.get("name") == safe for m in index.get("models", [])):
            raise HTTPException(status_code=404, detail="Reference model not in index.")
        index["active"] = safe
        _write_index(models_dir, index)
        return {"active": safe}

    # ── run + results ────────────────────────────────────────────────────────

    @route.post("/run")
    @job(progress=True, title="Conformance - replay")
    async def run(self, ctx: ModuleContext, *, technique: str | None = None) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        technique = _norm_technique(technique or cfg.get("technique"))
        threshold = float(cfg.get("conforming_fitness_threshold", 1.0))

        await ctx.progress.update(0.0, "Loading reference model")
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        active = _read_index(models_dir).get("active")
        if not active:
            raise HTTPException(status_code=404, detail="Upload a reference BPMN first.")
        bpmn_path = models_dir / active
        if not bpmn_path.exists():
            raise HTTPException(status_code=404, detail="Reference model file is missing.")
        model_hash = _hash_file(bpmn_path)

        if technique == "alignments":
            await self._guard_alignments_size(ctx, cfg)

        await ctx.progress.update(
            0.15, f"Replaying log ({technique.replace('_', ' ')}) - may take a few minutes"
        )
        result = await _offload(ctx, _conformance_worker, str(bpmn_path), technique, threshold)
        n_events = int(result.pop("_n_events", 0))

        # Token-replay precision is the OOM-prone ETConformance pass: run it in its
        # OWN offload so a child death degrades to precision="-" instead of taking
        # the whole run down with it (its old in-worker crash cached nothing → the
        # panel showed an empty "run" state). Alignments precision is computed
        # inline by the worker (bounded by `_guard_alignments_size`).
        if technique != "alignments":
            await ctx.progress.update(0.6, "Computing precision")
            prec, note = await self._token_precision(ctx, bpmn_path, n_events, cfg)
            result["kpis"]["precision"] = prec
            result["precision_skipped"] = note

        result["model_hash"] = model_hash
        result["model_name"] = active

        await ctx.progress.update(0.97, "Saving results")
        await ctx.cache.set(_results_key(model_hash, technique), result)
        await ctx.bus.emit(
            "conformance.computed",
            {
                "user_id": ctx.user_id,
                "log_id": ctx.log_id,
                "model_hash": model_hash,
                "technique": technique,
                "log_fitness": result["kpis"]["log_fitness"],
                "precision": result["kpis"]["precision"],
            },
        )
        await ctx.progress.update(1.0, "Done")
        return {
            "ran": True,
            "model_hash": model_hash,
            "technique": technique,
            "kpis": result["kpis"],
        }

    async def _token_precision(
        self, ctx: ModuleContext, bpmn_path: Path, n_events: int, cfg: dict[str, Any]
    ) -> tuple[float | None, str | None]:
        """Token-replay precision in a crash-isolated offload.

        Returns ``(precision, skipped_note)``. Precision is ``None`` (with a
        human-readable note) when the log is over the event budget, the offload
        child dies (OOM - the historical "offload process exited without returning
        a result" failure), or pm4py hands back a non-finite value. Never raises:
        precision is best-effort, the run must still ship fitness + deviations.
        """
        precision_max_events = int(cfg.get("precision_max_events", 150_000))
        if precision_max_events and n_events > precision_max_events:
            return None, (
                f"Precision skipped: this log has {n_events:,} events "
                f"(limit {precision_max_events:,}). Fitness and deviations are unaffected. "
                "Raise the precision event limit in this module's settings to compute it anyway."
            )
        try:
            prec = await _offload(ctx, _precision_token_worker, str(bpmn_path))
        except Exception:
            # Includes the offload child being OOM-killed mid-precision: its pipe
            # closes and the runtime raises rather than returning a result. Degrade
            # instead of failing the whole run (which would cache nothing).
            ctx.logger.warning("conformance.precision_offload_failed", exc_info=True)
            return None, (
                "Precision could not be computed: the calculation exceeded available "
                "memory and was stopped. Fitness and deviations are unaffected - lower the "
                "precision event limit in this module's settings to skip it sooner."
            )
        if prec is None or not math.isfinite(float(prec)):
            return None, (
                "Precision could not be computed for this model. "
                "Fitness and deviations are unaffected."
            )
        return round(float(prec), 4), None

    async def _guard_alignments_size(self, ctx: ModuleContext, cfg: dict[str, Any]) -> None:
        """Refuse alignments on inputs large enough to hang. Counted in DuckDB
        (GIL-free) before the pool offload so the 413 is raised synchronously."""
        max_cases = int(cfg.get("alignments_max_cases", 2000))
        max_variants = int(cfg.get("alignments_max_variants", 300))
        async with ctx.event_log as log:
            rows = await log.duckdb_fetch(
                "SELECT COUNT(*) AS n_cases, COUNT(DISTINCT v) AS n_variants FROM ("
                "  SELECT case_id, string_agg(activity, '>' ORDER BY timestamp) AS v"
                "  FROM events GROUP BY case_id"
                ")"
            )
        n_cases = int(rows[0][0]) if rows and rows[0][0] is not None else 0
        n_variants = int(rows[0][1]) if rows and rows[0][1] is not None else 0
        if n_cases > max_cases or n_variants > max_variants:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Alignments refused: log has {n_cases} cases and {n_variants} "
                    f"variants (limits: {max_cases} cases, {max_variants} variants). "
                    "Switch the technique to token-based replay, or raise the limits "
                    "in this module's settings."
                ),
            )

    async def _cached_results(
        self, ctx: ModuleContext, *, technique: str | None = None
    ) -> dict[str, Any] | None:
        cfg = ctx.config.value or {}
        technique = _norm_technique(technique or cfg.get("technique"))
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        active = _read_index(models_dir).get("active")
        if not active:
            return None
        bpmn_path = models_dir / active
        if not bpmn_path.exists():
            return None
        key = _results_key(_hash_file(bpmn_path), technique)
        if await ctx.cache.exists(key):
            cached = await ctx.cache.get(key)
            if isinstance(cached, dict):
                return cached
        return None

    @route.get("/results")
    async def results(self, ctx: ModuleContext, *, technique: str | None = None) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        technique = _norm_technique(technique or cfg.get("technique"))
        async with ctx.event_log as log:
            models_dir = _models_dir(log.events_path)
        active = _read_index(models_dir).get("active")
        if not active or not (models_dir / active).exists():
            return {"kind": "conformance", "ran": False, "has_model": False, "technique": technique}
        cached = await self._cached_results(ctx, technique=technique)
        if cached is not None:
            return {"ran": True, "has_model": True, **cached}
        return {
            "kind": "conformance",
            "ran": False,
            "has_model": True,
            "model_name": active,
            "technique": technique,
        }

    @route.get("/fitness")
    async def fitness(self, ctx: ModuleContext, *, technique: str | None = None) -> dict[str, Any]:
        """Thin KPI read other modules can call over HTTP today (the capability
        registry can't bind `conformance.compute_fitness` to a handler yet)."""
        cached = await self._cached_results(ctx, technique=technique)
        if cached is None:
            raise HTTPException(status_code=404, detail="No conformance results yet.")
        k = cached["kpis"]
        return {
            "log_fitness": k["log_fitness"],
            "precision": k["precision"],
            "perc_fit_traces": k["perc_fit_traces"],
            "technique": cached.get("technique"),
        }

    async def compute_fitness(self, ctx: ModuleContext, log_id: str | None = None) -> float | None:
        """Capability handler, ready for when the loader binds capability names.

        Returns the cached log fitness, or None if no run exists yet.
        """
        cached = await self._cached_results(ctx)
        if cached is None:
            return None
        return float(cached["kpis"]["log_fitness"])
