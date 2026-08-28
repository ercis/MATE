"""Content-addressed hashing of a *pristine* module source tree.

The install pipeline uses this to decide whether two uploads of the same module
id are the *same* module - in which case the second uploader shares the already
loaded code and just gains an ownership row (`module_installs`) - or a genuine
conflict, which is rejected. See `install_jobs._stage_validated_upload`.

The hash MUST be taken on the pristine staged tree, *before* `install_module`
synthesises a `pyproject.toml` into the folder (`installer._synthesise_pyproject`)
for deps-bearing / subprocess modules. Hashing a post-install folder would make a
deps-bearing module hash differently pre- vs post-install, so two byte-identical
uploads would wrongly diverge and re-manifest the very bug this fixes. We persist
the pristine hash in a sidecar (`.content-hash`) and always compare pristine vs
pristine.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mate.api.storage.module_archive import BUILD_ARTIFACT_NAMES

# Sidecar holding a module's pristine content hash, written next to its source.
# Excluded from the hash (it must not hash itself) but deliberately NOT part of
# `BUILD_ARTIFACT_NAMES`, so it rides along in the S3 source archive and survives
# a fresh-VM re-materialise.
CONTENT_HASH_FILE = ".content-hash"

# Derived build artifacts + the sidecar are never part of a module's identity.
# Reuse the archive's exclusion set as the single source of truth.
_HASH_EXCLUDE = BUILD_ARTIFACT_NAMES | {CONTENT_HASH_FILE}


def module_content_hash(folder: Path) -> str:
    """Deterministic hash of the regular files under *folder*.

    Files are hashed in sorted POSIX-relpath order, each entry length-framed
    (`len(relpath) | relpath | kind | payload`) so distinct trees can't collide
    (e.g. `("ab", "c")` vs `("a", "bc")`). Symlinks hash their target and are not
    followed - this sidesteps the zip-vs-tar asymmetry (the tar `data` filter
    strips escaping links) and avoids reading through a broken link. File mode /
    exec bit is intentionally ignored so the same content packed as `.zip` and
    `.tar.gz` matches; empty directories are ignored. Build artifacts (`.venv`,
    `.dist`, ...) and the `.content-hash` sidecar are skipped.
    """
    folder = Path(folder)
    digest = hashlib.blake2b(digest_size=32)
    for path in _iter_hashable_files(folder):
        rel = path.relative_to(folder).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        else:
            try:
                payload = hashlib.blake2b(path.read_bytes(), digest_size=32).digest()
            except OSError:
                # Unreadable regular file - contribute a stable sentinel rather
                # than raise, keeping the hash deterministic.
                payload = b"\x00" * 32
            digest.update(b"F")
            digest.update(payload)
    return digest.hexdigest()


def _iter_hashable_files(folder: Path) -> list[Path]:
    """All non-excluded regular files/symlinks under *folder*, sorted by relpath."""
    results: list[Path] = []
    for root, dirnames, filenames in os.walk(folder):
        # Prune excluded directories in place so os.walk never descends them.
        dirnames[:] = [d for d in dirnames if d not in _HASH_EXCLUDE]
        root_path = Path(root)
        for name in filenames:
            if name in _HASH_EXCLUDE:
                continue
            results.append(root_path / name)
    results.sort(key=lambda p: p.relative_to(folder).as_posix())
    return results


def read_content_hash(folder: Path) -> str | None:
    """Return the persisted pristine content hash for a module, or None if the
    sidecar is missing/empty (an older install, or an unverifiable state)."""
    try:
        text = (Path(folder) / CONTENT_HASH_FILE).read_text().strip()
    except OSError:
        return None
    return text or None


def write_content_hash(folder: Path, digest: str) -> None:
    """Persist a module's pristine content hash next to its source."""
    (Path(folder) / CONTENT_HASH_FILE).write_text(digest)
