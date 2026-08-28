"""Shared GraphState for the CDE pipeline.

Mirrors the original repo's `backend/state/schema.py` minus the drift-linker
fields and UI-only feedback dict - those are out of scope for the first port.
Every agent reads/writes a subset of these keys.
"""

from __future__ import annotations

from typing import TypedDict


class DriftInfo(TypedDict, total=False):
    """Structured representation of a single concept drift."""

    process_name: str
    changepoints: tuple[str, str]
    drift_type: str
    confidence: float
    # ISO-8601 strings, tz-naive UTC (the platform's ingest normalises to this).
    start_timestamp: str
    end_timestamp: str
    # Optional - only populated during evaluation against a labelled corpus.
    gold_doc: str


class FranzoiClassification(TypedDict):
    """Process Mining Context Taxonomy classification (Franzoi et al. 2025)."""

    full_path: str
    reasoning: str


class ContextSnippet(TypedDict, total=False):
    """A retrieved chunk of evidence (or a glossary term)."""

    snippet_text: str
    source_document: str
    timestamp: int  # Unix seconds, 0 when unknown.
    source_type: str  # "context" | "bpm-kb"
    score: float  # raw cosine similarity from Pinecone
    semantic_specificity: float | None
    priority_score: float | None
    support_only: bool  # True for glossary entries
    classifications: list[FranzoiClassification]


class RankedCause(TypedDict):
    """A single hypothesised cause shown in the final explanation."""

    cause_description: str
    evidence_snippet: str
    source_document: str
    context_category: str
    confidence_score: float


class Explanation(TypedDict):
    """The synthesised, ranked explanation."""

    summary: str
    ranked_causes: list[RankedCause]


class GraphState(TypedDict, total=False):
    """Full state passed through the LangGraph workflow."""

    # Selection from the UI: { drift_key: str } (and optional gold_doc).
    selected_drift: dict | None

    # Populated by drift_agent.
    drift_info: DriftInfo
    drift_keywords: list[str] | None
    drift_phrase: str | None

    # Populated by context_retrieval_agent.
    raw_context_snippets: list[ContextSnippet]

    # Populated by re_ranker_agent.
    reranked_context_snippets: list[ContextSnippet]
    supporting_context: list[ContextSnippet]

    # Populated by explanation_agent.
    explanation: Explanation

    # Chatbot.
    user_question: str | None
    chat_history: list[tuple[str, str]]

    # Cross-drift context for the chatbot - list of past completed states.
    full_state_log: list[dict]

    # Surface error messages cleanly in the UI.
    error_message: str | None
