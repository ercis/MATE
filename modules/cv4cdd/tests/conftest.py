"""Put the repo root on ``sys.path`` so ``modules.cv4cdd`` resolves.

Only ``cv4cdd_core`` is imported here - it needs numpy / pandas / pm4py / PIL,
all of which the platform venv already has. ``module.py`` is deliberately not
imported: it pulls in ``mate.sdk`` and (via a run) TensorFlow, which lives in
the module's own venv.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
