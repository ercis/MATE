"""The JVM runtime - modules shipped as self-contained fat jars.

There is no server-side dependency resolution (no Maven/Gradle): the jar must
bundle everything but the JRE. ``materialize`` therefore only *validates* -
JRE present and new enough, jar present inside the folder, jar runnable
(``Main-Class`` in its manifest) - and stamps ``.installed-hash`` for uniform
skip semantics. The worker speaks the wire protocol in ``modules/PROTOCOL.md``
(implemented by the ``mate-sdk-jvm`` library under ``packages/module-sdk-jvm``).
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path

import structlog

from mate.api.config import get_settings
from mate.api.modules.installer import ModuleInstallError
from mate.api.modules.runtimes.base import ModuleRuntime, WorkerLaunchSpec
from mate.sdk.manifest import Manifest, RuntimeJvm

log = structlog.get_logger(__name__)

_VERSION_RE = re.compile(r'version "([^"]+)"')


class JvmRuntime(ModuleRuntime):
    key = "jvm"

    def __init__(self) -> None:
        # Successful probes are cached for the process lifetime (the JRE won't
        # vanish mid-run); failures are NOT cached, so installing Java and
        # retrying an upload works without an API restart.
        self._probe_ok: tuple[str, int] | None = None

    def available(self) -> tuple[bool, str]:
        ok, detail, _feature = self._probe()
        return ok, detail

    async def materialize(
        self, folder: Path, manifest: Manifest, *, force: bool = False
    ) -> Path | None:
        rt = manifest.runtime
        if not isinstance(rt, RuntimeJvm):  # pragma: no cover - registry keys by kind
            raise ModuleInstallError(f"Module {manifest.id!r} is not a JVM module.")

        jar_path = (folder / rt.jar).resolve()
        expected = manifest.dependencies_hash()
        hash_file = folder / ".installed-hash"
        if (
            not force
            and hash_file.exists()
            and hash_file.read_text().strip() == expected
            and jar_path.is_file()
        ):
            log.debug("modules.jvm.skip_unchanged", module_id=manifest.id)
            return None

        ok, detail, feature = self._probe()
        if not ok:
            raise ModuleInstallError(
                f"Module {manifest.id!r} is a Java module but this server has no usable Java "
                f"runtime ({detail}). Install a JRE >= {rt.requires_java} (e.g. Temurin 21) or "
                "run the platform's Docker image, then install the module again."
            )
        if feature < rt.requires_java:
            raise ModuleInstallError(
                f"Module {manifest.id!r} needs Java >= {rt.requires_java} but this server runs "
                f"Java {feature} ({detail}). Upgrade the JRE or lower the module's "
                "runtime.requires-java."
            )
        # Belt-and-braces on top of the manifest validator: the resolved jar
        # must stay inside the module folder (symlinks resolved).
        if not jar_path.is_relative_to(folder.resolve()):
            raise ModuleInstallError(
                f"Module {manifest.id!r} declares runtime.jar {rt.jar!r}, which escapes the "
                "module folder."
            )
        if not jar_path.is_file():
            raise ModuleInstallError(
                f"Module {manifest.id!r} declares runtime.jar {rt.jar!r} but the file does not "
                "exist. Build it (for the bundled example: `make sdk-jvm`) or include it in the "
                "uploaded archive."
            )
        if _jar_main_class(jar_path) is None:
            raise ModuleInstallError(
                f"Module {manifest.id!r}'s jar {rt.jar!r} has no Main-Class in its "
                "META-INF/MANIFEST.MF - it is not runnable. Ship a fat jar built with a "
                "mainClass (e.g. Gradle shadowJar with `manifest.attributes['Main-Class']`)."
            )

        hash_file.write_text(expected)
        log.info(
            "modules.jvm.materialized",
            module_id=manifest.id,
            jar=rt.jar,
            java=detail,
        )
        return None

    def launch_spec(self, folder: Path, manifest: Manifest) -> WorkerLaunchSpec:
        rt = manifest.runtime
        if not isinstance(rt, RuntimeJvm):  # pragma: no cover - registry keys by kind
            raise RuntimeError(f"Module {manifest.id!r} is not a JVM module.")
        return WorkerLaunchSpec(
            argv=(get_settings().java_bin, *rt.jvm_args, "-jar", str((folder / rt.jar).resolve())),
            cwd=folder,
        )

    def _probe(self) -> tuple[bool, str, int]:
        """(ok, human detail, feature version). `java -version` prints to
        stderr; feature version is `21` for "21.0.11", `8` for "1.8.0_471"."""
        if self._probe_ok is not None:
            detail, feature = self._probe_ok
            return True, detail, feature
        java_bin = get_settings().java_bin
        try:
            proc = subprocess.run(
                [java_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            return False, f"`{java_bin}` not found on PATH", 0
        except OSError as exc:
            return False, f"`{java_bin} -version` failed to execute: {exc}", 0
        except subprocess.TimeoutExpired:
            return False, f"`{java_bin} -version` timed out", 0
        output = (proc.stderr or "") + (proc.stdout or "")
        match = _VERSION_RE.search(output)
        if proc.returncode != 0 or match is None:
            return False, f"`{java_bin} -version` gave no parseable version", 0
        raw = match.group(1)
        parts = raw.split(".")
        try:
            feature = int(parts[1]) if parts[0] == "1" and len(parts) > 1 else int(parts[0])
        except ValueError:
            return False, f"unparseable Java version {raw!r}", 0
        first_line = output.strip().splitlines()[0] if output.strip() else f"java {raw}"
        self._probe_ok = (first_line, feature)
        return True, first_line, feature


def _jar_main_class(jar_path: Path) -> str | None:
    """Read `Main-Class` from the jar's MANIFEST.MF, honouring the jar-manifest
    72-byte line-wrap rule (a continuation line starts with a single space)."""
    try:
        with zipfile.ZipFile(jar_path) as zf:
            raw = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    unfolded = raw.replace("\r\n", "\n").replace("\n ", "")
    for line in unfolded.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "Main-Class":
            main = value.strip()
            return main or None
    return None
