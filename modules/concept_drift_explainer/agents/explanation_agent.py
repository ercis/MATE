"""Explanation agent - synthesise the ranked, evidence-backed causes.

Adapted from the original `backend/agents/explanation_agent.py`. Same drift-
type-specific prompts, same confidence-score calculation, same two-pass draft
→ refine flow. The Pinecone index and OpenAI client are injected.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from pydantic.v1 import BaseModel, Field

from ..state.schema import Explanation, GraphState

CONTEXT_NS_DEFAULT = "context"


class Cause(BaseModel):
    cause_description: str = Field(
        description=(
            "A cautious, hypothetical analysis of how the evidence could "
            "explain the concept drift, citing the source. Frame this as a "
            "hypothesis, not a definitive conclusion."
        )
    )
    evidence_snippet: str = Field(
        description="The specific text snippet that supports the analysis."
    )
    source_document: str = Field(description="The name of the source document for the evidence.")
    context_category: str = Field(description="The most relevant Franzoi context category path.")
    confidence_score: float = Field(description="The data-driven confidence score for this cause.")


class RefinedCauseList(BaseModel):
    """A list of refined potential causes for the drift."""

    ranked_causes: list[Cause] = Field(
        description="A list of refined potential causes for the drift, ordered by importance."
    )


CAUSE_SCHEMA_SECTION = """

**## Required JSON Output Schema**

Respond with **exactly one** JSON object containing **all** of these top-level keys (no other keys, no wrapping, no prose):

- `cause_description` (string): A cautious, hypothetical analysis of how the evidence could explain the drift, citing the source. Frame as a hypothesis, not a conclusion.
- `evidence_snippet` (string): The specific snippet of text from the evidence that supports your analysis.
- `source_document` (string): The exact name of the source document the evidence comes from.
- `context_category` (string): The most relevant Franzoi context category path (e.g. `ORGANIZATION_INTERNAL::Process_Management`).
- `confidence_score` (number between 0 and 1): A data-driven confidence score; use 0.5 if unsure.

Example shape (replace the values, keep the keys):
{{
  "cause_description": "This could suggest that ...",
  "evidence_snippet": "...",
  "source_document": "policy-2017.pdf",
  "context_category": "ORGANIZATION_INTERNAL::Process_Management",
  "confidence_score": 0.7
}}
"""

REFINED_CAUSE_LIST_SCHEMA_SECTION = """

**## Required JSON Output Schema**

Respond with **exactly one** JSON object with a single top-level key `ranked_causes`, whose value is a JSON array. Each element of the array must be an object with **all** of these keys (no other keys, no wrapping, no prose):

- `cause_description` (string)
- `evidence_snippet` (string)
- `source_document` (string)
- `context_category` (string)
- `confidence_score` (number between 0 and 1)

Example shape (replace values, keep keys):
{{
  "ranked_causes": [
    {{
      "cause_description": "...",
      "evidence_snippet": "...",
      "source_document": "...",
      "context_category": "...",
      "confidence_score": 0.7
    }}
  ]
}}
"""


SUDDEN_DRIFT_PROMPT = """You are an expert business process analyst. Your goal is to explain a **Sudden Drift**.
A Sudden Drift involves an abrupt substitution of one process with another. From one point onward, the old process no longer occurs, and all new instances follow the updated version. This type of drift is often triggered by crises, emergencies, or immediate regulatory changes (Bose et al., 2011).
Prioritize evidence that points to a single, discrete event with a specific date.

**## 1. Detected Concept Drift**
- **Drift Type:** {drift_type}
- **Drift Period:** {start_timestamp} to {end_timestamp}

**## 2. Reference Glossary (for your eyes only - DO NOT cite)**
{formatted_glossary}

**## 3. Evidence from Context Documents**
{formatted_evidence}

**## 4. Your Task**
Based on the single block of evidence provided, generate one potential cause for the **SUDDEN** drift. The wording of your explanation must be cautious and hypothetical, not definitive. Use phrases like 'This could suggest...', 'A possible explanation is...', or 'The evidence may indicate...'. Your output must be a single, valid JSON object matching the requested schema.

## Heuristic Guidance
Pay special attention to any snippet classified as **ORGANIZATION_EXTERNAL::Legal** or **ORGANIZATION_EXTERNAL::Technical**, as these often explain sudden drifts.
"""

GRADUAL_DRIFT_PROMPT = """You are an expert business process analyst. Your goal is to explain a **Gradual Drift**.
A Gradual Drift describes a transition phase where both the old and new process variants coexist. This is common in rollout scenarios, where a new process is adopted for new cases, while ongoing cases continue under the old variant. Over time, the older version is phased out entirely (Bose et al., 2011).
Prioritize evidence suggesting a transition, coexistence of old/new processes, or phased rollouts.

