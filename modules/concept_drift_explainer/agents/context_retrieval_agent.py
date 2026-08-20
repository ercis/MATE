"""Context retrieval agent - broad hybrid search in Pinecone.

Adapted from the original `backend/agents/context_retrieval_agent.py`. The
Pinecone index and OpenAI embedder are now passed in via the graph builder
(see `graph/build_graph.py`) instead of being read from process env.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..state.schema import ContextSnippet, GraphState
from ..utils.embeddings import Embedder

CONTEXT_NS_DEFAULT = "context"
KB_NS = "bpm-kb"
CONTEXT_TOP_K = 30
WINDOW_BEFORE = 14
WINDOW_AFTER = 3


def run_context_retrieval_agent(
    state: GraphState,
    *,
    index,
    embedder: Embedder,
    context_namespace: str,
) -> dict:
    logging.info("--- Running Context Retrieval Agent ---")

    drift_info = state.get("drift_info")
    drift_keywords = state.get("drift_keywords", []) or []

    if not drift_info:
        return {"error_message": "Drift info not found in state."}

    changepoints = drift_info.get("changepoints", [])
    if not isinstance(changepoints, (list, tuple)) or len(changepoints) != 2:
        return {"error_message": f"Invalid changepoints: {changepoints}"}
    start_activity, end_activity = changepoints

    process_name = drift_info.get("process_name", "a business process")
    base_query = (
        f"A concept drift of type '{drift_info['drift_type']}' was detected. "
        f"It occurred in the '{process_name}' process involving the activities "
        f"'{start_activity}' and '{end_activity}'."
    )
    if drift_keywords:
        query_text = f"{base_query} Associated keywords include: {', '.join(drift_keywords)}."
    else:
        query_text = base_query

    try:
        query_vector = embedder.embed_query(query_text)
    except Exception as e:
        # Surface the full provider error (incl. response body) - without this
        # a 400 from the embeddings endpoint silently empties out the rest of
        # the pipeline and the UI shows nothing.
        body = getattr(getattr(e, "response", None), "text", None)
        detail = f"{e!s}" + (f" | body={body}" if body else "")
        logging.error("Embedding call failed: %s", detail)
        return {"error_message": f"Embedding failed: {detail}"}

    temporal_filter: Optional[dict] = None
    try:
        start_date = datetime.fromisoformat(drift_info["start_timestamp"])
        filter_start = start_date - timedelta(days=WINDOW_BEFORE)
        filter_end = start_date + timedelta(days=WINDOW_AFTER)
        temporal_filter = {
            "timestamp": {
                "$gte": int(filter_start.timestamp()),
                "$lte": int(filter_end.timestamp()),
            }
        }
    except (ValueError, TypeError, KeyError) as e:
        logging.warning("Could not build temporal filter: %s", e)

    all_hits: dict = {}

    try:
        kwargs = {
            "vector": query_vector,
            "top_k": CONTEXT_TOP_K,
            "namespace": context_namespace,
            "include_metadata": True,
        }
        if temporal_filter:
            context_response = index.query(filter=temporal_filter, **kwargs)
            if not getattr(context_response, "matches", []):
                context_response = index.query(**kwargs)
        else:
            context_response = index.query(**kwargs)
    except Exception as e:
        return {"error_message": f"Error querying '{context_namespace}': {e}"}

    for match in getattr(context_response, "matches", []) or []:
        meta = match.metadata or {}
        key = (meta.get("source", "Unknown"), meta.get("text", ""))
        if key not in all_hits or match.score > all_hits[key]["score"]:
            all_hits[key] = {
                "score": match.score,
                "metadata": meta,
                "source_type": context_namespace,
            }

    try:
        kb_response = index.query(
            vector=query_vector,
            top_k=1,
            namespace=KB_NS,
            include_metadata=True,
        )
    except Exception as e:
        logging.warning("BPM-KB query failed (continuing without glossary): %s", e)
        kb_response = None

    for match in getattr(kb_response, "matches", []) or []:
        meta = dict(match.metadata or {})
        meta["support_only"] = True
        key = (meta.get("source", "Unknown"), meta.get("text", ""))
        if key not in all_hits or match.score > all_hits[key]["score"]:
            all_hits[key] = {
                "score": match.score,
                "metadata": meta,
                "source_type": KB_NS,
            }

    sorted_hits = sorted(all_hits.values(), key=lambda x: x["score"], reverse=True)

    retrieved_snippets: list[ContextSnippet] = []
    for hit in sorted_hits:
        meta = hit["metadata"]
        snippet: ContextSnippet = {
            "snippet_text": meta.get("text", ""),
            "source_document": meta.get("source", "Unknown"),
            "timestamp": int(meta.get("timestamp", 0) or 0),
            "score": float(hit.get("score", 0.0)),
            "classifications": [],
            "source_type": hit["source_type"],
            "support_only": bool(meta.get("support_only", False)),
        }
        retrieved_snippets.append(snippet)

    logging.info("Retrieved %d candidate snippets.", len(retrieved_snippets))
    for i, snip in enumerate(retrieved_snippets[:5]):
        logging.info(
            "  > #%d %s (score=%.3f, src=%s)",
            i + 1,
            Path(snip["source_document"]).name,
            snip["score"],
            snip["source_type"],
        )

    return {"raw_context_snippets": retrieved_snippets}
