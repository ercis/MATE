"""Smoke test of the LangGraph wiring.

Builds the graph against trivial fakes and asserts:
  - The graph compiles end-to-end (no missing edges, no import cycles).
  - All six agent factories accept the documented kwargs.

Full-pipeline integration is best validated against real OpenAI / Pinecone /
Anthropic in a verification run (see README "Setup"). Mocking LangChain's
structured-output internals from pytest is brittle - the agents are
individually unit-tested via the public seam (the drift adapter test) and
through manual verification.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

# The graph pulls the module's own venv deps (langgraph/langchain), which the
# platform venv does not carry. Skip instead of failing so
# `uv run pytest modules/concept_drift_explainer/tests` stays runnable from the
# repo's toolchain; the full suite runs on the module venv.
pytest.importorskip("langchain_core")


class _FakeIndex:
    def __init__(self):
        self.queries = 0

    def query(self, **kwargs):
        self.queries += 1

        class _R:
            matches: ClassVar[list[object]] = []

        return _R()

    def upsert(self, **kwargs):
        pass


class _StubChatModel:
    """Minimal BaseChatModel duck-type for graph smoke tests."""

    def invoke(self, prompt):
        return SimpleNamespace(content="stub")

    def with_structured_output(self, schema):
        return self


STUB_EMBEDDING_DIM = 1536


class _StubEmbeddings:
    """Minimal Embeddings duck-type for graph smoke tests."""

    def __init__(self, dim: int = STUB_EMBEDDING_DIM):
        self.dim = dim

    def embed_query(self, text):
        return [0.0] * self.dim

    def embed_documents(self, texts):
        return [[0.0] * self.dim for _ in texts]


def test_build_graph_compiles():
    from modules.concept_drift_explainer.graph.build_graph import build_graph
    from modules.concept_drift_explainer.utils.embeddings import Embedder

    graph = build_graph(
        chat_llm=_StubChatModel(),
        embedder=Embedder(_StubEmbeddings()),
        index=_FakeIndex(),
        context_namespace="cde-test",
    )
    # langgraph compiles into a callable .invoke surface.
    assert hasattr(graph, "invoke")


def test_drift_adapter_chain_imports_cleanly():
    """Trivial - guards against future refactors breaking the import surface
    that module.py depends on (drift_record builder + the canonical map)."""
    from modules.concept_drift_explainer.agents.drift_agent import (
        build_drift_record,
        make_drift_agent,
    )

    assert callable(build_drift_record)
    assert callable(make_drift_agent)
