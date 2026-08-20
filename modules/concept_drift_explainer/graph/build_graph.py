"""LangGraph wiring for the CDE.

  drift → retrieval → re_ranker → franzoi_mapper → explanation
                                                      │
                                                      ▼  (if user_question)
                                                  chatbot ─┐
                                                      │    │
                                                      └────┘ (loops back to explanation)

The graph builder is parameterised over a chat LLM + embedder + Pinecone index
+ per-log namespace so the same wiring can be re-used in tests with mocks and
across providers (Anthropic / OpenAI / OpenAI-compat).
"""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, StateGraph

from ..agents.chatbot_agent import make_chatbot_agent
from ..agents.context_retrieval_agent import run_context_retrieval_agent
from ..agents.drift_agent import make_drift_agent
from ..agents.explanation_agent import make_explanation_agent
from ..agents.franzoi_mapper_agent import make_franzoi_mapper_agent
from ..agents.re_ranker_agent import make_reranker_agent
from ..state.schema import GraphState
from ..utils.embeddings import Embedder


def _should_continue(state: GraphState) -> Literal["chatbot_agent", "__end__"]:
    return "chatbot_agent" if state.get("user_question") else END


def build_graph(
    *,
    chat_llm: BaseChatModel,
    embedder: Embedder,
    index,
    context_namespace: str,
):
    """Compile a runnable LangGraph application bound to these resources."""
    drift_agent = make_drift_agent(llm=chat_llm)

    def retrieval_node(state: GraphState) -> dict:
        return run_context_retrieval_agent(
            state,
            index=index,
            embedder=embedder,
            context_namespace=context_namespace,
        )

    reranker = make_reranker_agent(llm=chat_llm, embedder=embedder)
    franzoi = make_franzoi_mapper_agent(llm=chat_llm)
    explainer = make_explanation_agent(
        llm=chat_llm,
        index=index,
        context_namespace=context_namespace,
    )
    chatbot = make_chatbot_agent(llm=chat_llm)

    workflow = StateGraph(GraphState)
    workflow.add_node("drift_agent", drift_agent)
    workflow.add_node("context_retrieval_agent", retrieval_node)
    workflow.add_node("re_ranker_agent", reranker)
    workflow.add_node("franzoi_mapper_agent", franzoi)
    workflow.add_node("explanation_agent", explainer)
    workflow.add_node("chatbot_agent", chatbot)

    workflow.set_entry_point("drift_agent")
    workflow.add_edge("drift_agent", "context_retrieval_agent")
    workflow.add_edge("context_retrieval_agent", "re_ranker_agent")
    workflow.add_edge("re_ranker_agent", "franzoi_mapper_agent")
    workflow.add_edge("franzoi_mapper_agent", "explanation_agent")
    workflow.add_conditional_edges(
        "explanation_agent",
        _should_continue,
        {"chatbot_agent": "chatbot_agent", END: END},
    )
    workflow.add_edge("chatbot_agent", "explanation_agent")

    return workflow.compile()
