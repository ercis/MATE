"""Cypher for behavior classification + extraction — rewritten for this module.

Provenance: the classification semantics follow the reference implementation of
Klijn / Tentina / Fahland / Mannhardt, "Decomposing Process Performance based on
Actor Behavior" (ICPM 2024), https://github.com/multi-dimensional-process-mining/
ekg_bpic17_decomposing_performance_by_actor_behavior. The upstream repo carries no
license file, so the queries are rewritten rather than vendored, with two fixes:

- the task-instance DF label is computed directly (``DF_TI_{Type}``, promg's own
  convention) — upstream calls a ``get_df_ti_label()`` that promg 1.0.10 lacks;
- activity/lifecycle values travel as bolt parameters instead of string-templated
  literals (quote-safe), and durations come back as epoch-millisecond diffs instead
  of Duration objects (exact, no lossy client-side parsing).

Builders return plain ``{"query_str": ..., "parameters": ...}`` dicts so this module
stays importable (and unit-testable) without promg; ``run.py`` adapts them to promg
``Query`` objects at execution time. Labels are baked from ``header_gen`` constants —
they are fixed identifiers, never user input.

The five behavior classes, applied in this order (later classifications overwrite
earlier ones on the same edge, mirroring the reference implementation):

1. ``continuation``            - same actor did e1 and e2 back-to-back in their own stream
2. ``interruption``            - same actor did both, but worked on something else between
3. ``handover_idle``           - different actor picked up e2 after having been idle
                                 (their previous task ended before this one), or e2 is the
                                 actor's first event in the log
4. ``handover_prioritized``    - different actor picked e2 up while mid another task
5. ``handover_deprioritized``  - different actor let e2 wait until after a later task
"""

from __future__ import annotations

from string import Template
from typing import Any, TypedDict

from .header_gen import CASE_ENTITY, DF_CASE, DF_RESOURCE, DF_TI_RESOURCE, RESOURCE_ENTITY


class QuerySpec(TypedDict):
    query_str: str
    parameters: dict[str, Any] | None


def _spec(
    template: str, subs: dict[str, str] | None = None, parameters: dict[str, Any] | None = None
) -> QuerySpec:
    query_str = Template(template).safe_substitute(subs or {})
    return {"query_str": query_str, "parameters": parameters}


_LABELS = {
    "df_case": DF_CASE,
    "df_resource": DF_RESOURCE,
    "df_ti_resource": DF_TI_RESOURCE,
    "resource_label": RESOURCE_ENTITY,
    "case_label": CASE_ENTITY,
}


# ── community-safe constraints (promg's set_constraints uses an Enterprise NODE KEY) ──


def constraint_queries() -> list[QuerySpec]:
    return [
        _spec("CREATE INDEX entity_sys_id_index IF NOT EXISTS FOR (n:Entity) ON (n.sysId)"),
        _spec(
            "CREATE CONSTRAINT record_id_unique IF NOT EXISTS "
            "FOR (r:Record) REQUIRE r.recordId IS UNIQUE"
        ),
        _spec("CREATE INDEX record_id_as_index IF NOT EXISTS FOR (r:Record) ON (r.recordId)"),
        _spec("CREATE INDEX load_status_as_index IF NOT EXISTS FOR (r:Record) ON (r.loadStatus)"),
    ]


# ── import (replaces promg's apoc.load.csv, which moved to APOC Extended in 5.x) ──────


def load_csv_import_query(labels: str, file_name: str) -> QuerySpec:
    """Create Record nodes from a server-side CSV with the built-in LOAD CSV.

    The prepared CSV is all-strings; the only typed columns are the two ints promg
    itself appends (loadStatus, index) — cast explicitly. Rides apoc.periodic.iterate
    (APOC core) for batching, like the rest of promg's import.

    ``lifecycle`` is coalesced to "": LOAD CSV reads empty fields as null, a null
    lifecycle never becomes an Event property, and promg's task-variant expression
    (``activity+'+'+lifecycle``) would then build a null-containing list — which
    Cypher refuses to store, silently zeroing out task instances inside
    apoc.periodic.iterate. Hit by any log without (or with partial) lifecycle data.
    """
    return _spec(
        """
        CALL apoc.periodic.iterate(
            'LOAD CSV WITH HEADERS FROM "file:///$file_name" AS row RETURN row',
            'CREATE (record:$labels)
             SET record += row,
                 record.loadStatus = toInteger(row.loadStatus),
                 record.index = toInteger(row.index),
                 record.lifecycle = coalesce(row.lifecycle, "")'
        , {batchSize:10000, parallel:true, retries: 1})
        """,
        {"file_name": file_name, "labels": labels},
    )


# ── behavior classification (SET df.actor_behavior, in reference order) ───────────────


