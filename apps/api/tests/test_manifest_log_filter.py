"""Manifest `frontend.log_filter` - the module-panel filter-bar opt-out.

The platform renders a log-scoped filter bar (column filters + time range)
above every module panel. A module opts out in its manifest; the modules route
folds that together with "has a panel at all" into the single
`supports_log_filter` flag the web app reads.

The invariants that matter:
  * absent `log_filter` == the bar shows (every pre-existing manifest keeps it),
  * the opt-out never touches `dependencies_hash()` - a changed hash would
    rebuild every deployed module venv on upgrade,
  * a module with no panel has no surface to filter, whatever it declares.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mate.sdk.manifest import Manifest

# Bundled modules whose panels deliberately show no filter bar. Pinned so a
# manifest edit that silently re-enables one is a test failure, not a surprise
# in the UI.
OPTED_OUT = {
    "agentsimulator",
    "concept_drift_explainer",
    "conformance",
    "cv4cdd",
    "pcomp",
    "performance",
    "process_comparison",
}

MODULES_DIR = Path(__file__).resolve().parents[3] / "modules"


def _manifest(**overrides: Any) -> Manifest:
    data: dict[str, Any] = {
        "id": "lf_test",
        "name": "LF",
        "version": "1.0.0",
        "category": "other",
    }
    data.update(overrides)
    return Manifest.model_validate(data)


def _shows_filter_bar(m: Manifest) -> bool:
    """Mirrors `ModuleSummary.supports_log_filter` in routes/modules.py."""
    return bool(m.frontend.panel) and m.frontend.log_filter


def test_log_filter_defaults_to_on() -> None:
    assert _manifest(frontend={"panel": "./panel/index.tsx"}).frontend.log_filter is True


def test_log_filter_opt_out_parses() -> None:
    m = _manifest(frontend={"panel": "./panel/index.tsx", "log_filter": False})
    assert m.frontend.log_filter is False
    assert _shows_filter_bar(m) is False


def test_panel_less_module_never_shows_the_bar() -> None:
    # Nothing declared: no panel means no surface to filter, so the default-on
    # `log_filter` must not leak a bar above the "no frontend" placeholder.
    assert _manifest().frontend.log_filter is True
    assert _shows_filter_bar(_manifest()) is False


def test_log_filter_does_not_affect_dependencies_hash() -> None:
    on = _manifest(frontend={"panel": "./panel/index.tsx"})
    off = _manifest(frontend={"panel": "./panel/index.tsx", "log_filter": False})
    assert on.dependencies_hash() == off.dependencies_hash()


def _bundled() -> list[tuple[str, Manifest]]:
    out = []
    for p in sorted(MODULES_DIR.glob("*/manifest.yaml")):
        m = Manifest.model_validate(yaml.safe_load(p.read_text()))
        out.append((m.id, m))
    return out


@pytest.mark.parametrize(
    "module_id,manifest", _bundled(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_bundled_modules_match_the_declared_opt_out_set(module_id: str, manifest: Manifest) -> None:
    assert _shows_filter_bar(manifest) is (module_id not in OPTED_OUT)
