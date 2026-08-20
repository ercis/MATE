"""Pydantic schemas for dashboard sharing + admin team management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class UserBrief(BaseModel):
    """Minimal user identity for member pickers and share/owner labels."""

    id: str
    email: str | None = None
    preferred_username: str | None = None
    name: str | None = None


class TeamOut(BaseModel):
    id: str
    name: str
    member_count: int = 0
    created_at: datetime


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def _strip(self) -> TeamCreate:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Team name cannot be empty.")
        return self


class TeamUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def _strip(self) -> TeamUpdate:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Team name cannot be empty.")
        return self


class TeamMemberOut(BaseModel):
    user_id: str
    role: str = "member"
    email: str | None = None
    preferred_username: str | None = None
    name: str | None = None
    created_at: datetime


class MemberAdd(BaseModel):
    user_id: str
    role: Literal["owner", "member"] = "member"


# --- Dashboard shares (user-facing) ---


class ShareCreate(BaseModel):
    """Add a share to a dashboard. Exactly one target must be set."""

    target_user_id: str | None = None
    target_team_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ShareCreate:
        if bool(self.target_user_id) == bool(self.target_team_id):
            raise ValueError("Provide exactly one of target_user_id or target_team_id.")
        return self


class DashboardShareOut(BaseModel):
    id: str
    dashboard_id: str
    kind: Literal["user", "team"]
    target_id: str
    label: str
    created_at: datetime


class ShareTarget(BaseModel):
    """A candidate the current user can share a dashboard with."""

    kind: Literal["user", "team"]
    id: str
    label: str
    sublabel: str | None = None


class SharedDashboard(BaseModel):
    """A dashboard another user shared with me - summary + owner label."""

    id: str
    name: str
    description: str | None = None
    event_log_id: str | None = None
    log_model: str = "case_centric"
    card_count: int = 0
    owner_label: str
    updated_at: datetime


# --- Admin oversight ---


class AdminShareOut(BaseModel):
    id: str
    dashboard_id: str
    dashboard_name: str
    owner_label: str
    target_kind: Literal["user", "team"]
    target_label: str
    created_at: datetime
