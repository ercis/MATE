"""Connection settings resolution + bolt reachability probe.

Resolution order per field: explicit module config → platform environment
(``MATE_NEO4J_URI`` / ``MATE_NEO4J_PASSWORD`` / ``MATE_NEO4J_IMPORT_DIR``, wired in
docker-compose for the "graph" profile) → local-dev default. The subprocess worker
inherits the api process environment, so the env layer works in every mode.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "mate-graph-dev"
DEFAULT_IMPORT_DIR = "./data/neo4j-import"

ENV_URI = "MATE_NEO4J_URI"
ENV_PASSWORD = "MATE_NEO4J_PASSWORD"
ENV_IMPORT_DIR = "MATE_NEO4J_IMPORT_DIR"


@dataclass(frozen=True)
class GraphSettings:
    uri: str
    user: str
    password: str
    import_dir: Path


def resolve_settings(
    config: Mapping[str, Any] | None,
    env: Mapping[str, str] | None = None,
) -> GraphSettings:
    """Merge module config over environment defaults over local-dev defaults."""
    cfg = config or {}
    e = os.environ if env is None else env

    def pick(cfg_key: str, env_key: str | None, default: str) -> str:
        value = str(cfg.get(cfg_key) or "").strip()
        if value:
            return value
        if env_key:
            env_value = str(e.get(env_key) or "").strip()
            if env_value:
                return env_value
        return default

    return GraphSettings(
        uri=pick("bolt_uri", ENV_URI, DEFAULT_URI),
        user=pick("username", None, DEFAULT_USER),
        password=pick("password", ENV_PASSWORD, DEFAULT_PASSWORD),
        import_dir=Path(pick("import_dir", ENV_IMPORT_DIR, DEFAULT_IMPORT_DIR)),
    )


def ping(settings: GraphSettings, timeout_seconds: float = 3.0) -> tuple[bool, str]:
    """(reachable, detail). Auth failures count as reachable-but-misconfigured
    and return a distinct message so the panel can render the right hint."""
    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import AuthError
    except ImportError as exc:  # pragma: no cover - driver is a manifest dep
        return False, f"neo4j driver missing: {exc}"

    try:
        driver = GraphDatabase.driver(
            settings.uri,
            auth=(settings.user, settings.password),
            connection_timeout=timeout_seconds,
        )
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True, "ok"
    except AuthError:
        return False, "auth-failed"
    except Exception as exc:
        return False, f"unreachable: {type(exc).__name__}"
