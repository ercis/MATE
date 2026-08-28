"""Manifest `runtime:` block - schema, validation, hash stability.

The invariants that matter for production:
  * absent `runtime` == python (every pre-existing manifest parses identically),
  * `dependencies_hash()` stays byte-identical for python manifests (a changed
    hash would rebuild every deployed module venv on upgrade),
  * foreign runtimes are normalised onto `isolation: subprocess` so every
    existing loader/metrics check keeps working,
  * misdeclared manifests fail loud at validation, not at mount time.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest, RuntimeJvm, RuntimePython


def _manifest(**overrides: Any) -> Manifest:
    data: dict[str, Any] = {
        "id": "rt_test",
        "name": "RT",
        "version": "1.0.0",
        "category": "other",
    }
    data.update(overrides)
    return Manifest.model_validate(data)


def test_runtime_defaults_to_python() -> None:
    m = _manifest()
    assert isinstance(m.runtime, RuntimePython)
    assert m.runtime.kind == "python"
    assert m.dependencies.python.isolation == "in_process"


def test_jvm_runtime_parses_and_normalises_isolation() -> None:
    m = _manifest(
        runtime={"kind": "jvm", "jar": "dist/a.jar", "requires-java": 21, "jvm-args": ["-Xmx1g"]}
    )
    assert isinstance(m.runtime, RuntimeJvm)
    assert m.runtime.requires_java == 21
    assert m.runtime.jvm_args == ["-Xmx1g"]
    # The validator forces subprocess so all `isolation == "subprocess"` checks apply.
    assert m.dependencies.python.isolation == "subprocess"


def test_jvm_rejects_python_dependency_block() -> None:
    for py_block in (
        {"packages": ["numpy"]},
        {"inherit": ["pandas"]},
        {"requires-python": ">=3.12"},
    ):
        with pytest.raises((ModuleManifestError, ValueError)):
            _manifest(runtime={"kind": "jvm", "jar": "a.jar"}, dependencies={"python": py_block})


def test_jvm_rejects_explicit_in_process() -> None:
    with pytest.raises((ModuleManifestError, ValueError)):
        _manifest(
            runtime={"kind": "jvm", "jar": "a.jar"},
            dependencies={"python": {"isolation": "in_process"}},
        )


def test_jvm_rejects_escaping_or_absolute_jar() -> None:
    for jar in ("../evil.jar", "/abs/evil.jar", "a/../../evil.jar", "  "):
        with pytest.raises((ModuleManifestError, ValueError)):
            _manifest(runtime={"kind": "jvm", "jar": jar})


def test_unknown_runtime_kind_rejected() -> None:
    # Node/R are design-reserved, not accepted - the tagged union is the gate.
    with pytest.raises((ModuleManifestError, ValueError)):
        _manifest(runtime={"kind": "node", "entry": "dist/worker.mjs"})


def test_requires_java_floor_is_17() -> None:
    with pytest.raises((ModuleManifestError, ValueError)):
        _manifest(runtime={"kind": "jvm", "jar": "a.jar", "requires-java": 11})


def test_python_dependencies_hash_unchanged_by_runtime_field() -> None:
    """For python manifests the hash payload must be exactly the pre-`runtime:`
    formula - byte-identical - or every deployed venv rebuilds on upgrade."""
    m = _manifest(dependencies={"python": {"packages": ["numpy>=2"]}})
    legacy_payload = json.dumps(m.dependencies.model_dump(by_alias=True), sort_keys=True)
    legacy_hash = hashlib.blake2b(legacy_payload.encode("utf-8"), digest_size=16).hexdigest()
    assert m.dependencies_hash() == legacy_hash


def test_jvm_hash_folds_runtime_block() -> None:
    base = _manifest(runtime={"kind": "jvm", "jar": "dist/a.jar"})
    changed_args = _manifest(runtime={"kind": "jvm", "jar": "dist/a.jar", "jvm-args": ["-Xmx2g"]})
    changed_jar = _manifest(runtime={"kind": "jvm", "jar": "dist/b.jar"})
    assert base.dependencies_hash() != changed_args.dependencies_hash()
    assert base.dependencies_hash() != changed_jar.dependencies_hash()
    # And it must differ from a plain-python manifest's hash (same deps block).
    assert base.dependencies_hash() != _manifest().dependencies_hash()
