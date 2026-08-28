"""Server-side dataset layer (Phase 2).

A *dataset* is a module's named, typed data output. This package promotes the
Phase-1 client-side normalization to the server: it resolves a module dataset
to a canonical :class:`DatasetEnvelope` and applies transform chains over it
(DuckDB). The envelope JSON intentionally matches the TS ``DatasetEnvelope`` shape
so the same generic-viz components render it.
"""
