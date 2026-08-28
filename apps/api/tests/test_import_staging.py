"""Staged uploads: `POST /event-logs/stage` + confirming with a staging_token.

The import wizard uploads once, probes the staged bytes for their columns, and
only creates the log when the user confirms the mapping. These cover the probe
output, the hand-off, and the failure modes that would otherwise leak bytes.
"""

from __future__ import annotations

import asyncio
import gzip
from pathlib import Path

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


async def _stage(client: AsyncClient, path: Path, filename: str | None = None) -> dict:
    with path.open("rb") as fh:
        resp = await client.post(
            "/api/v1/event-logs/stage",
            files={"file": (filename or path.name, fh, "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _staging_dir(token: str) -> Path:
    from mate.api.ingest.staging import staging_root

    return staging_root(TEST_USER_ID, token)


@pytest.mark.asyncio
async def test_stage_csv_reports_columns_roles_and_confidence(client: AsyncClient) -> None:
    body = await _stage(client, FIXTURES / "sample.csv")

    assert body["staging_token"]
    assert body["source_format"] == "csv"
    assert body["log_model"] == "case_centric"
    assert body["needs_mapping"] is True
    assert body["delimiter"] == ","
    assert body["events_sampled"] > 0
    assert body["size_bytes"] > 0
    assert body["filename"] == "sample.csv"

    columns = {c["name"]: c for c in body["columns"]}
    assert set(columns) == {"case_id", "activity", "timestamp", "resource"}
    # Every fixture row is populated, and the wizard gets real example values.
    assert columns["activity"]["coverage"] == 1.0
    assert "register order" in columns["activity"]["samples"]

    # The headers match the canonical role names exactly, so nothing is guessed.
    assert body["roles"]["case_id"] == "case_id"
    assert body["roles"]["timestamp"] == "timestamp"
    assert body["quality"]["case_id"] == "exact"
    assert body["quality"]["activity"] == "exact"

    # Staged bytes wait on disk until the import is confirmed.
    assert (_staging_dir(body["staging_token"]) / "original.csv").exists()


@pytest.mark.asyncio
async def test_stage_then_import_moves_the_staged_file(client: AsyncClient) -> None:
    staged = await _stage(client, FIXTURES / "sample.csv")
    token = staged["staging_token"]

    resp = await client.post(
        "/api/v1/event-logs",
        data={"staging_token": token, "name": "Staged CSV"},
    )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]

    detail = await _wait_until_ready(client, log_id)
    assert detail["name"] == "Staged CSV"
    assert detail["source_filename"] == "sample.csv"
    assert detail["events_count"] > 0
    # The confidence of each role survives into the log's schema.
    assert detail["detected_schema"]["column_role_quality"]["case_id"] == "exact"

    from mate.api.ingest.storage import log_paths

    paths = log_paths(log_id, TEST_USER_ID)
    assert paths.find_original() is not None
    # Nothing left behind: the staged copy was moved, not duplicated.
    assert not _staging_dir(token).exists()


@pytest.mark.asyncio
async def test_stage_gzipped_xes_samples_traces(client: AsyncClient, tmp_path: Path) -> None:
    """A compressed XES can't be read client-side - this is why the probe exists."""
    gz = tmp_path / "sample.xes.gz"
    gz.write_bytes(gzip.compress((FIXTURES / "sample.xes").read_bytes()))

    body = await _stage(client, gz)

    assert body["source_format"] == "xes.gz"
    assert body["needs_mapping"] is True
    assert body["events_sampled"] > 0
    names = {c["name"] for c in body["columns"]}
    assert {"case_id", "activity", "timestamp"} <= names
    # The XES parser already emits canonical names, so all three map exactly.
    assert body["quality"]["case_id"] == "exact"
    assert body["quality"]["activity"] == "exact"
    assert body["quality"]["timestamp"] == "exact"


@pytest.mark.asyncio
async def test_stage_ocel_skips_mapping(client: AsyncClient) -> None:
    body = await _stage(client, FIXTURES / "sample.jsonocel")

    assert body["source_format"] == "ocel"
    assert body["log_model"] == "object_centric"
    assert body["needs_mapping"] is False
    assert body["columns"] == []


@pytest.mark.asyncio
async def test_import_with_confirmed_roles_overrides_the_guess(client: AsyncClient) -> None:
    """The wizard's confirmed mapping wins over whatever the resolver guessed."""
    staged = await _stage(client, FIXTURES / "sample.csv")

    resp = await client.post(
        "/api/v1/event-logs",
        data={
            "staging_token": staged["staging_token"],
            "name": "Roles forced",
            "column_roles": '{"case_id": "case_id", "activity": "resource", '
            '"timestamp": "timestamp"}',
        },
    )
    assert resp.status_code == 202, resp.text

    detail = await _wait_until_ready(client, resp.json()["log_id"])
    assert detail["column_roles"]["activity"] == "resource"
    assert detail["detected_schema"]["column_role_quality"]["activity"] == "user"
    assert detail["mapping_needs_review"] is False


@pytest.mark.asyncio
async def test_unknown_staging_token_is_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/event-logs",
        data={"staging_token": "00000000-0000-7000-8000-00000000dead"},
    )
    assert resp.status_code == 404
    assert "no longer staged" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_traversal_token_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/event-logs",
        data={"staging_token": "../../users"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_file_and_token_together_is_422(client: AsyncClient) -> None:
    with (FIXTURES / "sample.csv").open("rb") as fh:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", fh, "text/csv")},
            data={"staging_token": "00000000-0000-7000-8000-00000000beef"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_neither_file_nor_token_is_422(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/event-logs", data={"name": "nothing"})
    assert resp.status_code == 422


def _staged_dirs() -> set[Path]:
    from mate.api.config import get_settings

    root = get_settings().staging_dir_for(TEST_USER_ID)
    return set(root.iterdir()) if root.is_dir() else set()


@pytest.mark.asyncio
async def test_unsupported_extension_never_stages(client: AsyncClient, tmp_path: Path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("not an event log")
    before = _staged_dirs()

    with junk.open("rb") as fh:
        resp = await client.post(
            "/api/v1/event-logs/stage",
            files={"file": ("notes.txt", fh, "text/plain")},
        )
    assert resp.status_code == 415
    assert _staged_dirs() == before


@pytest.mark.asyncio
async def test_unreadable_upload_cleans_up_its_staging_dir(
    client: AsyncClient, tmp_path: Path
) -> None:
    """A zip with no importable member passes `detect_format` but fails the
    content sniff - i.e. it fails *after* the bytes were written."""
    import zipfile

    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.md", "nothing to import here")
    before = _staged_dirs()

    with archive.open("rb") as fh:
        resp = await client.post(
            "/api/v1/event-logs/stage",
            files={"file": ("empty.zip", fh, "application/zip")},
        )
    assert resp.status_code == 415
    assert _staged_dirs() == before, "a failed stage must not leak bytes"


@pytest.mark.asyncio
async def test_sweep_reclaims_expired_staging(client: AsyncClient) -> None:
    from mate.api.ingest.staging import sweep_staging

    staged = await _stage(client, FIXTURES / "sample.csv")
    root = _staging_dir(staged["staging_token"])
    assert root.exists()

    # Nothing is due yet…
    assert sweep_staging() == 0
    assert root.exists()

    # …and everything is once the TTL is zero.
    assert sweep_staging(ttl_seconds=0) >= 1
    assert not root.exists()

    resp = await client.post("/api/v1/event-logs", data={"staging_token": staged["staging_token"]})
    assert resp.status_code == 404
