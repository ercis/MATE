"""Tests for the Pinecone helper's dimension-resolution logic.

These tests cover the pure helper that decides what dimension a fresh index
should be created with. Network-touching helpers (``get_pinecone_index`` /
``recreate_index``) are validated against a real account during the
verification run in the README.
"""

from __future__ import annotations

import pytest

from modules.concept_drift_explainer.utils.pinecone_client import (
    DEFAULT_DIMENSION,
    _configured_dimension,
)


@pytest.mark.parametrize(
    "cfg, expected",
    [
        # Empty config → default
        ({}, DEFAULT_DIMENSION),
        # AI block but no dimensions → default
        ({"ai": {}}, DEFAULT_DIMENSION),
        # Embedding model set but no dimensions → default
        ({"ai": {"embedding_model": "text-embedding-3-small"}}, DEFAULT_DIMENSION),
        # Explicit integer dimension
        ({"ai": {"embedding_dimensions": 1024}}, 1024),
        # String integer (e.g. from a number input losing its type round-trip)
        ({"ai": {"embedding_dimensions": "768"}}, 768),
        # Malformed string → default
        ({"ai": {"embedding_dimensions": "abc"}}, DEFAULT_DIMENSION),
        # Non-positive int → default
        ({"ai": {"embedding_dimensions": 0}}, DEFAULT_DIMENSION),
        ({"ai": {"embedding_dimensions": -5}}, DEFAULT_DIMENSION),
        # None → default
        ({"ai": {"embedding_dimensions": None}}, DEFAULT_DIMENSION),
    ],
)
def test_configured_dimension(cfg, expected):
    assert _configured_dimension(cfg) == expected
