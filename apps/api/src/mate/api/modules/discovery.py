"""Module discovery - filesystem + Python entry points (§5.3 step 1).

Two sources, both pointing at a folder with a `manifest.yaml`:

1. **Filesystem.** Walk one level under `modules_dir`. Folder names are
   arbitrary; only the manifest's `id` is authoritative.
2. **Python entry points.** Any installed Python package may declare an
   entry point under the `mate.modules` group whose value is the
   importable package name. The folder containing that package's
   `__init__.py` is treated as the module folder. This is how a
   `pip install`-ed package that declares the entry point gets picked up
   without copying files.

Two manifests declaring the same id is a hard error regardless of source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from importlib.util import find_spec
from pathlib import Path

import structlog

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)

ENTRY_POINT_GROUP = "mate.modules"


@dataclass(frozen=True)
class DiscoveredModule:
    folder: Path
    manifest: Manifest
    source: str = "filesystem"  # "filesystem" | "entry_point"

    @property
    def id(self) -> str:
        return self.manifest.id


def discover(*roots: Path) -> list[DiscoveredModule]:
    """Return the union of filesystem-discovered + entry-point-discovered
    modules. Filesystem entries take precedence: if both surface the same
    `id`, the entry-point copy is ignored with a warning (lets a developer
    override an installed module by dropping a folder in `modules/`).

    ``roots`` are filesystem roots scanned in order - typically the repo
    ``modules/`` defaults root first, then the persistent uploads root. The
    first copy of an id wins; a later duplicate (e.g. a leftover upload
    colliding with a bundled default) is skipped with a warning rather than
    aborting the whole load - one stray module must fail itself, not brick
    every module at boot. Rejecting an id collision is the upload path's job.

    Same policy for a folder whose ``manifest.yaml`` no longer validates: log
    and skip. Discovery never raises on one bad folder.
    """

    discovered: list[DiscoveredModule] = []
    seen_ids: dict[str, Path] = {}

    for modules_dir in roots:
        if not modules_dir.exists():
            continue
        for entry in sorted(modules_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.exists():
                log.debug("modules.discovery.no_manifest", folder=str(entry))
                continue
            try:
                manifest = Manifest.load_yaml(manifest_path)
            except ModuleManifestError as exc:
                # Skip, don't raise. A manifest that stopped validating (an
                # upload predating a schema change, a hand-edited folder) is one
                # module's problem; re-raising aborted the whole discovery pass
                # and booted the platform with *zero* modules. Loud error log,
                # everything else still loads. The upload path validates before
                # writing, so this only catches folders already on disk.
                log.error("modules.discovery.manifest_invalid", folder=str(entry), error=str(exc))
                continue
            if manifest.id in seen_ids:
                # Roots are scanned defaults-first, so the first-seen copy wins
                # and a later duplicate (almost always a leftover upload sitting
                # next to the bundled default) is skipped. Skipping with a
                # warning - instead of raising - keeps one stray module from
                # aborting the entire load and silently leaving the platform
                # with no modules at all.
                log.warning(
                    "modules.discovery.duplicate_id_skipped",
                    module_id=manifest.id,
                    kept=str(seen_ids[manifest.id]),
                    skipped=str(entry),
                )
                continue
            seen_ids[manifest.id] = entry
            discovered.append(
                DiscoveredModule(folder=entry, manifest=manifest, source="filesystem")
            )

    for ep_mod in discover_entry_points():
        if ep_mod.id in seen_ids:
            log.warning(
                "modules.discovery.entry_point_shadowed",
                module_id=ep_mod.id,
                installed_at=str(ep_mod.folder),
                shadowed_by=str(seen_ids[ep_mod.id]),
            )
            continue
        seen_ids[ep_mod.id] = ep_mod.folder
        discovered.append(ep_mod)

    return discovered


def discover_entry_points() -> list[DiscoveredModule]:
    """Scan installed Python packages for `mate.modules` entry points.

    Each entry point's value is an importable package name; we locate that
    package's directory via `find_spec()` and read `manifest.yaml` from it.
    A package that lacks a manifest is skipped with a warning rather than
    crashing - the missing-manifest case usually means the package wasn't
    built as a Mate module.
    """

    out: list[DiscoveredModule] = []
    try:
        eps = importlib_metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:
        log.warning("modules.discovery.entry_points_failed", error=str(exc))
        return out

    seen_ids: dict[str, str] = {}
    for ep in eps:
        package_name = ep.value
        try:
            spec = find_spec(package_name)
        except (ImportError, ValueError) as exc:
            log.warning(
                "modules.discovery.entry_point_unresolvable",
                name=ep.name,
                package=package_name,
                error=str(exc),
            )
            continue
        if spec is None or not spec.origin:
            log.warning(
                "modules.discovery.entry_point_no_origin",
                name=ep.name,
                package=package_name,
            )
            continue
        folder = Path(spec.origin).parent
        manifest_path = folder / "manifest.yaml"
        if not manifest_path.exists():
            log.warning(
                "modules.discovery.entry_point_no_manifest",
                name=ep.name,
                package=package_name,
                expected=str(manifest_path),
            )
            continue
        try:
            manifest = Manifest.load_yaml(manifest_path)
        except ModuleManifestError as exc:
            log.error(
                "modules.discovery.entry_point_manifest_invalid",
                name=ep.name,
                error=str(exc),
            )
            continue
        if manifest.id in seen_ids:
            log.error(
                "modules.discovery.entry_point_duplicate_id",
                module_id=manifest.id,
                package=package_name,
                prior=seen_ids[manifest.id],
            )
            continue
        seen_ids[manifest.id] = package_name
        out.append(DiscoveredModule(folder=folder, manifest=manifest, source="entry_point"))
    return out


def topo_sort(discovered: Iterable[DiscoveredModule]) -> list[DiscoveredModule]:
    """Topological sort by hard `requirements.modules`.

    Unloadable nodes are **dropped, not raised** - same policy as an invalid
    manifest in `discover()`: one broken module fails itself, it does not brick
    the boot. Dropped in two passes:

    1. A module requiring an id that isn't present (never installed, or skipped
       upstream because its manifest no longer validates) - and, at fixpoint,
       everything that in turn required *it*.
    2. Cycle members: whatever Kahn can't emit is in or behind a cycle.

    Ties inside a layer resolve alphabetically, so the order is byte-identical
    across boots.
    """

    by_id: dict[str, DiscoveredModule] = {d.id: d for d in discovered}

    # Pass 1 - fixpoint prune of unsatisfiable hard requirements.
    while True:
        unsatisfiable = {
            mid: dep
            for mid, d in by_id.items()
            for dep in d.manifest.requirements.modules
            if dep not in by_id
        }
        if not unsatisfiable:
            break
        for mid, dep in unsatisfiable.items():
            log.error("modules.discovery.requirement_missing", module_id=mid, requires=dep)
            del by_id[mid]

    # Pass 2 - Kahn over the pruned graph.
    deps: dict[str, set[str]] = {
        mid: set(d.manifest.requirements.modules) for mid, d in by_id.items()
    }
    out: list[DiscoveredModule] = []
    emitted: set[str] = set()
    while True:
        ready = sorted(mid for mid, need in deps.items() if not (need - emitted))
        if not ready:
            break
        for mid in ready:
            out.append(by_id[mid])
            emitted.add(mid)
            del deps[mid]

    for mid in sorted(deps):
        log.error("modules.discovery.dependency_cycle", module_id=mid, requires=sorted(deps[mid]))

    return out
