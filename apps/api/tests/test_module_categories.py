"""The 'comparison' module category (Goal 4).

`ModuleCategory` must accept "comparison", and the two bundled comparison
modules (Process Comparison + Pcomp) must declare it so the web app groups them
under their own "Comparison" section instead of "Advanced". The default test
suite loads modules from an empty dir, so we validate the bundled manifests
directly off disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
import yaml

from mate.sdk.manifest import Manifest, ModuleCategory

_MODULES_DIR = Path(__file__).resolve().parents[3] / "modules"


def test_comparison_is_a_valid_module_category() -> None:
    assert "comparison" in get_args(ModuleCategory)


@pytest.mark.parametrize("module_id", ["process_comparison", "pcomp"])
def test_bundled_comparison_modules_declare_the_category(module_id: str) -> None:
    data = yaml.safe_load((_MODULES_DIR / module_id / "manifest.yaml").read_text())
    assert data["category"] == "comparison"
    # The full manifest must still validate (would raise if "comparison" were not
    # an allowed category, or if the category change broke the manifest).
    manifest = Manifest.model_validate(data)
    assert manifest.category == "comparison"
