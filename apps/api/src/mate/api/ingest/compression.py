"""Transparent decompression for uploaded event logs.

Any supported source format may arrive compressed (``log.csv.gz``,
``events.json.xz``, ``export.xes.bz2``, ``data.zip``, …). Compression is
handled at two choke points:

- filename level - :func:`split_compression` strips the compression suffix so
  ``detect_format`` can classify the *inner* extension;
- content level - :func:`decompressed` magic-sniffs the staged bytes and
  yields a plain-file path (a streamed temp copy for compressed inputs), so
  ``sniff_format``, the probe routes, and the import parsers never see
  compressed bytes.

Magic bytes (not the filename) drive the actual decompression, so legacy
``original.xes.gz`` re-imports and misnamed uploads both work. Zip archives
must contain exactly one importable member (macOS junk like ``__MACOSX/`` is
ignored); a zip's inner format is resolved after staging via
:func:`resolve_zip_member`.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import shutil
import tempfile
import zipfile
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, cast

# Filename suffix → algorithm. Values are informational; decompression itself
# is magic-byte-driven.
COMPRESSION_SUFFIXES: dict[str, str] = {
    "gz": "gzip",
    "gzip": "gzip",
    "bz2": "bzip2",
    "xz": "xz",
    "lzma": "xz",
    "zip": "zip",
}

_MAGIC: list[tuple[bytes, str]] = [
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"PK\x03\x04", "zip"),
]

# Safety cap for nested wrapping (e.g. `log.csv.gz` inside a zip).
_MAX_NESTING = 3

_CHUNK = 1024 * 1024


def split_compression(filename: str) -> tuple[str, str | None]:
    """``log.csv.gz`` → (``log.csv``, "gzip"); ``log.csv`` → (``log.csv``, None)."""
    lower = filename.lower()
    for sfx, algo in COMPRESSION_SUFFIXES.items():
        if lower.endswith(f".{sfx}"):
            return filename[: -(len(sfx) + 1)], algo
    return filename, None


def detect_compression(path: Path) -> str | None:
    """Magic-byte sniff of ``path``. ``None`` for plain (or unreadable) files."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    for magic, algo in _MAGIC:
        if head.startswith(magic):
            return algo
    return None


def _is_zip_junk(name: str) -> bool:
    """Archive noise that must never count as a candidate member."""
    if name.endswith("/"):
        return True
    parts = name.split("/")
    if any(p == "__MACOSX" for p in parts):
        return True
    base = parts[-1]
    return base.startswith(".") or base.startswith("~$")


def zip_members(path: Path) -> list[str]:
    """Real-file members of a zip, junk filtered, order preserved."""
    with zipfile.ZipFile(path) as zf:
        return [n for n in zf.namelist() if not _is_zip_junk(n)]


def resolve_zip_member(path: Path) -> str:
    """The single importable member of a zip upload.

    Raises ``ValueError`` when the archive holds zero or more than one
    importable file - the archive-per-log contract keeps ``source_format``
    and the retained original unambiguous.
    """
    # Runtime import: detect.py imports this module for split_compression.
    from mate.api.ingest.detect import detect_format

    members = zip_members(path)
    importable: list[str] = []
    for name in members:
        try:
            detect_format(name.rsplit("/", 1)[-1])
        except ValueError:
            continue
        importable.append(name)
    if not importable:
        raise ValueError(
            "The zip archive contains no supported event-log file "
            "(.xes, .xes.gz, .csv, .xml, .json, or an OCEL extension)."
        )
    if len(importable) > 1:
        listing = ", ".join(importable[:5]) + ("…" if len(importable) > 5 else "")
        raise ValueError(
            f"The zip archive contains multiple event-log files ({listing}). "
            "Upload one log per archive."
        )
    return importable[0]


def _open_compressed(path: Path, algo: str) -> IO[bytes]:
    if algo == "gzip":
        return cast(IO[bytes], gzip.open(path, "rb"))
    if algo == "bzip2":
        return cast(IO[bytes], bz2.open(path, "rb"))
    if algo == "xz":
        return cast(IO[bytes], lzma.open(path, "rb"))
    raise ValueError(f"Unsupported compression: {algo}")


def _inner_suffix(path: Path, member: str | None) -> str:
    """Suffix for the decompressed temp file (kept meaningful for readers that
    care about extensions, e.g. pm4py's sqlite OCEL reader)."""
    name = member.rsplit("/", 1)[-1] if member else split_compression(path.name)[0]
    suffix = Path(name).suffix
    return suffix if suffix else ".bin"


@contextmanager
def decompressed(path: Path) -> Generator[Path]:
    """Yield a plain-file path for ``path``.

    Uncompressed inputs are yielded as-is. Compressed inputs are streamed into
    a temp file that lives only within the context. Nested wrapping (a
    ``log.csv.gz`` inside a zip) is unwrapped up to ``_MAX_NESTING`` layers.
    """
    temps: list[Path] = []
    try:
        current = path
        for _ in range(_MAX_NESTING):
            algo = detect_compression(current)
            if algo is None:
                break
            member = resolve_zip_member(current) if algo == "zip" else None
            with tempfile.NamedTemporaryFile(
                prefix="mate-decompress-",
                suffix=_inner_suffix(current, member),
                delete=False,
            ) as fd:
                tmp = Path(fd.name)
                temps.append(tmp)
                if algo == "zip":
                    with zipfile.ZipFile(current) as zf, zf.open(cast(str, member)) as src:
                        shutil.copyfileobj(src, fd, _CHUNK)
                else:
                    with _open_compressed(current, algo) as src:
                        shutil.copyfileobj(src, fd, _CHUNK)
            current = tmp
        else:
            raise ValueError("Upload is nested in too many compression layers.")
        yield current
    finally:
        for tmp in temps:
            with suppress(OSError):
                tmp.unlink()


__all__ = [
    "COMPRESSION_SUFFIXES",
    "decompressed",
    "detect_compression",
    "resolve_zip_member",
    "split_compression",
    "zip_members",
]
