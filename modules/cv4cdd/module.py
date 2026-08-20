"""CV4CDD - Computer-vision based concept-drift detection.

Wraps the WINSIM pipeline from Kraus & van der Aa (BPM'24) so the platform
can run it against any imported event log. The heavy work (similarity
matrix + TF inference) is wrapped in a ``@job`` so the user gets a
progress toast / dock entry while it runs.

The same job also auto-fires on ``log.imported`` so a freshly-imported
log is analysed without any extra click.

Routes:
  POST /detect        - kick off the detection (returns ``{"job_id": "..."}``)
  GET  /results       - fetch the cached detections JSON
  GET  /image         - fetch the overlay PNG (used by the panel <img>)
  GET  /similarity    - fetch the raw similarity-matrix PNG (no overlay)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

# ── Warm heavy imports on the loader's main thread ─────────────────────────────
#
# Several modules auto-run jobs on `log.imported` and lazily `import pm4py` from
# inside `asyncio.to_thread` worker threads. cv4cdd is the only one that reaches
# pm4py through *deep* submodules (pm4py.algo.filtering.*, pm4py.objects.*) while
# the others enter via a plain top-level `import pm4py`. When two of those jobs
# run at once, two worker threads import overlapping pm4py graphs through
# different entry points in different orders, which trips CPython's per-module
# import-lock deadlock detector ("deadlock detected by _ModuleLock('pm4py.algo
# .filtering')") and fails the job.
#
# Importing the exact submodules here - sequentially, on the loader's main thread
# at module-load time - guarantees they are already in sys.modules before any
# worker thread touches them, so the in-thread imports below are just cache hits
# and cv4cdd can never be a party to the race. TensorFlow is deliberately left
# lazy (it's ~0.5 GB and only needed on an actual run); its import is serialised
# separately in cv4cdd_core.
import pm4py  # noqa: F401
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response
from pm4py.algo.filtering.log.attributes import (  # noqa: F401
    attributes_filter as _warm_attributes_filter,
)
from pm4py.objects.conversion.log import converter as _warm_log_converter  # noqa: F401
from pm4py.objects.log.importer.xes import importer as _warm_xes_importer  # noqa: F401
from pm4py.objects.log.util import dataframe_utils as _warm_dataframe_utils  # noqa: F401

from mate.sdk import Module, ModuleContext, job, on_event, route

from . import cv4cdd_core as _warm_cv4cdd_core  # noqa: F401

# ── Platform-shared model store ────────────────────────────────────────────────
#
# Pretrained CV4CDD models are large (~0.5 GB) and are NOT committed to git.
# They're uploaded at runtime (POST /models) and extracted here. Because module
# code runs once per process shared across every user, anything that lands on
# this directory is automatically available platform-wide - user A uploads a
# model and user B can select it. Each account records *which* model it uses in
# its own module config (config_json["model"]); the files are shared.
MODEL_ROOT = Path(__file__).parent / "model"

# The snapshot the module historically shipped. Used as the implicit default
# when a user hasn't picked a model and this folder happens to be present.
_LEGACY_DEFAULT_MODEL = "20240922-233643_winsim_sgd_model_4d_v1"

# Folder/upload names are restricted to this character set to keep them safe as
# on-disk directory names and immune to path-traversal.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Suffixes we strip to derive a model name from an uploaded archive's filename.
_ARCHIVE_SUFFIXES = (".tar.zst", ".tar.zstd", ".tzst", ".tar")


def _is_model_dir(path: Path) -> bool:
    """A directory is a usable model iff it holds a TF SavedModel."""
    return path.is_dir() and (path / "saved_model.pb").exists()


def _list_models() -> list[str]:
    """Names of every installed model, sorted. Platform-wide (not per-user)."""
    if not MODEL_ROOT.is_dir():
        return []
    return sorted(
        p.name
        for p in MODEL_ROOT.iterdir()
        if not p.name.startswith(".") and _is_model_dir(p)
    )


def _resolve_model_dir(cfg: dict[str, Any]) -> Path | None:
    """Pick the model directory this invocation should use.

    Preference order: the account's configured ``model``, then the legacy
    default if it's on disk, then the first installed model (alphabetical).
    Returns ``None`` when no model has been uploaded yet.
    """
    available = _list_models()
    if not available:
        return None
    chosen = cfg.get("model")
    if isinstance(chosen, str) and chosen in available:
        return MODEL_ROOT / chosen
    if _LEGACY_DEFAULT_MODEL in available:
        return MODEL_ROOT / _LEGACY_DEFAULT_MODEL
    return MODEL_ROOT / available[0]


def _model_name_from_filename(filename: str) -> str:
    """Derive a safe model folder name from an uploaded archive's filename."""
    base = Path(filename).name
    lowered = base.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return _SAFE_NAME_RE.sub("-", base).strip("-. ")


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            with contextlib.suppress(OSError):
                total += p.stat().st_size
    return total


