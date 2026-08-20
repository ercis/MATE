"""Keycloak OIDC authentication (JWKS cache + FastAPI dependency)."""

from mate.api.auth.dependencies import (
    ADMIN_ROLE,
    AdminUserDep,
    CurrentUser,
    CurrentUserDep,
    get_current_user,
    get_current_user_from_token,
    require_admin,
)
from mate.api.auth.ownership import (
    get_owned_event_log,
    get_owned_folder,
    get_owned_job,
    get_owned_watched_folder,
)

__all__ = [
    "ADMIN_ROLE",
    "AdminUserDep",
    "CurrentUser",
    "CurrentUserDep",
    "get_current_user",
    "get_current_user_from_token",
    "get_owned_event_log",
    "get_owned_folder",
    "get_owned_job",
    "get_owned_watched_folder",
    "require_admin",
]