def behavior_queries() -> list[QuerySpec]:
    continuation = _spec(
        """
        MATCH (e1:Event)-[df:$df_case]->(e2:Event) WHERE (e1)-[:$df_resource]->(e2)
        SET df.actor_behavior = "continuation"
        """,
        _LABELS,
    )
    interruption = _spec(
        """
        MATCH (e1:Event)-[df:$df_case]->(e2:Event)
            WHERE (e1)-[:CORR]->(:$resource_label)<-[:CORR]-(e2)
              AND NOT (e1)-[:$df_resource]->(e2)
        SET df.actor_behavior = "interruption"
        """,
        _LABELS,
    )
    handover_idle = _spec(
        """
        CALL {
            MATCH (tic:TaskInstance)-[:CONTAINS]->(e1:Event)-[df:$df_case]->(e2:Event)
                <-[:CONTAINS]-(ti:TaskInstance)<-[:$df_ti_resource]-(tir:TaskInstance)
            WHERE NOT (e1)-[:CORR]->(:$resource_label)<-[:CORR]-(e2)
              AND tir.end_time < tic.end_time
            RETURN df
        UNION
            MATCH (e1:Event)-[df:$df_case]->(e2:Event) WHERE NOT ()-[:$df_resource]->(e2)
            RETURN df
        }
        SET df.actor_behavior = "handover_idle"
        """,
        _LABELS,
    )
    handover_prioritized = _spec(
        """
        MATCH (tic:TaskInstance)-[:CONTAINS]->(e1:Event)-[df:$df_case]->(e2:Event)
            <-[:CONTAINS]-(ti:TaskInstance)<-[:$df_ti_resource]-(tir:TaskInstance)
        WHERE NOT (e1)-[:CORR]->(:$resource_label)<-[:CORR]-(e2)
          AND tir.start_time < tic.end_time < tir.end_time
        SET df.actor_behavior = "handover_prioritized"
        """,
        _LABELS,
    )
    handover_deprioritized = _spec(
        """
        MATCH (tic:TaskInstance)-[:CONTAINS]->(e1:Event)-[df:$df_case]->(e2:Event)
            <-[:CONTAINS]-(ti:TaskInstance)<-[:$df_ti_resource]-(tir:TaskInstance)
        WHERE NOT (e1)-[:CORR]->(:$resource_label)<-[:CORR]-(e2)
          AND tic.end_time < tir.start_time
        SET df.actor_behavior = "handover_deprioritized"
        """,
        _LABELS,
    )
    return [continuation, interruption, handover_idle, handover_prioritized, handover_deprioritized]


# ── extraction ─────────────────────────────────────────────────────────────────────────


def list_edges_query(min_freq: int, limit: int) -> QuerySpec:
    """Distinct case-DF transitions as (activity, lifecycle) pairs, most frequent first.

    Named ``$edge_limit`` on purpose: promg's ``exec_query`` overwrites any parameter
    whose literal name is ``$limit`` or ``$batch_size`` with its own batch size.
    """
    return _spec(
        """
        MATCH (e1:Event)-[df:$df_case]->(e2:Event)
        WITH e1.activity AS activity1, e1.lifecycle AS lifecycle1,
             e2.activity AS activity2, e2.lifecycle AS lifecycle2, count(*) AS count
        WHERE count >= $$min_freq
        RETURN activity1, lifecycle1, activity2, lifecycle2, count
        ORDER BY count DESC, activity1, lifecycle1, activity2, lifecycle2
        LIMIT $$edge_limit
        """,
        _LABELS,
        parameters={"min_freq": max(int(min_freq), 1), "edge_limit": int(limit)},
    )


def edge_instances_query(
    activity1: str, lifecycle1: str, activity2: str, lifecycle2: str
) -> QuerySpec:
    """Every df-instance of one transition: start epoch-ms, duration-ms, behavior class.

    Values are bolt parameters (quote-safe for arbitrary activity names); the duration
    is an exact epoch-millisecond diff — no Duration-object parsing on the client.
    """
    return _spec(
        """
        MATCH (e1:Event {activity: $$activity1, lifecycle: $$lifecycle1})-[df:$df_case]->
              (e2:Event {activity: $$activity2, lifecycle: $$lifecycle2})
        RETURN e1.timestamp.epochMillis AS start_ms,
               e2.timestamp.epochMillis - e1.timestamp.epochMillis AS duration_ms,
               df.actor_behavior AS actor_behavior
        """,
        _LABELS,
        parameters={
            "activity1": activity1,
            "lifecycle1": lifecycle1,
            "activity2": activity2,
            "lifecycle2": lifecycle2,
        },
    )


def graph_stats_query() -> QuerySpec:
    return _spec(
        """
        CALL {
            MATCH (ti:TaskInstance) RETURN count(ti) AS task_instances
        }
        CALL {
            MATCH ()-[df:$df_case]->() RETURN count(df) AS df_case_edges
        }
        CALL {
            MATCH ()-[dfr:$df_resource]->() RETURN count(dfr) AS df_resource_edges
        }
        RETURN task_instances, df_case_edges, df_resource_edges
        """,
        _LABELS,
    )
