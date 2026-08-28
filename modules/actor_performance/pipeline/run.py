"""Blocking pipeline stages against the Neo4j sidecar (drives promg).

The @job in module.py orchestrates these through ``asyncio.to_thread`` so it can report
progress and honour cancellation between stages. Everything here assumes the module's
own venv (promg + pinned neo4j/pandas — see manifest) and runs inside the subprocess
worker.

promg 1.0.10 compatibility applied at import time, worker-local:

- ``apoc.load.csv`` moved to APOC *Extended* in Neo4j 5; the import query is replaced
  with built-in ``LOAD CSV`` (see queries.load_csv_import_query) so the sidecar runs the
  stock community image with APOC core only.
- ``Performance`` (a promg singleton wired into its internals via decorators) is primed
  with ``write_console=False`` so it never hijacks the worker's stdout.
- ``DBManagement.clear_db(replace=True)`` would issue the Enterprise-only
  ``CREATE OR REPLACE DATABASE``; we always pass ``replace=False`` (APOC batch delete)
  and create community-safe indexes instead of its NODE KEY constraint.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from promg import DatabaseConnection, Performance  # type: ignore[import-not-found]
from promg.cypher_queries.data_importer_ql import (  # type: ignore[import-not-found]
    DataImporterQueryLibrary,
)
from promg.data_managers.datastructures import (  # type: ignore[import-not-found]
    DatasetDescriptions,
)
from promg.data_managers.semantic_header import SemanticHeader  # type: ignore[import-not-found]
from promg.database_managers.db_connection import Query  # type: ignore[import-not-found]
from promg.facades.oced_pg import OcedPg  # type: ignore[import-not-found]
from promg.modules.db_management import DBManagement  # type: ignore[import-not-found]
from promg.modules.task_identification import (  # type: ignore[import-not-found]
    TaskIdentification,
)
from promg.utilities.configuration import Configuration  # type: ignore[import-not-found]

from . import queries
from .connection import GraphSettings
from .header_gen import CASE_ENTITY, RESOURCE_ENTITY, write_config_files
from .queries import QuerySpec

# Prime the Performance singleton before any promg call: its @track decorators
# lazily construct it with write_console=True, which permanently redirects the
# worker's stdout through its tqdm shim.
Performance(perf_path=None, write_console=False)


def _patched_load_csv(labels: str, file_name: str, mapping: str) -> Query:
    spec = queries.load_csv_import_query(labels=labels, file_name=file_name)
    return Query(query_str=spec["query_str"], parameters=spec["parameters"])


DataImporterQueryLibrary.get_create_nodes_by_loading_csv_query = staticmethod(_patched_load_csv)


class GraphPipeline:
    """One analysis run against the (single, shared) sidecar database.

    The graph is transient scratch: ``clear()`` runs first and ``wipe()`` last, and the
    caller holds the module-global lock for the whole run, so nothing user-identifiable
    outlives the job and concurrent runs never interleave.
    """

    def __init__(self, settings: GraphSettings, dataset_name: str, workdir: Path) -> None:
        self.settings = settings
        self.dataset_name = dataset_name
        self.workdir = workdir
        # Construct directly: promg's `set_up_connection` passes config.user as db_name.
        self.connection = DatabaseConnection(
            uri=settings.uri,
            db_name="neo4j",
            user=settings.user,
            password=settings.password,
            verbose=False,
            batch_size=10_000,
        )
        self._config: Configuration | None = None

    # ── promg exec adapter ──────────────────────────────────────────────────────

    def _exec(self, spec: QuerySpec) -> list[dict[str, Any]]:
        result = self.connection.exec_query(
            lambda: Query(query_str=spec["query_str"], parameters=spec["parameters"])
        )
        return result or []

    # ── stages ──────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        db_manager = DBManagement(self.connection)
        db_manager.clear_db(replace=False)
        for spec in queries.constraint_queries():
            self._exec(spec)

    def _promg_config(self, csv_dir: Path, csv_name: str) -> Configuration:
        if self._config is None:
            header_path, ds_path = write_config_files(
                self.workdir / "promg_config", self.dataset_name, csv_dir, csv_name
            )
            self._config = Configuration(
                semantic_header_path=header_path,
                dataset_description_path=ds_path,
                db_name="neo4j",
                uri=self.settings.uri,
                user=self.settings.user,
                password=self.settings.password,
                verbose=False,
                batch_size=10_000,
                use_sample=False,
                use_preprocessed_files=False,
                import_directory=self.settings.import_dir,
            )
        return self._config

    def build_graph(self, csv_dir: Path, csv_name: str) -> None:
        """Import the prepared CSV and construct Event/Entity nodes + DF edges."""
        config = self._promg_config(csv_dir, csv_name)
        semantic_header = SemanticHeader.create_semantic_header(config=config)
        dataset_descriptions = DatasetDescriptions(config=config)
        oced_pg = OcedPg(
            database_connection=self.connection,
            semantic_header=semantic_header,
            dataset_descriptions=dataset_descriptions,
            store_files=False,
            use_sample=False,
            use_preprocessed_files=False,
            import_directory=str(config.import_directory),
        )
        oced_pg.load()
        oced_pg.transform()
        oced_pg.create_df_edges()

    def build_tasks(self, csv_dir: Path, csv_name: str) -> None:
        config = self._promg_config(csv_dir, csv_name)
        semantic_header = SemanticHeader.create_semantic_header(config=config)
        TaskIdentification(
            db_connection=self.connection,
            semantic_header=semantic_header,
            resource=RESOURCE_ENTITY,
            case=CASE_ENTITY,
        ).identify_tasks()

    def classify_behavior(self) -> None:
        for spec in queries.behavior_queries():
            self._exec(spec)

    def graph_stats(self) -> dict[str, int]:
        rows = self._exec(queries.graph_stats_query())
        row = rows[0] if rows else {}
        return {
            "task_instances": int(row.get("task_instances") or 0),
            "df_case_edges": int(row.get("df_case_edges") or 0),
            "df_resource_edges": int(row.get("df_resource_edges") or 0),
        }

    def list_edges(self, min_freq: int, limit: int) -> list[dict[str, Any]]:
        return self._exec(queries.list_edges_query(min_freq=min_freq, limit=limit))

    def extract_edge(self, edge: dict[str, Any]) -> list[dict[str, Any]]:
        return self._exec(
            queries.edge_instances_query(
                activity1=str(edge["activity1"]),
                lifecycle1=str(edge.get("lifecycle1") or ""),
                activity2=str(edge["activity2"]),
                lifecycle2=str(edge.get("lifecycle2") or ""),
            )
        )

    def wipe(self) -> None:
        DBManagement(self.connection).clear_db(replace=False)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.connection.close_connection()
