"""End-to-end pipeline test against a live Neo4j sidecar.

Skipped unless both are true:
- ``NEO4J_TEST_URI`` (+ ``NEO4J_TEST_PASSWORD``, ``NEO4J_TEST_IMPORT_DIR``) point at a
  running server whose import directory is the given local path, e.g. the container
  from modules/actor_performance/README.md;
- promg is importable (it lives in the module's own venv, not the platform's - run
  with ``uv run --with promg==1.0.10 --with 'neo4j>=5.15,<6' --with 'pandas<3' ...``
  or inside the module venv).

The synthetic 3-case log is crafted so each behavior class is deterministic:
c1 A→B same actor with other work in between (interruption), c2 A→B handover to an
actor with no prior work (handover_idle), c3 A→B same actor back-to-back
(continuation).
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pandas as pd
import pytest

TEST_URI = os.environ.get("NEO4J_TEST_URI")
TEST_IMPORT_DIR = os.environ.get("NEO4J_TEST_IMPORT_DIR")

pytestmark = [
    pytest.mark.skipif(not TEST_URI, reason="NEO4J_TEST_URI not set"),
    pytest.mark.skipif(not TEST_IMPORT_DIR, reason="NEO4J_TEST_IMPORT_DIR not set"),
    pytest.mark.skipif(
        importlib.util.find_spec("promg") is None,
        reason="promg not importable (module venv only)",
    ),
]


def _synthetic_log() -> pd.DataFrame:
    t = pd.Timestamp("2024-01-01 09:00:00", tz="UTC")

    def minutes(m: int) -> pd.Timestamp:
        return t + pd.Timedelta(minutes=m)

    rows = [
        {"case_id": "c1", "activity": "A", "timestamp": minutes(0), "resource": "alice"},
        {"case_id": "c2", "activity": "A", "timestamp": minutes(10), "resource": "alice"},
        {"case_id": "c1", "activity": "B", "timestamp": minutes(20), "resource": "alice"},
        {"case_id": "c2", "activity": "B", "timestamp": minutes(30), "resource": "bob"},
        {"case_id": "c3", "activity": "A", "timestamp": minutes(40), "resource": "bob"},
        {"case_id": "c3", "activity": "B", "timestamp": minutes(50), "resource": "bob"},
    ]
    return pd.DataFrame(rows)


def test_pipeline_end_to_end(tmp_path: Path):
    from modules.actor_performance.pipeline import results as agg
    from modules.actor_performance.pipeline.connection import GraphSettings
    from modules.actor_performance.pipeline.prep import build_input_csv
    from modules.actor_performance.pipeline.run import GraphPipeline

    settings = GraphSettings(
        uri=str(TEST_URI),
        user=os.environ.get("NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("NEO4J_TEST_PASSWORD", "phase0-baseline"),
        import_dir=Path(str(TEST_IMPORT_DIR)),
    )

    csv_dir = tmp_path / "input"
    csv_name = f"it_{uuid.uuid4().hex[:6]}.csv"
    stats = build_input_csv(_synthetic_log(), csv_dir / csv_name)
    assert stats.events == 6

    pipeline = GraphPipeline(settings=settings, dataset_name="mate_it", workdir=tmp_path)
    try:
        pipeline.clear()
        pipeline.build_graph(csv_dir, csv_name)
        pipeline.build_tasks(csv_dir, csv_name)
        pipeline.classify_behavior()

        graph_stats = pipeline.graph_stats()
        assert graph_stats["df_case_edges"] == 3  # one A→B per case
        assert graph_stats["task_instances"] == 5

        edge_keys = pipeline.list_edges(min_freq=0, limit=10)
        assert len(edge_keys) == 1  # all three cases share the A→B transition
        edges = [agg.aggregate_edge(k, pipeline.extract_edge(k)) for k in edge_keys]

        totals = agg.behavior_totals(edges)
        assert totals["continuation"]["count"] == 1
        assert totals["interruption"]["count"] == 1
        assert totals["handover_idle"]["count"] == 1
        assert agg.UNCLASSIFIED not in totals
    finally:
        pipeline.wipe()
        pipeline.close()
