"""Chatbot agent - follow-up Q&A bound to a completed analysis state.

Adapted from `backend/agents/chatbot_agent.py`. Topical guardrail + final
answer generation, with the LLM client injected from the graph builder.
"""

from __future__ import annotations

import logging
import textwrap

from langchain_core.language_models import BaseChatModel
from pydantic.v1 import BaseModel, Field

from ..state.schema import GraphState


class ChatbotGuardrail(BaseModel):
    """Boolean check for whether a user's question is on-topic."""

    is_on_topic: bool = Field(
        description="True if the user's question is relevant to the provided context, False otherwise."
    )


def _format_chat_history(chat_history: list) -> str:
    if not chat_history:
        return "No previous conversation."
    return "\n".join(f"Human: {q}\nAssistant: {a}" for q, a in chat_history)


def _format_full_analysis_context(full_state_log: list[dict]) -> str:
    out = ""
    for i, state in enumerate(full_state_log, 1):
        drift_info = state.get("drift_info", {}) or {}
        explanation = state.get("explanation", {}) or {}
        ranked_causes = explanation.get("ranked_causes", []) or []
        causes_list = (
            "\n".join(
                f'    - {c.get("source_document", "N/A")}: "'
                f'{(c.get("evidence_snippet", "") or "")[:100]}…"'
                for c in ranked_causes
            )
            or "    - None"
        )
        out += textwrap.dedent(
            f"""\
            ### Drift #{i}: {drift_info.get("drift_type")}
            - **Timeframe:** {drift_info.get("start_timestamp")} to {drift_info.get("end_timestamp")}
            - **Summary:** {explanation.get("summary")}
            - **Causal Documents:**
            {causes_list}
            """
        )
    return out


def make_chatbot_agent(*, llm: BaseChatModel):
    guardrail_llm = llm.with_structured_output(ChatbotGuardrail, method="json_mode")
    answer_llm = llm
    response_cache: dict[str, str] = {}

    def _is_on_topic(user_question: str, context: str) -> bool:
        guard_prompt = textwrap.dedent(
            """\
            You are a topic-classification assistant. Your task is to determine if a user's question is relevant to the provided 'Analysis Context'.

            An on-topic question is one that asks about the concept drift, the business process, the evidence documents, or seeks clarification on the content within the analysis.
            An off-topic question asks about something completely unrelated to the provided context.

            **Analysis Context:**
            {context}

            **User's Question:**
            {question}

            Respond with exactly one JSON object with a single boolean key `is_on_topic` (no other keys, no prose, no wrapping). Example:
            {{"is_on_topic": true}}
            """
        ).format(context=context, question=user_question)

        try:
            response = guardrail_llm.invoke(guard_prompt)
            return bool(response.is_on_topic)
        except Exception as e:
            logging.error("Guardrail check failed: %s", e)
            return True

    def run_chatbot_agent(state: GraphState) -> dict:
        logging.info("--- Running Chatbot Agent ---")
        user_question = state.get("user_question")
        if not user_question:
            return {}

        full_state_log = state.get("full_state_log", []) or []
        chat_history = state.get("chat_history", []) or []
        analysis_context = _format_full_analysis_context(full_state_log)

        if not _is_on_topic(user_question, analysis_context):
            ai_answer = (
                "I am an assistant for analyzing concept drifts. I can only "
                "answer questions related to the drift analysis, the process, "
                "and the provided evidence. How can I help you with the analysis?"
            )
            return {"chat_history": [*chat_history, (user_question, ai_answer)]}

        full_context = textwrap.dedent(
            f"""
            **Full Analysis Report:**
            {analysis_context}
            **Previous Conversation:**
            {_format_chat_history(chat_history)}
            """
        )

        prompt = textwrap.dedent(
            """\
            You are a helpful AI assistant having a conversation with a business analyst. The analyst is asking follow-up questions about a concept drift explanation that you have already provided.
            Use the provided "Original Analysis Context" and "Previous Conversation" to answer the "User's New Question". Keep your answers concise and helpful. You can now refer to the "Causal Documents" by name in your answer.

            ---
            {context}
            ---

            **User's New Question:**
            {question}

            **Your Answer:**
            """
        ).format(context=full_context, question=user_question)

        cached = response_cache.get(prompt)
        if cached is None:
            try:
                cached = (answer_llm.invoke(prompt).content or "").strip()
                response_cache[prompt] = cached
            except Exception as e:
                logging.error("Chatbot agent failed: %s", e)
                return {"error_message": str(e)}

        return {"chat_history": [*chat_history, (user_question, cached)]}

    return run_chatbot_agent
