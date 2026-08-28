"""Cached embedding helper.

Wraps any LangChain ``Embeddings`` instance with a per-invocation memo table
so the same drift phrase isn't re-embedded within a single pipeline run.
The concrete OpenAI model is chosen upstream from the module's own config -
see [llm.py](./llm.py).
"""

from __future__ import annotations

import numpy as np
from langchain_core.embeddings import Embeddings


class Embedder:
    """Per-invocation embedding helper bound to an arbitrary LangChain client."""

    def __init__(self, embeddings: Embeddings) -> None:
        self._embedder = embeddings
        self._cache: dict[str, np.ndarray] = {}

    def get(self, text: str) -> np.ndarray:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vec = np.array(self._embedder.embed_query(text), dtype=float)
        self._cache[text] = vec
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed_query(text)
