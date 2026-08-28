"""Ensure the repo root is importable so ``modules.concept_drift_explainer`` resolves.

This module ships its own ``pyproject.toml``, which makes pytest anchor its
rootdir on the module folder rather than the repo - so without this shim the
repo root never lands on ``sys.path`` and every test here fails collection with
``ModuleNotFoundError: No module named 'modules'``. Mirrors the conftest every
other module's test suite already carries.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
