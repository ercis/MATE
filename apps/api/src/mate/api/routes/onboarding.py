"""/api/v1/onboarding - per-user onboarding state.

Completion lives in the per-user ``user_settings`` table (key ``onboarding``)
rather than browser localStorage. That makes the welcome flow correct under
multi-user: a brand-new Keycloak user always sees the overlay - even on a
browser where another account already finished it - and a returning user never
re-sees it regardless of which browser they sign in from.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from mate.api.auth import CurrentUserDep
from mate.api.db.models import UserSetting
from mate.api.db.session import SessionDep

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

ONBOARDING_KEY = "onboarding"

ExperienceLevel = Literal["beginner", "intermediate", "expert"]


class OnboardingState(BaseModel):
    completed: bool = False
    experience_level: ExperienceLevel | None = None
    # Whether the interactive product tour (the high-level platform
    # walkthrough) has been finished or skipped. Tracked separately from
    # ``completed`` (the setup wizard) so the tour can auto-chain after the
    # wizard yet be replayed on demand without re-opening the wizard.
    tour_completed: bool = False


def _load(row: UserSetting | None) -> OnboardingState:
    if row is None or not isinstance(row.value_json, dict):
        return OnboardingState()
    return OnboardingState.model_validate(row.value_json)


@router.get("", response_model=OnboardingState)
async def get_onboarding(session: SessionDep, user: CurrentUserDep) -> OnboardingState:
    # No row → not completed yet, so a new user gets the overlay.
    row = await session.get(UserSetting, (user.id, ONBOARDING_KEY))
    return _load(row)


@router.put("", response_model=OnboardingState)
async def put_onboarding(
    payload: OnboardingState, session: SessionDep, user: CurrentUserDep
) -> OnboardingState:
    # Merge, don't replace: callers PATCH a single field - the tour marks
    # ``tour_completed`` without knowing the wizard's ``experience_level``, and
    # Settings re-tunes ``experience_level`` without touching the tour flag.
    # ``exclude_unset`` keeps fields the client didn't send from being written
    # back to their model defaults and clobbering the stored value.
    incoming = payload.model_dump(mode="json", exclude_unset=True)
    row = await session.get(UserSetting, (user.id, ONBOARDING_KEY))
    base = row.value_json if row is not None and isinstance(row.value_json, dict) else {}
    merged = {**base, **incoming}
    if row is None:
        session.add(UserSetting(user_id=user.id, key=ONBOARDING_KEY, value_json=merged))
    else:
        row.value_json = merged
    await session.commit()
    return OnboardingState.model_validate(merged)
