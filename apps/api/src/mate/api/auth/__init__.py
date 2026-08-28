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
from mate.api.auth.tokens import TOKEN_PREFIX, mint_token, verify_token, verify_token_row

__all__ = [
    "ADMIN_ROLE",
    "TOKEN_PREFIX",
    "AdminUserDep",
    "CurrentUser",
    "CurrentUserDep",
    "get_current_user",
    "get_current_user_from_token",
    "get_owned_event_log",
    "get_owned_folder",
    "get_owned_job",
    "get_owned_watched_folder",
    "mint_token",
    "require_admin",
    "verify_token",
    "verify_token_row",
]
