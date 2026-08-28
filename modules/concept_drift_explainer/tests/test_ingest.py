"""Unit tests for document ingestion.

Covers the deterministic, non-LLM parts of [utils/ingest_documents.py](../utils/ingest_documents.py):
filename timestamp parsing, per-file chunk/upsert logic with a fake Pinecone
index, and graceful handling of files with no parseable date prefix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The imports below reach into the module's own venv deps (pypdf et al.), which
# the platform venv does not carry. Skip instead of failing collection so
# `uv run pytest modules/concept_drift_explainer/tests` stays runnable from the
# repo's toolchain; the full suite runs on the module venv.
pytest.importorskip("pypdf")
from modules.concept_drift_explainer.utils.ingest_documents import (
    KB_NS,
    delete_document_vectors,
    get_timestamp_from_filename,
    process_context_files,
    process_glossary_file,
)


class _FakeIndex:
    """Captures upsert / delete calls for assertion."""

    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.deletes: list[dict] = []

    def upsert(self, *, vectors, namespace):
        self.upserts.append({"namespace": namespace, "vectors": list(vectors)})

    def delete(self, *, filter, namespace):
        self.deletes.append({"namespace": namespace, "filter": filter})


class _FakeEmbedder:
    """Deterministic 4-dimensional embeddings keyed on text hash."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(hash(t) % 1000) / 1000.0] * 4 for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(hash(text) % 1000) / 1000.0] * 4


def test_get_timestamp_from_filename_happy():
    ts = get_timestamp_from_filename("2017-10-11_policy_v2.pdf")
    assert ts is not None and ts > 0


def test_get_timestamp_from_filename_rejects_missing_prefix():
    assert get_timestamp_from_filename("policy.pdf") is None
    assert get_timestamp_from_filename("2017_10_11_policy.pdf") is None


def test_process_context_files_skips_files_without_date_prefix(tmp_path: Path):
    no_prefix = tmp_path / "policy.txt"
    no_prefix.write_text("hello")
    result = process_context_files(
        [no_prefix],
        index=_FakeIndex(),
        embedder=_FakeEmbedder(),
        namespace="cde-test",
        api_key="fake",
        cache_dir=tmp_path / "cache",
    )
    assert result["ingested"] == 0
    assert any(s["file"] == "policy.txt" for s in result["skipped"])


def test_process_context_files_ingests_txt(tmp_path: Path):
    txt = tmp_path / "2024-01-01_notes.txt"
    txt.write_text("Lorem ipsum dolor sit amet. " * 30)
    index = _FakeIndex()
    result = process_context_files(
        [txt],
        index=index,
        embedder=_FakeEmbedder(),
        namespace="cde-test",
        api_key="fake",
        cache_dir=tmp_path / "cache",
    )
    assert result["ingested"] >= 1
    assert index.upserts and index.upserts[0]["namespace"] == "cde-test"
    # Metadata carries source filename and the parsed Unix timestamp.
    _, _, meta = index.upserts[0]["vectors"][0]
    assert meta["source"] == "2024-01-01_notes.txt"
    assert isinstance(meta["timestamp"], int)


def test_delete_document_vectors_dispatches_filter(tmp_path: Path):
    index = _FakeIndex()
    delete_document_vectors(
        index, namespace="cde-test", source_document_name="2024-01-01_notes.txt"
    )
    assert index.deletes == [
        {"namespace": "cde-test", "filter": {"source": "2024-01-01_notes.txt"}}
    ]


def test_process_glossary_file_ingests_bpm_kb(tmp_path: Path):
    glossary = tmp_path / "bpm.csv"
    glossary.write_text("term,definition\nKPI,Key performance indicator\n")
    index = _FakeIndex()
    n = process_glossary_file(glossary, index=index, embedder=_FakeEmbedder())
    assert n == 1
    assert index.upserts[0]["namespace"] == KB_NS
