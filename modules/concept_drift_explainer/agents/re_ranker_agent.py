"""Re-ranker agent - blended-score pre-sort + LLM curator.

Adapted from the original `backend/agents/re_ranker_agent.py`. Identical
algorithm; only the credential/embedder plumbing changes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
from langchain_core.language_models import BaseChatModel
from numpy.linalg import norm
from pydantic.v1 import BaseModel, Field

from ..state.schema import ContextSnippet, GraphState
from ..utils.embeddings import Embedder

NUM_SNIPPETS_TO_KEEP = 4
MAX_CANDIDATES_FOR_LLM = 20
ALPHA = 0.4


class RerankedIndices(BaseModel):
    """A list of the 1-based integer indices of the most relevant snippets."""

    reranked_indices: List[int] = Field(
        description="A list of the 1-based integer indices of the top snippets, ordered by relevance."
    )


PROMPT_TEMPLATE = """You are an expert ranking assistant. Your task is to rank a list of candidate documents that may explain a concept drift. Each candidate has been pre-scored with a `priority_score`.

You will receive:

1. A detected concept drift:
   - Drift Type: {drift_type}
   - Drift Phrase: "{drift_phrase}"

2. A list of candidate snippets, each with:
   - index (1-based)
   - document identifier
   - priority_score
   - similarity_score
   - snippet text

Format:
{formatted_snippets}

Your task is to sort all candidates by relevance using these pairwise rules:

1. **Priority**
   - If |priority_score_A - priority_score_B| > 0.025, the higher priority_score wins.

2. **Similarity**
   - Otherwise (|Δpriority| ≤ 0.025):
     - If |similarity_score_A - similarity_score_B| > 0.01, the higher similarity_score wins.

3. **Semantic Judgment**
   - Otherwise (both differences within thresholds):
     - Use your semantic understanding of the snippet texts to decide which is more relevant.

**Output**
Return **only** a JSON object with a single key `"reranked_indices"`, whose value is the list of the top {num_to_keep} 1-based indices, in descending order of relevance.
Example:
 ```json
{{ "reranked_indices": [3, 1, 4, 2] }}
"""


def _format_snippets(snippets: List[ContextSnippet], start_date: datetime) -> str:
    out = ""
    for i, snippet in enumerate(snippets, 1):
        text = snippet["snippet_text"].replace("\n", " ").replace('"', '\\"')
        out += (
            f"Candidate {i}:\n"
            f"  doc_id: \"{snippet['source_document']}\"\n"
            f"  priority_score: {snippet.get('priority_score', 0.0):.3f}\n"
            f"  similarity_score: {snippet.get('score', 0.0):.3f}\n"
            f"  semantic_specificity: {snippet.get('semantic_specificity', 0.0):.3f}\n"
            f"  snippet: \"{text}\"\n"
        )
    return out


def make_reranker_agent(*, llm: BaseChatModel, embedder: Embedder):
    structured_llm = llm.with_structured_output(RerankedIndices, method="json_mode")
    response_cache: dict[str, dict] = {}

    def run_reranker_agent(state: GraphState) -> dict:
        logging.info("--- Running Re-Ranker Agent ---")

        raw_snippets = state.get("raw_context_snippets", []) or []
        supporting_context: list[ContextSnippet] = []
        candidates: list[ContextSnippet] = []
        for snip in raw_snippets:
            if snip.get("source_type") == "bpm-kb":
                supporting_context.append(snip)
            else:
                candidates.append(snip)

        if not candidates:
            return {
                "reranked_context_snippets": [],
                "supporting_context": supporting_context,
            }

        drift_info = state.get("drift_info", {})
        drift_phrase = state.get("drift_phrase", "")
        try:
            start_date = datetime.fromisoformat(drift_info["start_timestamp"])
        except (KeyError, ValueError):
            start_date = datetime.utcnow()

        # Step 1: blend semantic specificity and similarity.
        drift_emb = embedder.get(drift_phrase)
        for snip in candidates:
            snippet_emb = embedder.get(snip["snippet_text"])
            denom = norm(drift_emb) * norm(snippet_emb)
            sem_spec = float(np.dot(drift_emb, snippet_emb) / denom) if denom else 0.0
            snip["semantic_specificity"] = sem_spec
            similarity = float(snip.get("score", 0.0))
            snip["priority_score"] = (ALPHA * sem_spec) + ((1 - ALPHA) * similarity)

        # Step 2: dedupe per document, keeping the best chunk per doc.
        unique_by_doc: dict[str, ContextSnippet] = {}
        for snip in candidates:
            doc = snip["source_document"]
            current = unique_by_doc.get(doc)
            if current is None or snip["priority_score"] > current["priority_score"]:
                unique_by_doc[doc] = snip
        candidates = list(unique_by_doc.values())

        # Step 3: pre-sort by blended priority.
        sorted_candidates = sorted(
            candidates, key=lambda x: x.get("priority_score", 0.0), reverse=True
        )

        # Step 4: LLM final pick.
        if len(candidates) == 1:
            reranked_list = candidates
        else:
            candidates_for_llm = sorted_candidates[:MAX_CANDIDATES_FOR_LLM]
            prompt = PROMPT_TEMPLATE.format(
                drift_type=drift_info.get("drift_type", ""),
                drift_phrase=drift_phrase,
                formatted_snippets=_format_snippets(candidates_for_llm, start_date),
                num_to_keep=NUM_SNIPPETS_TO_KEEP,
            )
            cached = response_cache.get(prompt)
            if cached is None:
                try:
                    resp = structured_llm.invoke(prompt)
                except Exception as e:
                    logging.warning(
                        "Re-ranker LLM call failed, falling back to pre-sort: %s", e
                    )
                    resp = None
                if resp is None:
                    cached = {
                        "reranked_indices": list(range(1, len(candidates_for_llm) + 1))
                    }
                else:
                    cached = resp.dict()
                response_cache[prompt] = cached

            indices = cached.get("reranked_indices", []) or []
            llm_picks = [
                candidates_for_llm[i - 1]
                for i in indices
                if 1 <= i <= len(candidates_for_llm)
            ]

            seen_docs: set[str] = set()
            final_evidence: list[ContextSnippet] = []
            for snip in llm_picks:
                doc = snip["source_document"]
                if doc in seen_docs:
                    continue
                seen_docs.add(doc)
                final_evidence.append(snip)
                if len(final_evidence) == NUM_SNIPPETS_TO_KEEP:
                    break
            reranked_list = final_evidence

        logging.info(
            "Re-ranker kept %d of %d candidates: %s",
            len(reranked_list),
            len(candidates),
            [Path(s["source_document"]).name for s in reranked_list],
        )
        return {
            "supporting_context": supporting_context,
            "reranked_context_snippets": reranked_list,
        }

    return run_reranker_agent
