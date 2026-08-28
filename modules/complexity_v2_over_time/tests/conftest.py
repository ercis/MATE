"""Ensure the repo root is importable so ``modules.complexity_v2_over_time`` resolves.

The module's own code uses package-relative imports (``from .metrics_core
import ...``); importing it under the ``modules.complexity_v2_over_time``
package name (mirroring the other modules' tests) requires the repo root on
``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
