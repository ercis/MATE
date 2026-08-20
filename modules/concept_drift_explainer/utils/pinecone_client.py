"""Pinecone index helper.

Reads `pinecone_api_key` and `pinecone_index_name` from the module's config
(rendered by the platform from `manifest.yaml::config_schema`) and returns a
ready-to-query `Index` handle. Creates the index on first use, matching the
original repository's serverless defaults (aws / us-east-1, cosine).

The vector ``dimension`` is taken from the module's own embedding config
(``cfg["ai"]["embedding_dimensions"]``) and falls back to 1536 - the native
size of OpenAI's ``text-embedding-3-small`` - when unset.
"""

from __future__ import annotations

from fastapi import HTTPException
from pinecone import Pinecone, ServerlessSpec

DEFAULT_DIMENSION = 1536
DEFAULT_INDEX_NAME = "conceptdriftexplainer"


def _configured_dimension(cfg: dict) -> int:
    """Return the embedding dimension configured for this module, else 1536."""
    ai = (cfg or {}).get("ai") or {}
    dim = ai.get("embedding_dimensions")
    if isinstance(dim, str) and dim.strip():
        try:
            dim = int(dim)
        except ValueError:
            return DEFAULT_DIMENSION
    if isinstance(dim, int) and dim > 0:
        return dim
    return DEFAULT_DIMENSION


def _require_api_key(cfg: dict) -> str:
    api_key = cfg.get("pinecone_api_key")
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail=(
                "Pinecone API key missing. Save one under Settings → Modules "
                "→ Concept Drift Explainer."
            ),
        )
    return api_key


def get_pinecone_index(cfg: dict):
    api_key = _require_api_key(cfg)
    index_name = cfg.get("pinecone_index_name") or DEFAULT_INDEX_NAME
    dimension = _configured_dimension(cfg)

    pc = Pinecone(api_key=api_key)
    try:
        existing = {idx.name for idx in pc.list_indexes()}
    except Exception:  # pragma: no cover - older SDKs
        existing = set(pc.list_indexes().names())  # type: ignore[attr-defined]

    if index_name not in existing:
        pc.create_index(
            name=index_name,
            dimension=dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)


def recreate_index(cfg: dict) -> dict:
    """Delete (if exists) and recreate the configured Pinecone index.

    Returns ``{"index_name": ..., "dimension": ...}`` so the UI can confirm
    what was provisioned. All previously ingested vectors are lost - the
    caller is responsible for re-ingesting documents afterwards.
    """
    api_key = _require_api_key(cfg)
    index_name = cfg.get("pinecone_index_name") or DEFAULT_INDEX_NAME
    dimension = _configured_dimension(cfg)

    pc = Pinecone(api_key=api_key)
    try:
        existing = {idx.name for idx in pc.list_indexes()}
    except Exception:  # pragma: no cover - older SDKs
        existing = set(pc.list_indexes().names())  # type: ignore[attr-defined]

    if index_name in existing:
        pc.delete_index(index_name)

    pc.create_index(
        name=index_name,
        dimension=dimension,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )

    return {"index_name": index_name, "dimension": dimension}
