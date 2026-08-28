"""per-card module control - migrate module/setting locks to card scope

Revision ID: 0012_card_policies
Revises: 0011_drop_flows
Create Date: 2026-07-12

The admin control framework moves from a coarse whole-module lock (``scope=
"module"``, one blob replacing a module's entire ``config_json``) plus a
hardcoded ``cv4cdd.model`` setting, to one lock per settings *card* (``scope=
"card"``, key ``"<module_id>:<card_id>"``; see ``mate.api.modules.cards``). This
data-only migration converts any existing rows:

* Each ``(module, <mid>)`` lock is split by the ``config_json`` slice each card
  owns: ``ai`` → ``(card, "<mid>:ai")`` = ``{"ai": ...}``; ``model`` →
  ``(card, "<mid>:model")`` = ``{"model": ...}``; every other top-level key →
  ``(card, "<mid>:config")``. In practice the old admin editor only ever wrote
  ``config_schema`` props, so this typically yields just a config card.
* Each ``(setting, "<mid>.model")`` lock (the ``cv4cdd.model`` pin and the
  generic ``<module_id>.model`` overlay) → ``(card, "<mid>:model")`` =
  ``{"model": "<name>"}``.

Idempotent (guards on the live table and skips card rows that already exist) in
the style of the squashed baseline. ``downgrade`` best-effort recombines card
rows back into a whole-module blob (+ ``<mid>.model`` setting for the model
card); it is an emergency inverse, not byte-exact.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_card_policies"
down_revision: str | None = "0011_drop_flows"
branch_labels = None
depends_on = None

# Top-level config_json keys owned by the ai/model cards (plus the loader
# runtime sentinel); everything else is the config card's slice.
_RESERVED = {"ai", "model", "__model_admin_locked__"}


def _table() -> sa.Table:
    return sa.table(
        "control_policies",
        sa.column("scope", sa.String()),
        sa.column("key", sa.String()),
        sa.column("control_mode", sa.String()),
        sa.column("admin_value_json", sa.JSON()),
        sa.column("updated_by", sa.String()),
        sa.column("updated_at", sa.DateTime()),
    )


def _as_obj(value: object) -> object:
    """Defensive JSON decode - the JSON column type usually deserializes, but a
    raw string slips through on some drivers."""
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def upgrade() -> None:
    bind = op.get_bind()
    if "control_policies" not in set(sa.inspect(bind).get_table_names()):
        return
    cp = _table()

    # (scope, key) -> row values to insert; a later entry overwrites an earlier
    # one, so the meaningful `<mid>.model` setting pin wins over the (rare)
    # `model` key inside a module blob.
    new_rows: dict[tuple[str, str], dict[str, object]] = {}
    del_keys: list[tuple[str, str]] = []

    # 1) Whole-module locks → per-card rows.
    for row in bind.execute(sa.select(cp).where(cp.c.scope == "module")).mappings():
        mid = row["key"]
        del_keys.append(("module", mid))
        blob = _as_obj(row["admin_value_json"])
        if not isinstance(blob, dict):
            continue
        common = {
            "control_mode": row["control_mode"],
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }
        if blob.get("ai") is not None:
            new_rows[("card", f"{mid}:ai")] = {
                "scope": "card",
                "key": f"{mid}:ai",
                "admin_value_json": {"ai": blob["ai"]},
                **common,
            }
        if "model" in blob:
            new_rows[("card", f"{mid}:model")] = {
                "scope": "card",
                "key": f"{mid}:model",
                "admin_value_json": {"model": blob["model"]},
                **common,
            }
        config_slice = {k: v for k, v in blob.items() if k not in _RESERVED}
        if config_slice:
            new_rows[("card", f"{mid}:config")] = {
                "scope": "card",
                "key": f"{mid}:config",
                "admin_value_json": config_slice,
                **common,
            }

    # 2) `<mid>.model` setting locks → the model card.
    for row in bind.execute(sa.select(cp).where(cp.c.scope == "setting")).mappings():
        key = row["key"]
        if not key.endswith(".model"):
            continue
        mid = key[: -len(".model")]
        if not mid:
            continue
        del_keys.append(("setting", key))
        val = _as_obj(row["admin_value_json"])
        value = {"model": val} if isinstance(val, str) and val.strip() else {}
        new_rows[("card", f"{mid}:model")] = {
            "scope": "card",
            "key": f"{mid}:model",
            "admin_value_json": value,
            "control_mode": row["control_mode"],
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"],
        }

    for scope, key in del_keys:
        bind.execute(sa.delete(cp).where(cp.c.scope == scope, cp.c.key == key))
    for (scope, key), values in new_rows.items():
        exists = bind.execute(
            sa.select(cp.c.key).where(cp.c.scope == scope, cp.c.key == key)
        ).first()
        if exists is None:
            bind.execute(cp.insert().values(**values))


def downgrade() -> None:
    bind = op.get_bind()
    if "control_policies" not in set(sa.inspect(bind).get_table_names()):
        return
    cp = _table()

    modules: dict[str, dict[str, object]] = {}
    models: dict[str, dict[str, object]] = {}
    del_keys: list[tuple[str, str]] = []

    for row in bind.execute(sa.select(cp).where(cp.c.scope == "card")).mappings():
        mid, sep, cid = str(row["key"]).rpartition(":")
        if not sep or not mid:
            continue
        del_keys.append(("card", row["key"]))
        val = _as_obj(row["admin_value_json"])
        entry = modules.setdefault(
            mid,
            {
                "blob": {},
                "mode": "user",
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            },
        )
        if row["control_mode"] == "admin":
            entry["mode"] = "admin"
        if isinstance(val, dict):
            entry["blob"].update(val)  # type: ignore[attr-defined]
        if cid == "model":
            name = None
            if isinstance(val, dict):
                name = next((v for v in val.values() if isinstance(v, str) and v.strip()), None)
            models[mid] = {
                "control_mode": row["control_mode"],
                "admin_value_json": name,
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }

    for scope, key in del_keys:
        bind.execute(sa.delete(cp).where(cp.c.scope == scope, cp.c.key == key))
    for mid, entry in modules.items():
        bind.execute(
            cp.insert().values(
                scope="module",
                key=mid,
                control_mode=entry["mode"],
                admin_value_json=entry["blob"],
                updated_by=entry["updated_by"],
                updated_at=entry["updated_at"],
            )
        )
    for mid, ms in models.items():
        bind.execute(
            cp.insert().values(
                scope="setting",
                key=f"{mid}.model",
                control_mode=ms["control_mode"],
                admin_value_json=ms["admin_value_json"],
                updated_by=ms["updated_by"],
                updated_at=ms["updated_at"],
            )
        )
