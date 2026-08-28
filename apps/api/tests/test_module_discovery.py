"""Discovery is fail-soft: one broken module must never brick the whole boot.

A folder whose `manifest.yaml` stopped validating (an upload predating a schema
change, a hand-edited folder) used to re-raise out of `discover()`, abort
`load_all()`, and leave the platform with *zero* modules - the UI then showed
"No modules installed" for every user. Same class of failure for a module whose
hard `requirements.modules` names an id that isn't present, or a dependency
cycle. All of them now log loudly and drop just the offending module(s).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from mate.api.modules.discovery import DiscoveredModule, discover, topo_sort
from mate.sdk.manifest import Manifest


def _write_module(root: Path, folder: str, **manifest: object) -> Path:
    path = root / folder
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path


def _base(module_id: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": module_id,
        "name": module_id.replace("_", " ").title(),
        "version": "1.0.0",
        "category": "foundation",
    }
    data.update(overrides)
    return data


def _discovered(module_id: str, requires: list[str] | None = None) -> DiscoveredModule:
    manifest = Manifest.model_validate(_base(module_id, requirements={"modules": requires or []}))
    return DiscoveredModule(folder=Path("/nonexistent") / module_id, manifest=manifest)


# ── discover() ──────────────────────────────────────────────────────────────


def test_invalid_manifest_skips_only_that_folder(tmp_path: Path) -> None:
    # `author` was removed in favour of `source`; an upload written before that
    # change no longer validates. It must not take the valid modules with it.
    _write_module(tmp_path, "good", **_base("good"))
    _write_module(tmp_path, "legacy", **_base("legacy", author="Mate"))

    assert [d.id for d in discover(tmp_path)] == ["good"]


def test_invalid_manifest_in_uploads_root_keeps_the_defaults_root(tmp_path: Path) -> None:
    # The real VM shape: bundled defaults in one root, a stale upload in another.
    defaults = tmp_path / "modules"
    uploads = tmp_path / "uploaded_modules"
    _write_module(defaults, "discovery", **_base("discovery"))
    _write_module(uploads, "case_count", **_base("case_count", author="Mate"))

    assert [d.id for d in discover(defaults, uploads)] == ["discovery"]


# ── topo_sort() ─────────────────────────────────────────────────────────────


def test_dependency_first_ordering_alphabetical_within_a_layer() -> None:
    out = topo_sort([_discovered("c", ["a", "b"]), _discovered("b"), _discovered("a")])
    assert [d.id for d in out] == ["a", "b", "c"]


def test_missing_requirement_drops_the_dependent_chain_only() -> None:
    # `needs_ghost` requires a module that was skipped upstream; `also_needs`
    # requires `needs_ghost`, so it goes too. `standalone` is unaffected.
    out = topo_sort(
        [
            _discovered("standalone"),
            _discovered("needs_ghost", ["ghost"]),
            _discovered("also_needs", ["needs_ghost"]),
        ]
    )
    assert [d.id for d in out] == ["standalone"]


def test_cycle_members_are_dropped_not_raised() -> None:
    out = topo_sort([_discovered("a", ["b"]), _discovered("b", ["a"]), _discovered("root")])
    assert [d.id for d in out] == ["root"]
