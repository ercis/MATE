"""Unit tests for the promg-free pipeline pieces: CSV prep, header generation,
query building, result aggregation, and the /results staleness guard.

No Neo4j, no promg, no ModuleContext - these run against the platform venv.
The full pipeline (graph build → classification → extraction) is covered by the
NEO4J_TEST_URI-gated integration test in test_integration_neo4j.py.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

# Importing the module-private guard is intentional: the /results staleness check
# is factored into this pure helper precisely so it's unit-testable.
from modules.actor_performance.module import (  # pyright: ignore[reportPrivateUsage]
    RESULT_SCHEMA,
    _result_is_current,
)
from modules.actor_performance.pipeline import header_gen, queries, results
from modules.actor_performance.pipeline.connection import resolve_settings
from modules.actor_performance.pipeline.prep import build_input_csv

# ── prep ────────────────────────────────────────────────────────────────────


def _log(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_build_input_csv_maps_canonical_columns(tmp_path):
    df = _log(
        [
            {
                "case_id": "c1",
                "activity": "A",
                "timestamp": pd.Timestamp("2024-01-01 10:00:00.250", tz="UTC"),
                "resource": "alice",
                "lifecycle": "complete",
            },
            {
                "case_id": "c1",
                "activity": "B",
                "timestamp": pd.Timestamp("2024-01-01 11:30:00", tz="UTC"),
                "resource": "bob",
                "lifecycle": "complete",
            },
        ]
    )
    out = tmp_path / "log.csv"
    stats = build_input_csv(df, out)

    written = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert list(written.columns) == ["case", "activity", "timestamp", "lifecycle", "resource"]
    # millisecond precision, slash format, no offset in the string (appended by promg)
    assert written["timestamp"].tolist() == ["2024/01/01 10:00:00.250", "2024/01/01 11:30:00.000"]
    assert stats.events == 2
    assert stats.cases == 1
    assert stats.resources == 2
    assert stats.has_lifecycle is True


def test_build_input_csv_synthesizes_lifecycle_and_drops_null_resource(tmp_path):
    df = _log(
        [
            {
                "case_id": "c1",
                "activity": "A",
                "timestamp": "2024-01-01 10:00",
                "resource": "alice",
            },
            {"case_id": "c1", "activity": "B", "timestamp": "2024-01-01 11:00", "resource": None},
        ]
    )
    out = tmp_path / "log.csv"
    stats = build_input_csv(df, out)

    written = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert len(written) == 1
    assert written["lifecycle"].tolist() == [""]
    assert stats.dropped_null_resource == 1
    assert stats.has_lifecycle is False


def test_build_input_csv_naive_timestamps_treated_as_utc(tmp_path):
    df = _log(
        [{"case_id": "c", "activity": "A", "timestamp": "2024-06-01 12:00:00", "resource": "r"}]
    )
    out = tmp_path / "log.csv"
    build_input_csv(df, out)
    written = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert written["timestamp"].tolist() == ["2024/06/01 12:00:00.000"]


def test_build_input_csv_missing_required_column(tmp_path):
    df = _log([{"case_id": "c", "activity": "A", "timestamp": "2024-01-01"}])
    with pytest.raises(ValueError, match="resource"):
        build_input_csv(df, tmp_path / "log.csv")


# ── header generation ───────────────────────────────────────────────────────


def test_semantic_header_shape():
    header = header_gen.semantic_header("mate_test")
    types = [n["type"] for n in header["nodes"]]
    assert types == ["Event", "Case", "Resource"]
    for node in header["nodes"][1:]:
        assert node["infer_df"] is True
        assert node["include_label_in_df"] is True
    assert header_gen.DF_CASE == "DF_CASE"
    assert header_gen.DF_TI_RESOURCE == "DF_TI_Resource"


def test_write_config_files_round_trip(tmp_path):
    header_path, ds_path = header_gen.write_config_files(
        tmp_path, "mate_test", tmp_path / "csv", "log.csv"
    )
    header = json.loads(header_path.read_text())
    ds = json.loads(ds_path.read_text())
    assert header["name"] == "mate_test"
    assert ds[0]["file_name"] == "log.csv"
    attr_names = [a["name"] for a in ds[0]["attributes"]]
    assert attr_names == ["activity", "lifecycle", "timestamp", "case", "resource"]
    ts = next(a for a in ds[0]["attributes"] if a["name"] == "timestamp")
    assert ts["datetime_object"]["format"] == header_gen.TIMESTAMP_FORMAT


# ── queries ─────────────────────────────────────────────────────────────────


def test_behavior_queries_labels_and_order():
    specs = queries.behavior_queries()
    texts = [s["query_str"] for s in specs]
    assert len(texts) == 5
    assert '"continuation"' in texts[0]
    assert '"interruption"' in texts[1]
    assert '"handover_idle"' in texts[2]
    assert '"handover_prioritized"' in texts[3]
    assert '"handover_deprioritized"' in texts[4]
    for text in texts:
        assert "DF_CASE" in text
        assert "$df_case" not in text  # all template holes filled
    # the task-instance label promg 1.0.10 cannot produce itself
    assert "DF_TI_Resource" in texts[2]


def test_edge_instances_query_uses_bolt_parameters():
    spec = queries.edge_instances_query('A "quoted"', "complete", "B", "")
    assert spec["parameters"] == {
        "activity1": 'A "quoted"',
        "lifecycle1": "complete",
        "activity2": "B",
        "lifecycle2": "",
    }
    # values must NOT be interpolated into the query text
    assert "quoted" not in spec["query_str"]
    assert "$activity1" in spec["query_str"]
    assert "epochMillis" in spec["query_str"]


def test_list_edges_query_avoids_promg_param_hijack():
    spec = queries.list_edges_query(min_freq=0, limit=200)
    # promg's exec_query overwrites parameters literally named $limit/$batch_size
    assert "$limit" not in spec["query_str"].replace("$edge_limit", "")
    assert "$batch_size" not in spec["query_str"]
    assert spec["parameters"] == {"min_freq": 1, "edge_limit": 200}


def test_load_csv_import_query_casts_promg_int_columns():
    spec = queries.load_csv_import_query("Record:EventRecord", "log.csv")
    assert 'LOAD CSV WITH HEADERS FROM "file:///log.csv"' in spec["query_str"]
    assert "toInteger(row.loadStatus)" in spec["query_str"]
    assert "toInteger(row.index)" in spec["query_str"]
    # LOAD CSV yields null for empty fields; a null lifecycle silently breaks
    # promg's task-instance creation (null-containing variant lists).
    assert 'coalesce(row.lifecycle, "")' in spec["query_str"]


# ── aggregation ─────────────────────────────────────────────────────────────


def _edge_key() -> dict[str, str]:
    return {"activity1": "A", "lifecycle1": "", "activity2": "B", "lifecycle2": ""}


def test_aggregate_edge_counts_percentages_and_hours():
    rows = [
        {"start_ms": 0, "duration_ms": 3_600_000, "actor_behavior": "continuation"},
        {"start_ms": 0, "duration_ms": 7_200_000, "actor_behavior": "continuation"},
        {"start_ms": 0, "duration_ms": 36_000_000, "actor_behavior": "handover_idle"},
    ]
    edge = results.aggregate_edge(_edge_key(), rows)
    assert edge["count"] == 3
    b = edge["behaviors"]
    assert b["all"]["count"] == 3
    assert b["continuation"]["count"] == 2
    assert b["continuation"]["percentage"] == pytest.approx(2 / 3)
    assert b["continuation"]["mean_hours"] == pytest.approx(1.5)
    assert b["handover_idle"]["mean_hours"] == pytest.approx(10.0)
    assert b["all"]["mean_hours"] == pytest.approx((1 + 2 + 10) / 3)
    assert b["all"]["median_hours"] == pytest.approx(2.0)


def test_aggregate_edge_buckets_unclassified():
    rows = [{"start_ms": 0, "duration_ms": 1000, "actor_behavior": None}]
    edge = results.aggregate_edge(_edge_key(), rows)
    assert edge["behaviors"][results.UNCLASSIFIED]["count"] == 1


def test_behavior_totals_weighted_across_edges():
    edges = [
        results.aggregate_edge(
            _edge_key(),
            [
                {"start_ms": 0, "duration_ms": 3_600_000, "actor_behavior": "continuation"},
                {"start_ms": 0, "duration_ms": 3_600_000, "actor_behavior": "handover_idle"},
            ],
        ),
        results.aggregate_edge(
            _edge_key(),
            [{"start_ms": 0, "duration_ms": 10_800_000, "actor_behavior": "continuation"}],
        ),
    ]
    totals = results.behavior_totals(edges)
    assert totals["continuation"]["count"] == 2
    assert totals["continuation"]["percentage"] == pytest.approx(2 / 3)
    assert totals["continuation"]["mean_hours"] == pytest.approx(2.0)
    assert totals["handover_idle"]["count"] == 1


# ── settings + staleness guard ──────────────────────────────────────────────


def test_resolve_settings_precedence():
    env = {
        "MATE_NEO4J_URI": "bolt://envhost:7687",
        "MATE_NEO4J_PASSWORD": "env-secret",
        "MATE_NEO4J_IMPORT_DIR": "/env/import",
    }
    s = resolve_settings({"bolt_uri": "bolt://cfg:7687", "password": ""}, env=env)
    assert s.uri == "bolt://cfg:7687"  # explicit config wins
    assert s.password == "env-secret"  # empty config falls through to env
    assert str(s.import_dir) == "/env/import"

    s2 = resolve_settings({}, env={})
    assert s2.uri == "bolt://localhost:7687"
    assert s2.user == "neo4j"


def test_result_is_current():
    good = {
        "schema": RESULT_SCHEMA,
        "params": {},
        "input": {},
        "graph": {},
        "behavior_totals": {},
        "edges": [],
    }
    assert _result_is_current(good)
    assert not _result_is_current({**good, "schema": RESULT_SCHEMA - 1})
    assert not _result_is_current({k: v for k, v in good.items() if k != "edges"})
    assert not _result_is_current(None)
