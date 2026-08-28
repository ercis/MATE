"""0012_card_policies data migration - split module/setting locks into cards.

Drives the real ``upgrade``/``downgrade`` against an in-memory SQLite by
monkeypatching the migration's ``op`` proxy onto an Operations bound to our own
connection, so it needs no DATABASE_URL juggling or full alembic run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0012_card_policies.py"
    spec = importlib.util.spec_from_file_location("mig_0012", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str, mig, monkeypatch: pytest.MonkeyPatch, conn) -> None:
    ops = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(mig, "op", ops)
    getattr(mig, fn)()


def _blob(v: object) -> object:
    return json.loads(v) if isinstance(v, str) else v


def test_card_migration_split_and_recombine(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE control_policies ("
            "scope TEXT, key TEXT, control_mode TEXT, admin_value_json JSON,"
            " updated_by TEXT, updated_at DATETIME, PRIMARY KEY (scope, key))"
        )
        seed = sa.text(
            "INSERT INTO control_policies (scope,key,control_mode,admin_value_json,updated_at)"
            " VALUES (:s,:k,:m,:v, datetime('now'))"
        )
        # legacy whole-module lock (config props only - the finding-2 reality)
        conn.execute(
            seed, {"s": "module", "k": "cv4cdd", "m": "admin", "v": json.dumps({"n_windows": 5})}
        )
        # legacy model pin (the cv4cdd.model setting, bare string)
        conn.execute(
            seed, {"s": "setting", "k": "cv4cdd.model", "m": "admin", "v": json.dumps("model-x")}
        )
        # a module lock that carried ai + model keys too
        conn.execute(
            seed,
            {
                "s": "module",
                "k": "expl",
                "m": "admin",
                "v": json.dumps({"max_causes": 3, "ai": {"k": 1}, "model": "leg"}),
            },
        )
        # unrelated server setting - must be untouched
        conn.execute(
            seed, {"s": "setting", "k": "ai.config", "m": "admin", "v": json.dumps({"p": "openai"})}
        )

        _run("upgrade", mig, monkeypatch, conn)

        rows = {
            (r[0], r[1]): r[2]
            for r in conn.execute(
                sa.text("SELECT scope,key,admin_value_json FROM control_policies")
            )
        }
        assert ("module", "cv4cdd") not in rows
        assert ("setting", "cv4cdd.model") not in rows
        assert ("setting", "ai.config") in rows  # untouched
        assert _blob(rows[("card", "cv4cdd:config")]) == {"n_windows": 5}
        assert _blob(rows[("card", "cv4cdd:model")]) == {"model": "model-x"}
        assert _blob(rows[("card", "expl:config")]) == {"max_causes": 3}
        assert _blob(rows[("card", "expl:ai")]) == {"ai": {"k": 1}}
        assert _blob(rows[("card", "expl:model")]) == {"model": "leg"}

        # idempotent re-run
        _run("upgrade", mig, monkeypatch, conn)

        # downgrade recombines card rows back into module blobs + .model settings
        _run("downgrade", mig, monkeypatch, conn)
        rows2 = {
            (r[0], r[1]): r[2]
            for r in conn.execute(
                sa.text("SELECT scope,key,admin_value_json FROM control_policies")
            )
        }
        assert not any(scope == "card" for scope, _ in rows2)
        assert ("module", "cv4cdd") in rows2
        assert _blob(rows2[("setting", "cv4cdd.model")]) == "model-x"
