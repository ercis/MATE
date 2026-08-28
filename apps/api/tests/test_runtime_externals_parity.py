"""`runtime-externals.json` and the `LOADERS` table must stay in lockstep.

A module bundle marks these specifiers external, then resolves them at runtime
from `window.__FF_RUNTIME__`. So the two files are one contract in two places:

* in the JSON but not in `LOADERS`  -> the bundle drops the import and the
  widget throws `required "X" which is not in the runtime` when it mounts;
* in `LOADERS` but not the JSON     -> esbuild inlines the module's own copy,
  silently duplicating React context and breaking hooks across the boundary.

Both only surface at runtime today (a `console.error` in `checkDrift`), which
is far too late — the edits are in different files and easy to half-do. This is
a cheap static guard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[3] / "apps" / "web"
EXTERNALS_JSON = WEB / "lib" / "runtime-externals.json"
RUNTIME_TS = WEB / "lib" / "module-runtime.ts"


def _declared_externals() -> list[str]:
    return json.loads(EXTERNALS_JSON.read_text())


def _loader_keys() -> set[str]:
    src = RUNTIME_TS.read_text()
    _, _, after = src.partition("const LOADERS")
    block, _, _ = after.partition("\n};")
    assert block, "could not locate the LOADERS table in module-runtime.ts"
    return set(re.findall(r'"([^"]+)":\s*\(\)', block))


def test_every_external_has_a_loader() -> None:
    missing = [e for e in _declared_externals() if e not in _loader_keys()]
    assert not missing, (
        f"externals with no LOADERS entry: {missing}. "
        "A module importing one of these throws at mount."
    )


def test_every_loader_is_declared_external() -> None:
    declared = set(_declared_externals())
    extra = sorted(k for k in _loader_keys() if k not in declared)
    assert not extra, (
        f"LOADERS entries missing from runtime-externals.json: {extra}. "
        "These get inlined into each bundle instead of shared."
    )


def test_externals_list_has_no_duplicates() -> None:
    declared = _declared_externals()
    assert len(declared) == len(set(declared)), "duplicate entries in runtime-externals.json"
