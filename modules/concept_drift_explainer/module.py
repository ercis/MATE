"""Concept Drift Explainer - module entry point.

Routes:
  GET    /drifts                    → list adapted drifts from cv4cdd's cache
  GET    /documents                 → list per-log documents currently on disk
  POST   /documents                 → upload one document (multipart)
  DELETE /documents/{name}          → remove a document + its Pinecone vectors
  POST   /ingest                    → re-index all per-log documents (job)
  POST   /pinecone/recreate-index   → delete & recreate the Pinecone index
  POST   /explain                   → run the LangGraph pipeline for one drift (job)
  GET    /explanations              → list cached explanations for this log
  GET    /explanations/{drift_key}  → fetch one cached explanation
  POST   /chat                      → ask a follow-up question against a cached state
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel

from mate.sdk import Module, ModuleContext, job, on_event, route

from .agents.drift_agent import build_drift_record
from .graph.build_graph import build_graph
from .utils.embeddings import Embedder
from .utils.ingest_documents import (
    KB_NS,
    SUPPORTED_SUFFIXES,
    delete_document_vectors,
    get_timestamp_from_filename,
    process_context_files,
    process_glossary_file,
)
from .utils.llm import load_module_ai_clients
from .utils.pinecone_client import get_pinecone_index, recreate_index

MODULE_DIR = Path(__file__).resolve().parent
BPM_GLOSSARY_PATH = MODULE_DIR / "data" / "knowledge_base" / "bpm_glossary.csv"

DOCS_SUBDIR = "concept_drift_explainer_docs"
FILENAME_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def _docs_dir(events_path: Path) -> Path:
    return events_path.parent / DOCS_SUBDIR


def _ns_for(log_id: str) -> str:
    return f"cde-{log_id}"


def _namespace_vector_count(index, namespace: str) -> int:
    """Vectors upserted into ``namespace``, or -1 if the stat call fails.

    Used to tell "uploaded but never re-indexed" (namespace absent / 0 vectors)
    apart from "indexed, but nothing matched this drift". Returns -1 on any SDK
    error so a transient stats failure never blocks a legitimate explain.
    """
    try:
        stats = index.describe_index_stats()
    except Exception:
        return -1
    namespaces = getattr(stats, "namespaces", None)
    if namespaces is None and isinstance(stats, dict):
        namespaces = stats.get("namespaces")
    if not namespaces:
        return 0
    entry = namespaces.get(namespace)
    if entry is None:
        return 0
    count = getattr(entry, "vector_count", None)
    if count is None and isinstance(entry, dict):
        count = entry.get("vector_count")
    return int(count or 0)


def _drift_key(drift: dict) -> str:
    ts = drift.get("start_timestamp", "")
    try:
        unix = int(datetime.fromisoformat(ts).timestamp()) if ts else 0
    except ValueError:
        unix = 0
    return f"{unix}:{drift.get('type', 'unknown')}"


class AiCheckRequest(BaseModel):
    # Optional so the user can validate an unsaved key typed into the card.
    # Falls back to the saved ``cfg["ai"]["api_key"]`` when omitted.
    api_key: str | None = None


class ExplainRequest(BaseModel):
    drift_key: str


class ChatRequest(BaseModel):
    drift_key: str
    user_question: str


class ConceptDriftExplainerModule(Module):
    id = "concept_drift_explainer"

    guidance_system_prompt = (
        "You are a process-mining analyst surfacing concept-drift explanations. "
        "Each cached explanation links a detected drift to ranked, "
        "evidence-backed hypotheses synthesised from the user's enterprise "
        "documents. Cite the source_document for each cause and flag "
        "low-confidence causes as tentative."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        if not await ctx.cache.exists("explanations"):
            return None
        explanations = await ctx.cache.get("explanations")
        if not isinstance(explanations, dict):
            return None
        return {"explanations": explanations}

    # ── triggers ──────────────────────────────────────────────────────────────

    @on_event("cv4cdd.completed")
    async def on_cv4cdd_completed(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        """Cheap refresh hook - no LLM cost.

        The platform auto-emits `<module_id>.completed` when a module's precompute
        job succeeds, so this fires after cv4cdd finishes detecting drifts and the
        panel can show a fresh drift list without the user having to refresh. It is
        *not* a `@job` (no `progress`/`@job` stacked) so it never gates the log's
        `processing → ready` transition, and it intentionally does *not* trigger
        the explanation pipeline (which costs real money).
        """
        ctx.logger.info("cv4cdd.completed arrived; drift list will refresh on next poll")

    # ── drift listing ────────────────────────────────────────────────────────

    @route.get("/drifts")
    async def list_drifts(self, ctx: ModuleContext) -> dict[str, Any]:
        adapted, n_windows = await self._adapted_drifts(ctx)
        return {
            "kind": "cde_drifts",
            "drifts": adapted,
            "ran": bool(adapted),
            "n_windows": n_windows,
        }

    # ── document corpus ──────────────────────────────────────────────────────

    @route.get("/documents")
    async def list_docs(self, ctx: ModuleContext) -> dict[str, Any]:
        async with ctx.event_log as log:
            docs_dir = _docs_dir(log.events_path)

        if not docs_dir.exists():
            return {"documents": []}

        out: list[dict[str, Any]] = []
        for p in sorted(docs_dir.iterdir()):
            if not p.is_file():
                continue
            ts = get_timestamp_from_filename(p.name)
            out.append(
                {
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "timestamp": ts,
                    "indexable": ts is not None and p.suffix.lower() in SUPPORTED_SUFFIXES,
                }
            )
        return {"documents": out}

    @route.post("/documents")
    async def upload_doc(self, ctx: ModuleContext, file: UploadFile) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=422, detail="filename missing")
        if not FILENAME_PREFIX_RE.match(file.filename):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Filename must start with a YYYY-MM-DD_ date prefix "
                    "(used as the document timestamp for temporal retrieval)."
                ),
            )
        if Path(file.filename).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported extension. Supported: {sorted(SUPPORTED_SUFFIXES)}",
            )

        async with ctx.event_log as log:
            docs_dir = _docs_dir(log.events_path)

        docs_dir.mkdir(parents=True, exist_ok=True)
        target = docs_dir / file.filename
        target.write_bytes(await file.read())
        return {"name": file.filename, "size_bytes": target.stat().st_size}

    @route.delete("/documents/{name}")
    async def delete_doc(self, ctx: ModuleContext, name: str) -> dict[str, Any]:
        async with ctx.event_log as log:
            docs_dir = _docs_dir(log.events_path)
        target = docs_dir / name
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"{name} not found")
        target.unlink()

        cfg = ctx.config.value or {}
        try:
            index = get_pinecone_index(cfg)
            delete_document_vectors(index, namespace=_ns_for(ctx.log_id), source_document_name=name)
        except HTTPException:
            # Pinecone not configured - the file is gone, that's enough.
            pass
        return {"deleted": name}

    # ── ingestion ────────────────────────────────────────────────────────────

    @route.post("/ingest")
    @job(progress=True, title="CDE - indexing documents")
    async def ingest(self, ctx: ModuleContext) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        async with ctx.event_log as log:
            docs_dir = _docs_dir(log.events_path)

        if not docs_dir.exists() or not any(docs_dir.iterdir()):
            raise HTTPException(
                status_code=422,
                detail="Upload at least one document before re-indexing.",
            )

        clients = await load_module_ai_clients(cfg)
        index = get_pinecone_index(cfg)
        embedder = Embedder(clients.embeddings)

        await ctx.progress.update(0.0, "Indexing documents")

        files = [p for p in docs_dir.iterdir() if p.is_file()]
        loop = asyncio.get_running_loop()

        def progress(fraction: float, message: str) -> None:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(
                    ctx.progress.update(min(0.95, fraction * 0.9), message), loop
                )

        result = await asyncio.to_thread(
            process_context_files,
            files,
            index=index,
            embedder=embedder,
            vision_llm=clients.chat,
            namespace=_ns_for(ctx.log_id),
            cache_dir=ctx.workdir,
            progress=progress,
        )

        await ctx.progress.update(0.97, "Indexing glossary")
        glossary_vectors = await asyncio.to_thread(
            process_glossary_file,
            BPM_GLOSSARY_PATH,
            index=index,
            embedder=embedder,
            namespace=KB_NS,
        )
        result["glossary_terms"] = glossary_vectors

        await ctx.progress.update(1.0, "Done")
        return result

    # ── OpenAI key check ─────────────────────────────────────────────────────

    @route.post("/ai/check")
    async def ai_check(self, ctx: ModuleContext, body: AiCheckRequest) -> dict[str, Any]:
        """Validate the module's OpenAI key and list available models.

        The module keeps its AI config isolated from the platform's global
        Settings → AI, so the settings card calls this to verify the key and
        populate the chat / embedding model dropdowns.
        """
        cfg = ctx.config.value or {}
        ai = cfg.get("ai") or {}
        api_key = (body.api_key or "").strip() or (ai.get("api_key") or "").strip()
        if not api_key:
            raise HTTPException(
                status_code=422,
                detail="Enter an OpenAI API key before checking.",
            )

        def _list_models() -> list[str]:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            return sorted(m.id for m in client.models.list().data)

        try:
            models = await asyncio.to_thread(_list_models)
        except Exception as exc:
            # Broad on purpose: surface any OpenAI SDK error (auth, network,
            # rate-limit) back to the settings card as a 400 with its message.
            raise HTTPException(
                status_code=400,
                detail=f"OpenAI rejected the key: {exc}",
            ) from exc

        return {"ok": True, "models": models}

    @route.post("/pinecone/recreate-index")
    async def recreate_pinecone_index(self, ctx: ModuleContext) -> dict[str, Any]:
        cfg = ctx.config.value or {}
        # The AI card owns the embedding dimension this rebuild provisions, so an
        # admin lock on that card also blocks the destructive recreate. The
        # platform injects this sentinel into runtime config when the card is
        # locked (mirrors cv4cdd's __model_admin_locked__ read-only guard).
        if cfg.get("__ai_admin_locked__"):
            raise HTTPException(
                status_code=403,
                detail="AI models are administrator-controlled; index rebuild is disabled.",
            )
        result = await asyncio.to_thread(recreate_index, cfg)
        return {"ok": True, **result}

    # ── explanation pipeline ─────────────────────────────────────────────────

    @route.post("/explain")
    @job(progress=True, title="CDE - explaining drift")
    async def explain(self, ctx: ModuleContext, body: ExplainRequest) -> dict[str, Any]:
        adapted, _n_windows = await self._adapted_drifts(ctx)
        drift = next((d for d in adapted if d["drift_key"] == body.drift_key), None)
        if drift is None:
            raise HTTPException(status_code=404, detail=f"Unknown drift_key {body.drift_key!r}")

        cfg = ctx.config.value or {}
        clients = await load_module_ai_clients(cfg)
        index = get_pinecone_index(cfg)
        embedder = Embedder(clients.embeddings)

        # Upload ≠ Re-index: `POST /documents` only writes bytes to disk; the
        # vectors land only when `/ingest` runs. An empty namespace yields no
        # retrieval hits and the pipeline degrades to a generic "no relevant
        # documents" summary that reads as a lie to a user who *did* upload.
        # Fail fast with the actual remedy instead.
        if _namespace_vector_count(index, _ns_for(ctx.log_id)) == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No indexed documents for this log. Uploading a file does "
                    "not embed it — click Re-index in Context documents after "
                    "uploading (and check the indexing job didn't skip your "
                    "files, e.g. a scanned PDF with no extractable text)."
                ),
            )

        await ctx.progress.update(0.05, "Loading event log")
        events_preview = await self._events_preview(ctx)
        process_name = await self._process_name(ctx)

        selected = {
            "drift_key": body.drift_key,
            "drift_record": drift,
            "events_preview": events_preview,
            "process_name": process_name,
        }

        await ctx.progress.update(0.15, "Running CDE pipeline")

        def run_sync() -> dict:
            graph = build_graph(
                chat_llm=clients.chat,
                embedder=embedder,
                index=index,
                context_namespace=_ns_for(ctx.log_id),
            )
            return graph.invoke({"selected_drift": selected})

        result_state = await asyncio.to_thread(run_sync)

        pipeline_error = result_state.get("error_message")
        if pipeline_error:
            raise HTTPException(status_code=502, detail=pipeline_error)

        explanation = result_state.get("explanation") or {
            "summary": "",
            "ranked_causes": [],
        }
        # Filter by config'd confidence threshold + max_causes.
        threshold = float(cfg.get("confidence_threshold", 0.25))
        max_causes = int(cfg.get("max_causes", 3))
        explanation = {
            "summary": explanation.get("summary", ""),
            "ranked_causes": [
                c
                for c in (explanation.get("ranked_causes") or [])
                if c.get("confidence_score", 0.0) >= threshold
            ][:max_causes],
        }

        await ctx.progress.update(0.97, "Saving explanation")
        existing = await ctx.cache.get("explanations") or {}
        if not isinstance(existing, dict):
            existing = {}
        existing[body.drift_key] = {
            "drift_info": result_state.get("drift_info"),
            "drift_phrase": result_state.get("drift_phrase"),
            "explanation": explanation,
            "reranked_context_snippets": result_state.get("reranked_context_snippets", []),
            "supporting_context": result_state.get("supporting_context", []),
        }
        await ctx.cache.set("explanations", existing)
        await ctx.progress.update(1.0, "Done")

        return {"drift_key": body.drift_key, "explanation": explanation}

    @route.get("/explanations")
    async def list_explanations(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"explanations": await ctx.cache.get("explanations") or {}}

    @route.get("/explanations/{drift_key}")
    async def get_explanation(self, ctx: ModuleContext, drift_key: str) -> dict[str, Any]:
        explanations = await ctx.cache.get("explanations") or {}
        if not isinstance(explanations, dict) or drift_key not in explanations:
            raise HTTPException(
                status_code=404,
                detail=f"No explanation cached for {drift_key!r}",
            )
        return explanations[drift_key]

    # ── chatbot ──────────────────────────────────────────────────────────────

    @route.post("/chat")
    async def chat(self, ctx: ModuleContext, body: ChatRequest) -> dict[str, Any]:
        explanations = await ctx.cache.get("explanations") or {}
        if not isinstance(explanations, dict) or body.drift_key not in explanations:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Run /explain for this drift before chatting - the chatbot "
                    "needs the analysis state."
                ),
            )

        cfg = ctx.config.value or {}
        clients = await load_module_ai_clients(cfg)

        stored = explanations[body.drift_key]
        chat_state = await ctx.cache.get(f"chat_{body.drift_key}") or {"chat_history": []}
        chat_history = list(chat_state.get("chat_history") or [])

        # Re-run the graph just to answer the question. The drift_agent and
        # retrieval/re-rank stages re-execute, but their LLM responses are
        # cached on the per-invocation closures so the cost is bounded.
        # Simpler alternative: invoke the chatbot agent directly using the
        # cached state. We do the latter - cheaper and avoids re-spending on
        # retrieval and re-ranking.
        from .agents.chatbot_agent import make_chatbot_agent

        chatbot = make_chatbot_agent(llm=clients.chat)

        def run_sync() -> dict:
            return chatbot(
                {
                    "user_question": body.user_question,
                    "chat_history": chat_history,
                    "full_state_log": [stored],
                }
            )

        result = await asyncio.to_thread(run_sync)

        if "error_message" in result:
            raise HTTPException(status_code=500, detail=result["error_message"])

        new_history = result.get("chat_history") or chat_history
        chat_state = {"chat_history": new_history}
        await ctx.cache.set(f"chat_{body.drift_key}", chat_state)
        return chat_state

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _adapted_drifts(self, ctx: ModuleContext) -> tuple[list[dict[str, Any]], int]:
        """Read cv4cdd's detections and adapt them into the CDE drift shape."""
        from mate.api.modules.cache import ResultCache

        # cv4cdd writes its detections to *its own* per-user result cache. Scope
        # the read to the same user (ResultCache requires user_id since the
        # multi-user migration) or the lookup raises and we silently get no drifts.
        cv4cdd_cache = ResultCache(log_id=ctx.log_id, module_id="cv4cdd", user_id=ctx.user_id)
        detections = await cv4cdd_cache.get("detections")
        if not isinstance(detections, dict):
            return [], 0

        n_windows = int(detections.get("n_windows") or 0)
        df = await self._events_preview(ctx)

        adapted: list[dict[str, Any]] = []
        for drift in detections.get("drifts", []) or []:
            record = build_drift_record(cv4cdd_drift=drift, df=df, n_windows=n_windows)
            record["drift_key"] = _drift_key(drift)
            adapted.append(record)
        return adapted, n_windows

    async def _events_preview(self, ctx: ModuleContext):
        """Return the platform's events.parquet as a pandas DataFrame.

        Timestamps in this dataframe are tz-naive UTC (the platform's ingest
        path normalises them at write time -
        [apps/api/src/mate/api/ingest/dispatch.py:121](../../apps/api/src/mate/api/ingest/dispatch.py#L121)).
        Downstream code must NOT call ``pd.to_datetime(..., utc=False)`` on
        them - that path silently drops mixed-offset rows.
        """
        async with ctx.event_log as log:
            return await log.pandas()

    async def _process_name(self, ctx: ModuleContext) -> str:
        async with ctx.event_log as log:
            meta_path = log.events_path.parent / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    return str(meta.get("display_name") or meta.get("name") or ctx.log_id)
                except (OSError, ValueError):
                    pass
        return ctx.log_id