def _extract_tar_zst(archive: Path, dest: Path) -> None:
    """Stream-decompress a .tar.zst archive into ``dest`` (path-traversal safe)."""
    import zstandard

    dctx = zstandard.ZstdDecompressor()
    # mode="r|" is a non-seekable streaming reader - matches the zstd stream.
    # filter="data" (Python 3.12) blocks absolute paths and `..` traversal.
    with (
        archive.open("rb") as fh,
        dctx.stream_reader(fh) as reader,
        tarfile.open(fileobj=reader, mode="r|") as tar,
    ):
        tar.extractall(dest, filter="data")


def _find_model_root(root: Path) -> Path | None:
    """Return the directory holding saved_model.pb (archives often wrap it)."""
    if (root / "saved_model.pb").exists():
        return root
    for hit in root.rglob("saved_model.pb"):
        return hit.parent
    return None


class Cv4cddModule(Module):
    id = "cv4cdd"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting concept-drift detections "
        "in an event log. Each drift has a type (sudden/gradual/incremental/"
        "recurring), a confidence score, and a time window. Explain what kind "
        "of change the model is signalling, flag low-confidence detections as "
        "uncertain, and suggest where the analyst should investigate first."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        if not await ctx.cache.exists("detections"):
            return None
        detections = await ctx.cache.get("detections")
        if not isinstance(detections, dict):
            return None
        return {
            "drifts": detections.get("drifts", []),
            "n_windows": detections.get("n_windows"),
            "confidence_threshold": detections.get("confidence_threshold"),
        }

    # ── Triggers ──────────────────────────────────────────────────────────────

    @on_event("log.imported")
    @job(progress=True, title="CV4CDD - auto-detecting drifts")
    async def on_log_imported(
        self, ctx: ModuleContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Auto-run detection right after a log finishes importing.

        The loader stacks this as a job so the user sees a progress toast
        and can cancel it from the dock if it's not wanted. When no model is
        available ``_do_detect`` raises and the job fails loudly - a missing
        model is a misconfiguration the admin/user must resolve, not a silent
        no-op.
        """
        return await self._do_detect(ctx)

    @route.post("/detect")
    @job(progress=True, title="CV4CDD - concept-drift detection")
    async def detect(self, ctx: ModuleContext) -> dict[str, Any]:
        return await self._do_detect(ctx)

    # ── Core ─────────────────────────────────────────────────────────────────

    async def _do_detect(self, ctx: ModuleContext) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        model_dir = _resolve_model_dir(cfg)
        if model_dir is None:
            # Both entry points (route /detect and the log.imported autodetect)
            # run as jobs, so this surfaces as a failed job with this message -
            # not an HTTP error. An admin-pinned global model also flows through
            # cfg["model"]; reaching here means none is installed at all.
            raise RuntimeError(
                "No CV4CDD detection model is available. An administrator can pin a "
                "shared model under Admin → Controls, or upload one (.tar.zst) on the "
                "module's settings page."
            )

        n_windows = int(cfg.get("n_windows", 200))
        threshold = float(cfg.get("confidence_threshold", 0.5))

        await ctx.progress.update(0.0, "Loading event log")
        df = await self._load_sorted_df(ctx)

        # Capture the running loop here on the main thread so the worker
        # thread can marshal progress callbacks back through it - calling
        # `asyncio.get_event_loop()` from inside `to_thread` raises since
        # the thread-pool thread doesn't own a loop.
        loop = asyncio.get_running_loop()

        result = await asyncio.to_thread(
            self._run_sync, df, model_dir, n_windows, threshold, ctx, loop
        )

        await ctx.progress.update(0.97, "Saving results")
        await ctx.cache.set(
            "detections",
            {
                "kind": "cv4cdd_detections",
                "drifts": result["drifts"],
                "n_windows": result["n_windows"],
                "confidence_threshold": threshold,
            },
        )
        await ctx.cache.set("overlay", result["overlay_png"])
        await ctx.cache.set("similarity", result["similarity_png"])

        await ctx.progress.update(1.0, "Done")
        return {
            "kind": "cv4cdd_detections",
            "drifts": result["drifts"],
            "n_windows": result["n_windows"],
        }

    async def _load_sorted_df(self, ctx: ModuleContext) -> Any:
        """Return a DataFrame sorted so that traces appear in pm4py TIMESTAMP_SORT
        order - exactly matching the reference pipeline.

        The platform stores events.parquet sorted by (case_id, timestamp), which
        gives alphabetical trace ordering for same-timestamp ties.  The reference
        uses pm4py's XES importer with TIMESTAMP_SORT=True, which preserves the
        original XES file order for ties.  For logs with many traces starting at
        the same placeholder timestamp (e.g. midnight) this produces different
        window assignments.

        When the original XES file is still on disk we re-import it via pm4py to
        recover the exact ordering.  For CSV logs (no XES file) we fall back to
        the Parquet and sort by (start_timestamp, case_id) - consistent and
        reproducible, though it may differ from the reference for tied timestamps.
        """
        async with ctx.event_log as log:
            # events_path is public; derive the log root from it.
            log_root = log.events_path.parent
            meta_path = log_root / "meta.json"

            source_format: str = ""
            if meta_path.exists():
                try:
                    source_format = json.loads(meta_path.read_text()).get(
                        "source_format", ""
                    )
                except Exception:
                    pass

            # Try to load via pm4py when the original XES file is present.
            for ext in ([source_format] if source_format else []) + ["xes", "xes.gz"]:
                original = log_root / f"original.{ext}"
                if original.exists() and ext in {"xes", "xes.gz"}:
                    return await asyncio.to_thread(self._load_xes_df, original)

            # Fallback: read Parquet and apply a deterministic trace sort.
            df = await log.pandas()

        # Sort events by timestamp (mergesort keeps Parquet row order for ties,
        # which is alphabetical case_id - reproducible even if not identical to
        # the reference's XES file order).
        return df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    @staticmethod
    def _load_xes_df(xes_path: Path) -> Any:
        """Load an XES file via pm4py with TIMESTAMP_SORT=True.

        This replicates the reference pipeline's import step exactly, giving
        the same trace order as the standalone cv4cdd tool.
        """
        import pandas as pd
        from pm4py.algo.filtering.log.attributes import attributes_filter
        from pm4py.objects.conversion.log import converter as log_converter
        from pm4py.objects.log.importer.xes import importer as xes_importer
        from pm4py.objects.log.util import dataframe_utils

        variant = xes_importer.Variants.ITERPARSE
        parameters = {
            variant.value.Parameters.TIMESTAMP_SORT: True,
            variant.value.Parameters.SHOW_PROGRESS_BAR: False,
        }
        event_log = xes_importer.apply(
            str(xes_path), variant=variant, parameters=parameters
        )

        # Mirror the reference's filter_complete_events (no-op when the log
        # has no lifecycle:transition attribute).
        try:
            event_log = attributes_filter.apply_events(
                event_log,
                ["complete", "COMPLETE"],
                parameters={
                    attributes_filter.Parameters.ATTRIBUTE_KEY: "lifecycle:transition",
                    attributes_filter.Parameters.POSITIVE: True,
                },
            )
        except Exception:
            pass

        df = log_converter.apply(event_log, variant=log_converter.Variants.TO_DATA_FRAME)
        df = dataframe_utils.convert_timestamp_columns_in_df(df, timest_format="ISO8601")

        return df.rename(
            columns={
                "case:concept:name": "case_id",
                "concept:name": "activity",
                "time:timestamp": "timestamp",
            }
        )[["case_id", "activity", "timestamp"]].copy()

    def _run_sync(
        self,
        df: Any,
        model_dir: Path,
        n_windows: int,
        threshold: float,
        ctx: ModuleContext,
        loop: asyncio.AbstractEventLoop,
    ) -> dict[str, Any]:
        from . import cv4cdd_core

        def progress(fraction: float, message: str) -> None:
            # Fire-and-forget on the main loop; we don't await so the worker
            # thread isn't blocked on the WebSocket write.
            try:
                asyncio.run_coroutine_threadsafe(
                    ctx.progress.update(fraction, message), loop
                )
            except RuntimeError:
                # Loop is closed (shutdown in progress) - drop the update.
                pass

        return cv4cdd_core.run_detection(
            df=df,
            model_path=model_dir,
            n_windows=n_windows,
            threshold=threshold,
            progress=progress,
        )

    # ── Read-only routes ─────────────────────────────────────────────────────

    @route.get("/results")
    async def results(self, ctx: ModuleContext) -> dict[str, Any]:
        cached = await ctx.cache.get("detections")
        if cached is None:
            return {
                "kind": "cv4cdd_detections",
                "drifts": [],
                "n_windows": 0,
                "ran": False,
            }
        return {**cached, "ran": True}

    @route.get("/image")
    async def image(self, ctx: ModuleContext) -> Response:
        png = await ctx.cache.get("overlay")
        if png is None:
            raise HTTPException(
                status_code=404,
                detail="No detection has been run yet. POST /detect first.",
            )
        return Response(content=png, media_type="image/png")

    @route.get("/similarity")
    async def similarity(self, ctx: ModuleContext) -> Response:
        png = await ctx.cache.get("similarity")
        if png is None:
            raise HTTPException(
                status_code=404,
                detail="No detection has been run yet.",
            )
        return Response(content=png, media_type="image/png")

    # ── Model store (platform-wide) ──────────────────────────────────────────
    #
    # Models live on shared on-disk storage (MODEL_ROOT), so list/upload/delete
    # operate platform-wide. Only the *selection* - which model this account
    # uses - is per-user, stored in module config under "model" and saved via
    # the platform's standard PUT /modules/{id}/config from the settings page.

    @route.get("/models")
    async def list_models(self, ctx: ModuleContext) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        # When an admin pins the model platform-wide the loader injects the
        # shared choice into cfg["model"] + this sentinel; the per-user picker
        # then renders read-only ("administrator-controlled").
        locked = bool(cfg.get("__model_admin_locked__"))
        raw_selected = cfg.get("model")
        selected = raw_selected if isinstance(raw_selected, str) else None
        resolved = _resolve_model_dir(cfg)
        active = resolved.name if resolved else None
        models = [
            {
                "name": name,
                "size_bytes": _dir_size(MODEL_ROOT / name),
                # The account's explicit choice (may differ from `active` when
                # the chosen model was deleted or none was ever picked).
                "selected": name == selected,
                # What detection would actually load right now.
                "active": name == active,
            }
            for name in _list_models()
        ]
        return {"models": models, "selected": selected, "active": active, "locked": locked}

    @route.post("/models")
    async def upload_model(self, ctx: ModuleContext, file: UploadFile) -> dict[str, Any]:
        name = _model_name_from_filename(file.filename or "")
        if not name:
            raise HTTPException(
                status_code=400,
                detail="Could not derive a model name from the uploaded filename.",
            )
        dest = MODEL_ROOT / name
        if _is_model_dir(dest):
            raise HTTPException(
                status_code=409,
                detail=f"A model named '{name}' already exists. Delete it first to replace it.",
            )

        MODEL_ROOT.mkdir(parents=True, exist_ok=True)
        archive: Path | None = None
        staging: Path | None = None
        try:
            # Stream the upload to a temp file - never hold ~0.5 GB in memory.
            fd, archive_path = tempfile.mkstemp(suffix=".tar.zst", dir=MODEL_ROOT)
            archive = Path(archive_path)
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)

            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=MODEL_ROOT))
            await asyncio.to_thread(_extract_tar_zst, archive, staging)

            model_src = _find_model_root(staging)
            if model_src is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Archive does not contain a TensorFlow SavedModel "
                        "(no saved_model.pb found inside)."
                    ),
                )
            await asyncio.to_thread(shutil.move, str(model_src), str(dest))
        except HTTPException:
            raise
        except Exception as exc:  # surface install failure to the UI
            ctx.logger.exception("cv4cdd.model_upload_failed", model=name)
            raise HTTPException(
                status_code=500, detail=f"Failed to install model: {exc}"
            ) from exc
        finally:
            await file.close()
            if archive and archive.exists():
                archive.unlink(missing_ok=True)
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        ctx.logger.info("cv4cdd.model_installed", model=name)
        return {"name": name, "size_bytes": _dir_size(dest)}

    @route.delete("/models/{name}")
    async def delete_model(self, ctx: ModuleContext, name: str) -> dict[str, Any]:
        if "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid model name.")
        target = MODEL_ROOT / name
        if not target.is_dir() or target.resolve().parent != MODEL_ROOT.resolve():
            raise HTTPException(status_code=404, detail=f"Model '{name}' not found.")
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        ctx.logger.info("cv4cdd.model_deleted", model=name)
        return {"deleted": name}
