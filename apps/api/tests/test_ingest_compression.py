"""Compressed-upload support: filename detection, magic-byte decompression,
zip member resolution, and the sniff/parse integration."""

from __future__ import annotations

import bz2
import gzip
import json
import lzma
import zipfile
from pathlib import Path

import pytest

from mate.api.ingest.compression import (
    decompressed,
    detect_compression,
    resolve_zip_member,
    split_compression,
)
from mate.api.ingest.detect import detect_format, original_extension, sniff_format
from mate.api.ingest.dispatch import _parse_case_centric

CSV_BODY = b"case,activity,timestamp\nc1,A,2024-01-01 10:00:00\nc1,B,2024-01-01 11:00:00\n"

OCEL_JSON_BODY = json.dumps(
    {"objectTypes": [], "eventTypes": [], "events": [], "objects": []}
).encode()


# ── filename-level detection ──────────────────────────────────────────────────


def test_split_compression() -> None:
    assert split_compression("log.csv.gz") == ("log.csv", "gzip")
    assert split_compression("log.json.xz") == ("log.json", "xz")
    assert split_compression("log.xes.bz2") == ("log.xes", "bzip2")
    assert split_compression("data.zip") == ("data", "zip")
    assert split_compression("log.csv") == ("log.csv", None)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("log.csv.gz", "csv"),
        ("log.csv.zip", "csv"),
        ("log.json.xz", "json"),
        ("log.xml.bz2", "xml"),
        ("log.xes.bz2", "xes"),
        ("log.xes.gz", "xes.gz"),  # legacy special case survives
        ("log.jsonocel.gz", "ocel"),
        ("log.sqlite.gz", "ocel"),
        ("data.zip", "zip"),  # inner format resolved after staging
    ],
)
def test_detect_format_compressed(filename: str, expected: str) -> None:
    assert detect_format(filename) == expected


def test_detect_format_bare_stream_compression_rejected() -> None:
    with pytest.raises(ValueError, match="inner"):
        detect_format("mystery.gz")


def test_original_extension_keeps_compression_suffix() -> None:
    assert original_extension("log.csv.gz", "csv") == "csv.gz"
    assert original_extension("log.xes.gz", "xes.gz") == "xes.gz"
    assert original_extension("log.xmlocel.xz", "ocel") == "xmlocel.xz"
    assert original_extension("data.zip", "zip") == "zip"


# ── content-level decompression ───────────────────────────────────────────────


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_detect_compression_magic(tmp_path: Path) -> None:
    assert detect_compression(_write(tmp_path, "a.gz", gzip.compress(CSV_BODY))) == "gzip"
    assert detect_compression(_write(tmp_path, "b.bz2", bz2.compress(CSV_BODY))) == "bzip2"
    assert detect_compression(_write(tmp_path, "c.xz", lzma.compress(CSV_BODY))) == "xz"
    assert detect_compression(_write(tmp_path, "d.csv", CSV_BODY)) is None


@pytest.mark.parametrize(
    "compress",
    [gzip.compress, bz2.compress, lzma.compress],
    ids=["gzip", "bz2", "xz"],
)
def test_decompressed_stream_roundtrip(tmp_path: Path, compress) -> None:  # type: ignore[no-untyped-def]
    src = _write(tmp_path, "log.csv.x", compress(CSV_BODY))
    with decompressed(src) as plain:
        assert plain != src
        assert plain.read_bytes() == CSV_BODY
    assert not plain.exists()  # temp cleaned up


def test_decompressed_passthrough_for_plain_files(tmp_path: Path) -> None:
    src = _write(tmp_path, "log.csv", CSV_BODY)
    with decompressed(src) as plain:
        assert plain == src


def test_decompressed_zip_single_member(tmp_path: Path) -> None:
    src = tmp_path / "data.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("__MACOSX/log.csv", b"junk")
        zf.writestr(".DS_Store", b"junk")
        zf.writestr("log.csv", CSV_BODY)
    with decompressed(src) as plain:
        assert plain.read_bytes() == CSV_BODY


def test_decompressed_nested_zip_of_gz(tmp_path: Path) -> None:
    src = tmp_path / "data.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("log.csv.gz", gzip.compress(CSV_BODY))
    with decompressed(src) as plain:
        assert plain.read_bytes() == CSV_BODY


def test_resolve_zip_member_rejects_ambiguous_and_empty(tmp_path: Path) -> None:
    multi = tmp_path / "multi.zip"
    with zipfile.ZipFile(multi, "w") as zf:
        zf.writestr("a.csv", CSV_BODY)
        zf.writestr("b.csv", CSV_BODY)
    with pytest.raises(ValueError, match="multiple"):
        resolve_zip_member(multi)

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", b"hello")
    with pytest.raises(ValueError, match="no supported"):
        resolve_zip_member(empty)


# ── sniff integration ─────────────────────────────────────────────────────────


def test_sniff_gzipped_ocel_json(tmp_path: Path) -> None:
    src = _write(tmp_path, "original.json.gz", gzip.compress(OCEL_JSON_BODY))
    assert sniff_format(src, "json", filename="log.json.gz") == ("ocel", "json")


def test_sniff_zip_resolves_member_format(tmp_path: Path) -> None:
    src = tmp_path / "original.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("nested/log.csv", CSV_BODY)
    assert sniff_format(src, "zip", filename="data.zip") == ("csv", None)


def test_sniff_compressed_ocel_flavor_from_name(tmp_path: Path) -> None:
    src = _write(tmp_path, "original.xmlocel.gz", gzip.compress(b"<log/>"))
    assert sniff_format(src, "ocel", filename="log.xmlocel.gz") == ("ocel", "xml")


# ── parse integration (dispatch chokepoint) ───────────────────────────────────


@pytest.mark.parametrize(
    "compress",
    [gzip.compress, bz2.compress, lzma.compress],
    ids=["gzip", "bz2", "xz"],
)
def test_parse_case_centric_compressed_csv(tmp_path: Path, compress) -> None:  # type: ignore[no-untyped-def]
    src = _write(tmp_path, "original.csv.gz", compress(CSV_BODY))
    rows, detected, effective = _parse_case_centric("csv", src, None, None, None)
    assert len(rows) == 2
    assert detected["csv_columns"] == ["case", "activity", "timestamp"]
    assert effective is not None


def test_parse_case_centric_zip_csv(tmp_path: Path) -> None:
    src = tmp_path / "original.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("log.csv", CSV_BODY)
    rows, _detected, _effective = _parse_case_centric("csv", src, None, None, None)
    assert len(rows) == 2
