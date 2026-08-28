"""Put the repo root on sys.path so ``modules.pcomp`` resolves.

Mirrors the other modules' tests: the package is imported under its
``modules.pcomp`` name, which requires the repo root on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
