"""Put the repo root on sys.path so ``modules.conformance`` resolves.

The module's own code uses package-relative imports (``from .serializers
import ...``); importing it under the ``modules.conformance`` package name
(mirroring the other modules' tests) requires the repo root on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
