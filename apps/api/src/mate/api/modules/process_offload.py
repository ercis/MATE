"""Cross-process module CPU offload bridge (§8.3).

``ctx.run_in_process(fn, *args)`` ships the heaviest module compute (pm4py /
networkx / pandas mining) to the platform's ``ProcessPoolExecutor`` so it runs
on its own core instead of contending on the GIL.

The naive approach - pickling the callable by qualified name - does **not** work
for bundled modules: the loader imports each ``module.py`` under a *synthetic*
``sys.modules`` name from a file path (``loader._import_module_class``), so a
spawned/forkserver worker (the only fork-safe start methods once the asyncio
loop + DuckDB threads are running) cannot re-import it, and the module's own
``.venv`` deps aren't on the worker's path either.

This module is the importable bridge. ``mate.api`` *is* importable in any worker
(it's on the propagated ``sys.path``), so the worker imports :func:`invoke`,
which is handed the module's folder, its ``.venv`` site-packages, the
``module.py`` path and the target function name; it rebuilds the import
environment, imports the module by path (cached per worker so the import + heavy
deps load once), and calls the function. Authors keep the unchanged
``await ctx.run_in_process(top_level_fn, *args)`` API - the platform fills in the
module metadata from the loader registry.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

# Per-worker cache: module_file -> imported module. Keeps the (possibly heavy)
# module import and its transitive deps resident for the life of the worker.
_loaded: dict[str, ModuleType] = {}


def _load_module(folder: str, site_packages: str, module_file: str) -> ModuleType:
    cached = _loaded.get(module_file)
    if cached is not None:
        return cached
    # Make the module's own dependencies (its .venv) and sibling files importable.
    for entry in (site_packages, folder):
        if entry and os.path.isdir(entry) and entry not in sys.path:
            sys.path.insert(0, entry)
    name = f"_ff_offload_{Path(folder).name}"
    spec = importlib.util.spec_from_file_location(
        name, module_file, submodule_search_locations=[folder]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {module_file!r}")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the module's relative imports (`from .x import ...`)
    # resolve against this package name.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _loaded[module_file] = mod
    return mod


def invoke(
    folder: str,
    site_packages: str,
    module_file: str,
    qualname: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any] | None,
) -> Any:
    """Worker entrypoint: import the module by path and call ``qualname(*args)``."""
    mod = _load_module(folder, site_packages, module_file)
    obj: Any = mod
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj(*args, **(kwargs or {}))


def offload_child_main(conn: Any, spec: dict[str, Any]) -> None:
    """Entry point for one killable per-call offload process (§8.3).

    Runs in a *fresh* spawned/forkserver child - one process per
    ``ctx.run_in_process`` call, not a shared pool worker. The isolation is
    deliberate: the job runtime must be able to ``SIGKILL`` a runaway offload
    when the wall-clock reaper fires (a ``ProcessPoolExecutor`` future can't be
    cancelled once running, and a shared pool would make killing one tenant's
    job collateral-kill others'). The price is a cold module import per call -
    fine for the once-per-job mining offloads are built for.

    First call ``setsid()`` so the child leads its own process group: the host
    kills via ``killpg(pid)`` (group id == pid), reaping any grandchildren the
    payload spawned. The ``(ok, value)`` result - or ``(False, exception)`` -
    is shipped back over ``conn``; if the host kills us we simply never send and
    the host detects the dead process.
    """
    with contextlib.suppress(OSError):
        os.setsid()  # already a group leader (or unsupported) → host falls back to pid kill
    try:
        if spec["kind"] == "module":
            result = invoke(
                spec["folder"],
                spec["site_packages"],
                spec["module_file"],
                spec["qualname"],
                spec["args"],
                spec["kwargs"],
            )
        else:
            fn = spec["fn"]
            result = fn(*spec["args"], **(spec["kwargs"] or {}))
        payload: tuple[bool, Any] = (True, result)
    except BaseException as exc:
        payload = (False, exc)
    try:
        conn.send(payload)
    except Exception:
        # Result (or exception) wasn't picklable - send a degraded error so the host
        # raises something meaningful instead of hanging on a silent child.
        _ok, value = payload
        with contextlib.suppress(Exception):
            conn.send((False, RuntimeError(f"offload result was not picklable: {value!r}")))
    finally:
        conn.close()
