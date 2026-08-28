"""Staged uploads for the import wizard.

The import flow uploads a file exactly once: the bytes land here
(``data/staging/{user_id}/{token}/original.{ext}``), get probed for their
columns, and are *moved* into the log directory when the user confirms the
mapping. A wizard the user abandons therefore leaves an orphan directory
behind, which the TTL sweep below reclaims.

Staging is intentionally not modelled in the database: nothing here is a log
yet, and a crash between upload and confirm must not leave a half-created
``process_logs`` row.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from mate.api.config import get_settings

log = structlog.get_logger(__name__)

# Sidecar next to the staged bytes: the original filename plus the sniffed
# format, so confirming the import needs neither the client's word for it nor a
# second content sniff.
_MANIFEST_NAME = "staged.json"

# An abandoned wizard's bytes are worthless after this. Long enough that a slow
# user (or a long look at the mapping) never loses their upload.
STAGING_TTL_SECONDS = 2 * 60 * 60

# Tokens are uuid7 hex - anything else is rejected before it reaches the
# filesystem, so a token can never traverse out of the user's staging root.
_TOKEN_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{15,63}$")


@dataclass(frozen=True)
class StagedUpload:
    token: str
    root: Path
    file: Path
    manifest: dict[str, Any]

    @property
    def filename(self) -> str:
        return str(self.manifest.get("filename") or self.file.name)

    @property
    def source_format(self) -> str:
        return str(self.manifest.get("source_format") or "")

    @property
    def ocel_flavor(self) -> str | None:
        flavor = self.manifest.get("ocel_flavor")
        return str(flavor) if flavor else None

    @property
    def size_bytes(self) -> int:
        try:
            return self.file.stat().st_size
        except OSError:
            return 0

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def write_manifest(root: Path, data: dict[str, Any]) -> None:
    (root / _MANIFEST_NAME).write_text(json.dumps(data, default=str))


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / _MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def is_valid_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token))


def staging_root(user_id: str, token: str) -> Path:
    return get_settings().staging_dir_for(user_id) / token


def create_staging_dir(user_id: str, token: str) -> Path:
    root = staging_root(user_id, token)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_staged(user_id: str, token: str) -> StagedUpload | None:
    """Locate a staged upload, or ``None`` when it never existed / was swept.

    Scoped by ``user_id`` through the directory layout, so one user's token can
    never resolve inside another user's staging root.
    """
    if not is_valid_token(token):
        return None
    root = staging_root(user_id, token)
    if not root.is_dir():
        return None
    for candidate in sorted(root.glob("original.*")):
        return StagedUpload(token=token, root=root, file=candidate, manifest=_read_manifest(root))
    return None


def sweep_staging(ttl_seconds: int = STAGING_TTL_SECONDS) -> int:
    """Delete staging directories older than the TTL. Returns how many went."""
    root = get_settings().staging_dir
    if not root.is_dir():
        return 0
    cutoff = time.time() - ttl_seconds
    removed = 0
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for staged in user_dir.iterdir():
            if not staged.is_dir():
                continue
            try:
                if staged.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(staged, ignore_errors=True)
            removed += 1
    return removed


__all__ = [
    "STAGING_TTL_SECONDS",
    "StagedUpload",
    "create_staging_dir",
    "is_valid_token",
    "resolve_staged",
    "staging_root",
    "sweep_staging",
    "write_manifest",
]