**## 1. Detected Concept Drift**
- **Drift Type:** {drift_type}
- **Drift Period:** {start_timestamp} to {end_timestamp}

**## 2. Reference Glossary (for your eyes only - DO NOT cite)**
{formatted_glossary}

**## 3. Evidence from Context Documents**
{formatted_evidence}

**## 4. Your Task**
Based on the single block of evidence provided, generate one potential cause for the **GRADUAL** drift. The wording of your explanation must be cautious and hypothetical, not definitive. Use phrases like 'This could suggest...', 'A possible explanation is...', or 'The evidence may indicate...'. Your output must be a single, valid JSON object matching the requested schema.

## Heuristic Guidance
Pay special attention to any snippet classified as **ORGANIZATION_INTERNAL::Organizational** or **ORGANIZATION_EXTERNAL::Legal**, as these often explain gradual drifts.
"""

INCREMENTAL_DRIFT_PROMPT = """You are an expert business process analyst. Your goal is to explain an **Incremental Drift**.
An Incremental Drift consists of a sequence of small, continuous changes that cumulatively result in significant process transformation. It is often associated with agile BPM practices, where iterative adjustments are made without a single, identifiable change point (Bose et al., 2011; Kraus und van der Aa, 2025).
Prioritize evidence of multiple small adjustments, iterative improvements, or agile practices over time.

**## 1. Detected Concept Drift**
- **Drift Type:** {drift_type}
- **Drift Period:** {start_timestamp} to {end_timestamp}

**## 2. Reference Glossary (for your eyes only - DO NOT cite)**
{formatted_glossary}

**## 3. Evidence from Context Documents**
{formatted_evidence}

**## 4. Your Task**
Based on the single block of evidence provided, generate one potential cause for the **INCREMENTAL** drift. The wording of your explanation must be cautious and hypothetical, not definitive. Use phrases like 'This could suggest...', 'A possible explanation is...', or 'The evidence may indicate...'. Your output must be a single, valid JSON object matching the requested schema.

## Heuristic Guidance
Pay special attention to any snippet classified as **ORGANIZATION_INTERNAL::Process_Management** or **ORGANIZATION_INTERNAL::IT_Management**, as these often explain incremental drifts.
"""

RECURRING_DRIFT_PROMPT = """You are an expert business process analyst. Your goal is to explain a **Recurring Drift**.
A Recurring Drift occurs when previously observed process versions reappear over time, often in a cyclical pattern. These drifts may follow seasonal cycles or non-periodic triggers (e.g., market-specific promotional workflows) (Bose et al., 2011; Kraus und van der Aa, 2025).
Prioritize evidence of seasonal activities, cyclical patterns, or temporary process changes that are designed to reappear.

**## 1. Detected Concept Drift**
- **Drift Type:** {drift_type}
- **Drift Period:** {start_timestamp} to {end_timestamp}

**## 2. Reference Glossary (for your eyes only - DO NOT cite)**
{formatted_glossary}

**## 3. Evidence from Context Documents**
{formatted_evidence}

**## 4. Your Task**
Based on the single block of evidence provided, generate one potential cause for the **RECURRING** drift. The wording of your explanation must be cautious and hypothetical, not definitive. Use phrases like 'This could suggest...', 'A possible explanation is...', or 'The evidence may indicate...'. Your output must be a single, valid JSON object matching the requested schema.

## Heuristic Guidance
Pay special attention to any snippet classified as **ORGANIZATION_EXTERNAL::Social** or **ORGANIZATION_EXTERNAL::Economic**, as these often explain recurring drifts.
"""

SUMMARY_PROMPT_TEMPLATE = """You are a senior business process analyst. Based on the following list of potential causes for a concept drift, write a concise, 1-3 sentence executive summary that synthesizes the main findings.

**## Potential Causes**
{formatted_causes}
"""

REFINE_PROMPT_TEMPLATE = """You are a senior editor reviewing an analysis from a junior analyst.
Your task is to critique and refine the provided "Draft Explanation" based on the original "Evidence" and "Reference Glossary".
Ensure the final summary is concise, the cause descriptions are logical, and that every claim is strongly supported by the cited evidence.

**## Original Reference Glossary (for your eyes only - DO NOT cite)**
{formatted_glossary}

**## Original Evidence**
{formatted_evidence}

**## Draft Explanation to Review**
{draft_causes}

