"""Hot-reload watcher path filter.

Regression: the watcher used to ignore only `.venv/.dist/node_modules` at depth
1, so a reload's own writes (`.installed-hash`, `__pycache__/*.pyc`) re-triggered
it → infinite reload loop on a prod box left in `env=dev`.
"""

from pathlib import Path

import pytest

from mate.api.modules.hot_reload import _is_ignored


@pytest.mark.parametrize(
    "rel",
    [
        "conformance/.installed-hash",  # installer rewrites this every rebuild
        "conformance/pyproject.toml",  # synthesised by the installer
        "conformance/__pycache__/module.cpython-312.pyc",  # re-import bytecode
        "conformance/sub/__pycache__/x.pyc",  # nested __pycache__ (deeper than parts[1])
        "conformance/module.cpython-312.pyc",  # stray bytecode by suffix
        "cv4cdd/.venv/lib/python3.12/site-packages/x.py",
        "cv4cdd/.dist/panel.js",
        "ocel/node_modules/dep/index.js",
        "conformance/.DS_Store",
    ],
)
def test_artifacts_ignored(rel: str) -> None:
    assert _is_ignored(Path(rel)) is True


@pytest.mark.parametrize(
    "rel",
    [
        "conformance/module.py",  # the source we DO want to reload on
        "conformance/manifest.yaml",
        "conformance/conformance.py",
        "cv4cdd/panel/index.tsx",
    ],
)
def test_source_not_ignored(rel: str) -> None:
    assert _is_ignored(Path(rel)) is False
