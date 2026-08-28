"""Franzoi mapper - classify each snippet against the Process Mining Context
Taxonomy from Franzoi, Hartl et al. (2025).

Adapted from the original `backend/agents/franzoi_mapper_agent.py`. Same
prompt and Pydantic schema; the LLM client is injected by the graph builder.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from pydantic.v1 import BaseModel, Field

from ..state.schema import ContextSnippet, FranzoiClassification, GraphState


class _Classification(BaseModel):
    full_path: str = Field(
        description="The full hierarchical path of the category, e.g., ORGANIZATION_INTERNAL::Process_Management"
    )
    reasoning: str = Field(description="A brief reasoning for this classification.")


class ClassificationList(BaseModel):
    """A list of all relevant classifications for the given text snippet."""

    classifications: list[_Classification]


PROMPT_TEMPLATE = """You are an expert business process analyst specializing in process mining. Your task is to classify a given text snippet against the full three-level Franzoi et al. context taxonomy.

The snippet may fit into one or more categories. Identify all relevant categories from the taxonomy provided below.

TAXONOMY:
- LEVEL 1: PROCESS_IMMEDIATE (data directly related to the process execution)
  - Case: Properties of a single process instance (e.g., case ID, type of product).
  - Resource: Information about the actor performing the work (e.g., skill level, workload).
  - System_Interaction: Technical system events (e.g., button clicks, API calls).
- LEVEL 2: ORGANIZATION_INTERNAL (context from within the organization)
  - Organizational: Restructuring, new roles, departmental changes.
  - Process_Management: New KPIs, process redesign, new Standard Operating Procedures (SOPs).
  - IT_Management: New software rollout, system updates, server maintenance.
- LEVEL 3: ORGANIZATION_EXTERNAL (context from outside the organization)
  - Economic: Market shifts, competitor actions, price changes.
  - Social: Public holidays, seasonality, social trends, pandemics.
  - Legal: New laws, regulations, compliance requirements.
  - Technical: New external standards, infrastructure changes (e.g., cloud provider outage).

TEXT SNIPPET:
"{snippet_text}"

OUTPUT FORMAT:
Respond with exactly one JSON object with a single top-level key `classifications`, whose value is a JSON array. Each element must be an object with these two keys (no other keys, no prose, no wrapping):

- `full_path` (string): the full hierarchical path of a relevant category, e.g. `ORGANIZATION_INTERNAL::Process_Management`.
- `reasoning` (string): a brief justification for that classification.

Example shape (replace the values, keep the keys):
{{
  "classifications": [
    {{"full_path": "ORGANIZATION_INTERNAL::Process_Management", "reasoning": "..."}}
  ]
}}
"""


def make_franzoi_mapper_agent(*, llm: BaseChatModel):
    structured_llm = llm.with_structured_output(ClassificationList, method="json_mode")
    response_cache: dict[str, dict] = {}

    def run_franzoi_mapper_agent(state: GraphState) -> dict:
        logging.info("--- Running Franzoi Mapper Agent ---")
        snippets: list[ContextSnippet] = state.get("reranked_context_snippets", []) or []
        if not snippets:
            return {}

        logging.info(
            "Classifying %d snippets: %s",
            len(snippets),
            [Path(s["source_document"]).name for s in snippets],
        )

        for snippet in snippets:
            sanitized = snippet["snippet_text"].replace('"', '\\"')
            prompt = PROMPT_TEMPLATE.format(snippet_text=sanitized)

            cached = response_cache.get(prompt)
            if cached is None:
                try:
                    resp = structured_llm.invoke(prompt)
                    cached = resp.dict()
                    response_cache[prompt] = cached
                except Exception as e:
                    logging.error("Classification failed: %s", e)
                    snippet["classifications"] = [
                        {"full_path": "CLASSIFICATION_FAILED", "reasoning": str(e)}
                    ]
                    continue

            typed: list[FranzoiClassification] = [
                {
                    "full_path": item.get("full_path", "UNKNOWN"),
                    "reasoning": item.get("reasoning", ""),
                }
                for item in cached.get("classifications", [])
            ]
            snippet["classifications"] = typed

        return {}

    return run_franzoi_mapper_agent