**## 3. Your Task**
Critique and refine the draft explanation, ensuring the final wording is cautious and hypothetical. Your output must be a single, valid JSON object matching the requested schema.
"""


# Append schema descriptions to each prompt. With `method="json_mode"` the
# LLM is told to return JSON but is NOT given the schema - so we have to spell
# it out in the prompt or the model invents its own shape.
SUDDEN_DRIFT_PROMPT += CAUSE_SCHEMA_SECTION
GRADUAL_DRIFT_PROMPT += CAUSE_SCHEMA_SECTION
INCREMENTAL_DRIFT_PROMPT += CAUSE_SCHEMA_SECTION
RECURRING_DRIFT_PROMPT += CAUSE_SCHEMA_SECTION
REFINE_PROMPT_TEMPLATE += REFINED_CAUSE_LIST_SCHEMA_SECTION


def _format_context_for_prompt(snippets: list) -> str:
    out = ""
    for i, snippet in enumerate(snippets):
        out += f"### Evidence Snippet {i + 1}\n"
        out += f"- **Source Document:** {snippet['source_document']}\n"
        cls = ", ".join(c["full_path"] for c in snippet.get("classifications", []) or [])
        if cls:
            out += f"- **Classified As:** [{cls}]\n"
        sanitized = snippet["snippet_text"].replace('"', '\\"')
        out += f'- **Snippet Text:** "{sanitized}"\n\n'
    return out


def _expand_context(snippets: list, index, namespace: str) -> list:
    """Pull the full text of each unique source doc back from Pinecone."""
    if not snippets:
        return []
    unique_sources = sorted({s["source_document"] for s in snippets})

    # The dummy vector used for metadata-only queries must match the index's
    # actual dimension, which the user can configure per module.
    try:
        stats = index.describe_index_stats()
        dim = int(getattr(stats, "dimension", 0) or (stats or {}).get("dimension", 0))
    except Exception:
        dim = 0
    if dim <= 0:
        dim = 1536
    dummy_vector = [0] * dim

    expanded: list[dict] = []
    for source in unique_sources:
        try:
            response = index.query(
                vector=dummy_vector,
                filter={"source": source},
                top_k=100,
                namespace=namespace,
                include_metadata=True,
            )
            full_text = " ".join(
                (m.metadata or {}).get("text", "") for m in (response.matches or [])
            ).strip()
        except Exception as e:
            logging.error("Failed to expand context for '%s': %s", source, e)
            full_text = ""

        original = next(s for s in snippets if s["source_document"] == source)
        expanded_doc = dict(original)
        if full_text:
            expanded_doc["snippet_text"] = full_text
        expanded.append(expanded_doc)

    return sorted(expanded, key=lambda d: d.get("priority_score", 0.0), reverse=True)


def _calculate_confidence(snippet: dict, drift_info: dict, rank: int) -> float:
    base = float(snippet.get("priority_score", 0.0))
    bonus = 0.0
    try:
        drift_start = datetime.fromisoformat(drift_info["start_timestamp"])
        ts = int(snippet.get("timestamp", 0) or 0)
        if ts > 0:
            evidence_dt = datetime.fromtimestamp(ts)
            delta_days = abs((drift_start - evidence_dt).days)
            bonus = 0.15 * max(0.0, 1.0 - (delta_days / 60.0))
    except (KeyError, ValueError):
        pass

    multiplier = 2.0 if rank == 0 else 1.5 if rank == 1 else 1.0
    final = min(0.99, (base + bonus) * multiplier)
    return round(final, 2)


def _select_prompt(drift_type: str) -> str:
    t = (drift_type or "").lower()
    if "sudden" in t:
        return SUDDEN_DRIFT_PROMPT
    if "gradual" in t:
        return GRADUAL_DRIFT_PROMPT
    if "recurring" in t:
        return RECURRING_DRIFT_PROMPT
    return INCREMENTAL_DRIFT_PROMPT


def make_explanation_agent(
    *,
    llm: BaseChatModel,
    index,
    context_namespace: str = CONTEXT_NS_DEFAULT,
):
    structured_cause = llm.with_structured_output(Cause, method="json_mode")
    structured_refine = llm.with_structured_output(RefinedCauseList, method="json_mode")
    response_cache: dict[str, object] = {}

    def _invoke_structured(structured_llm, prompt: str, schema_name: str) -> dict:
        resp = structured_llm.invoke(prompt)
        if resp is None:
            try:
                raw = llm.invoke(prompt)
                raw_text = getattr(raw, "content", str(raw))
            except Exception:
                raw_text = "<raw invoke also failed>"
            logging.error(
                "Structured LLM returned None for schema %s. Raw output: %r",
                schema_name,
                raw_text[:1000] if isinstance(raw_text, str) else raw_text,
            )
            raise RuntimeError(
                f"LLM produced no parseable {schema_name} output - "
                "the selected model may not support structured tool calling. "
                "Try a different chat model in Module settings → AI models."
            )
        return resp.dict()

    def run_explanation_agent(state: GraphState) -> dict:
        logging.info("--- Running Explanation Agent ---")

        drift_info = state.get("drift_info") or {}
        evidence_context = state.get("reranked_context_snippets", []) or []
        glossary_context = state.get("supporting_context", []) or []

        usable_evidence = [
            s
            for s in evidence_context
            if not s.get("support_only") and s.get("source_type") != "bpm-kb"
        ]
        usable_evidence = _expand_context(usable_evidence, index, context_namespace)

        if not usable_evidence:
            return {
                "explanation": {
                    "summary": "No explanation could be generated as no relevant contextual documents were found.",
                    "ranked_causes": [],
                }
            }

        try:
            if len(usable_evidence) == 1:
                evidence_doc = usable_evidence[0]
                formatted_glossary = _format_context_for_prompt(glossary_context)
                formatted_evidence = _format_context_for_prompt([evidence_doc])
                prompt_template = _select_prompt(drift_info.get("drift_type", ""))
                prompt = prompt_template.format(
                    drift_type=drift_info.get("drift_type", ""),
                    start_timestamp=drift_info.get("start_timestamp", ""),
                    end_timestamp=drift_info.get("end_timestamp", ""),
                    formatted_glossary=formatted_glossary,
                    formatted_evidence=formatted_evidence,
                )

                cached = response_cache.get(prompt)
                if cached is None:
                    cached = _invoke_structured(structured_cause, prompt, "Cause")
                    response_cache[prompt] = cached
                cause_dict = dict(cached)
                cause_dict["confidence_score"] = 0.99
                final_explanation: Explanation = {
                    "summary": cause_dict["cause_description"],
                    "ranked_causes": [cause_dict],
                }
                return {"explanation": final_explanation}

            # Multi-evidence case.
            draft_causes: list[dict] = []
            for i, evidence_doc in enumerate(usable_evidence):
                formatted_glossary = _format_context_for_prompt(glossary_context)
                formatted_evidence = _format_context_for_prompt([evidence_doc])
                prompt_template = _select_prompt(drift_info.get("drift_type", ""))
                prompt = prompt_template.format(
                    drift_type=drift_info.get("drift_type", ""),
                    start_timestamp=drift_info.get("start_timestamp", ""),
                    end_timestamp=drift_info.get("end_timestamp", ""),
                    formatted_glossary=formatted_glossary,
                    formatted_evidence=formatted_evidence,
                )
                cached = response_cache.get(prompt)
                if cached is None:
                    cached = _invoke_structured(structured_cause, prompt, "Cause")
                    response_cache[prompt] = cached
                cause_dict = dict(cached)
                cause_dict["confidence_score"] = _calculate_confidence(
                    evidence_doc, drift_info, rank=i
                )
                draft_causes.append(cause_dict)

            drafts_for_prompt = [
                {k: v for k, v in c.items() if k != "confidence_score"} for c in draft_causes
            ]
            refine_prompt = REFINE_PROMPT_TEMPLATE.format(
                formatted_glossary=_format_context_for_prompt(glossary_context),
                formatted_evidence=_format_context_for_prompt(usable_evidence),
                draft_causes=json.dumps({"ranked_causes": drafts_for_prompt}, indent=2),
            )
            cached = response_cache.get(refine_prompt)
            if cached is None:
                cached = _invoke_structured(structured_refine, refine_prompt, "RefinedCauseList")
                response_cache[refine_prompt] = cached
            refined_data = dict(cached)
            final_causes: list[dict] = list(refined_data.get("ranked_causes", []) or [])

            if len(final_causes) == len(draft_causes):
                for i, cause in enumerate(final_causes):
                    cause["confidence_score"] = draft_causes[i]["confidence_score"]

            formatted_causes = "\n".join(
                f"- {c.get('cause_description', '')}" for c in final_causes
            )
            summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(formatted_causes=formatted_causes)
            cached_summary = response_cache.get(summary_prompt)
            if cached_summary is None:
                cached_summary = (llm.invoke(summary_prompt).content or "").strip()
                response_cache[summary_prompt] = cached_summary

            final_explanation = {
                "summary": cached_summary,
                "ranked_causes": final_causes,
            }
            return {"explanation": final_explanation}

        except Exception as e:
            logging.error("Failed to generate final explanation: %s", e, exc_info=True)
            return {"error_message": f"Failed to generate final explanation: {e}"}

    return run_explanation_agent
