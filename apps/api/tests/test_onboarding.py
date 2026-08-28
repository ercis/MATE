"""Per-user onboarding state - drives whether the welcome overlay shows."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_onboarding_lifecycle(client: AsyncClient) -> None:
    # A user with no saved state is treated as not-yet-onboarded, so the
    # overlay shows on first open.
    resp = await client.get("/api/v1/onboarding")
    assert resp.status_code == 200
    assert resp.json() == {
        "completed": False,
        "experience_level": None,
        "tour_completed": False,
    }

    # Finishing persists completion (and the chosen experience level) per-user.
    put = await client.put(
        "/api/v1/onboarding",
        json={"completed": True, "experience_level": "beginner"},
    )
    assert put.status_code == 200
    assert (await client.get("/api/v1/onboarding")).json() == {
        "completed": True,
        "experience_level": "beginner",
        "tour_completed": False,
    }

    # "Restart onboarding" flips it back; the overlay returns.
    await client.put(
        "/api/v1/onboarding",
        json={"completed": False, "experience_level": None},
    )
    assert (await client.get("/api/v1/onboarding")).json()["completed"] is False


@pytest.mark.asyncio
async def test_onboarding_partial_merge(client: AsyncClient) -> None:
    # The PUT merges (exclude_unset) rather than replacing, so independent
    # writers don't clobber each other's fields.
    await client.put(
        "/api/v1/onboarding",
        json={"completed": True, "experience_level": "expert"},
    )

    # Finishing the product tour patches only `tour_completed`; the wizard's
    # completion + experience level must survive.
    put = await client.put("/api/v1/onboarding", json={"tour_completed": True})
    assert put.json() == {
        "completed": True,
        "experience_level": "expert",
        "tour_completed": True,
    }

    # Re-tuning the experience level later must not reset the tour flag.
    await client.put(
        "/api/v1/onboarding",
        json={"completed": True, "experience_level": "beginner"},
    )
    assert (await client.get("/api/v1/onboarding")).json() == {
        "completed": True,
        "experience_level": "beginner",
        "tour_completed": True,
    }
