"""Put the repo root on sys.path so ``modules.process_comparison`` resolves.

The module's own code uses package-relative imports; importing its pure helpers
under the ``modules.process_comparison`` package name (as the other modules'
tests do) needs the repo root importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
