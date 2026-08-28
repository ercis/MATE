"""OAuth-style scope taxonomy for MCP access.

One ``:read`` / ``:write``-style pair per toolset, plus ``modules:manage``
(install lifecycle) and the special ``admin`` scope. An empty grant means
"all read scopes" (back-compat for tokens minted before scoping). Tools
declare the scope they require; :func:`mate.api.mcp.core.authz` enforces it.

``admin`` is never grantable on a PAT: PAT principals are role-less by
construction (`auth/tokens.py`), so the admin toolset is reachable only via an
OAuth JWT that carries both the ``admin`` scope and the ``admin`` realm role.
"""

from __future__ import annotations

from collections.abc import Iterable

SCOPE_PROCESSES_READ = "processes:read"
SCOPE_PROCESSES_WRITE = "processes:write"
SCOPE_MODULES_READ = "modules:read"
SCOPE_MODULES_WRITE = "modules:write"
SCOPE_MODULES_MANAGE = "modules:manage"
SCOPE_DASHBOARDS_READ = "dashboards:read"
SCOPE_DASHBOARDS_WRITE = "dashboards:write"
SCOPE_JOBS_READ = "jobs:read"
SCOPE_JOBS_CONTROL = "jobs:control"
SCOPE_WATCHED_READ = "watched:read"
SCOPE_WATCHED_WRITE = "watched:write"
SCOPE_ACCOUNT_READ = "account:read"
SCOPE_ACCOUNT_WRITE = "account:write"
SCOPE_ADMIN = "admin"

READ_SCOPES: tuple[str, ...] = (
    SCOPE_PROCESSES_READ,
    SCOPE_MODULES_READ,
    SCOPE_DASHBOARDS_READ,
    SCOPE_JOBS_READ,
    SCOPE_WATCHED_READ,
    SCOPE_ACCOUNT_READ,
)

WRITE_SCOPES: tuple[str, ...] = (
    SCOPE_PROCESSES_WRITE,
    SCOPE_MODULES_WRITE,
    SCOPE_MODULES_MANAGE,
    SCOPE_DASHBOARDS_WRITE,
    SCOPE_JOBS_CONTROL,
    SCOPE_WATCHED_WRITE,
    SCOPE_ACCOUNT_WRITE,
)

ALL_SCOPES: tuple[str, ...] = READ_SCOPES + WRITE_SCOPES + (SCOPE_ADMIN,)

# What a user may put on a PAT (everything except ``admin``).
PAT_GRANTABLE_SCOPES: tuple[str, ...] = READ_SCOPES + WRITE_SCOPES

SCOPE_DESCRIPTIONS: dict[str, str] = {
    SCOPE_PROCESSES_READ: "List processes (event logs) and read their aggregate stats.",
    SCOPE_PROCESSES_WRITE: "Import, rename, filter, remap, duplicate and delete processes.",
    SCOPE_MODULES_READ: "Read module analysis outputs and datasets for your processes.",
    SCOPE_MODULES_WRITE: "Change per-module configuration (enable/disable, settings).",
    SCOPE_MODULES_MANAGE: "Uninstall modules and restore default modules.",
    SCOPE_DASHBOARDS_READ: "List and read dashboards (own and shared with you).",
    SCOPE_DASHBOARDS_WRITE: "Create, edit, share and delete dashboards.",
    SCOPE_JOBS_READ: "List background jobs and read their status/progress.",
    SCOPE_JOBS_CONTROL: "Cancel/retry jobs and pause/resume your queue.",
    SCOPE_WATCHED_READ: "List watched import folders and their file ledger.",
    SCOPE_WATCHED_WRITE: "Create, edit, scan and delete watched import folders.",
    SCOPE_ACCOUNT_READ: "Read your usage summary and API-token list.",
    SCOPE_ACCOUNT_WRITE: "Revoke your own API tokens.",
    SCOPE_ADMIN: "Platform administration (OAuth + admin realm role only).",
}


def sanitize_scopes(scopes: Iterable[str] | None, *, allow_admin: bool = False) -> list[str]:
    """Keep only known scopes, de-duplicated and ordered. Drops the unknown.

    ``admin`` is dropped unless ``allow_admin`` - a PAT mint request can never
    persist the admin scope.
    """
    if not scopes:
        return []
    seen = set(scopes)
    allowed = ALL_SCOPES if allow_admin else PAT_GRANTABLE_SCOPES
    return [s for s in allowed if s in seen]


def effective_scopes(granted: Iterable[str] | None) -> tuple[str, ...]:
    """The scopes a principal actually has: an empty grant == all read scopes."""
    cleaned = sanitize_scopes(granted)
    return tuple(cleaned) if cleaned else READ_SCOPES


def scopes_from_oauth_claims(claims: dict[str, object], roles: tuple[str, ...]) -> tuple[str, ...]:
    """Map a verified Keycloak access token onto MCP scopes.

    The JWT ``scope`` claim is a space-separated list; we take the intersection
    with our taxonomy. A token carrying none of our scopes (e.g. a plain
    browser client) falls back to the read scopes - back-compat with pre-scope
    OAuth clients. The ``admin`` scope additionally requires the ``admin``
    realm role on the same token; without the role it is silently dropped
    (tool-level authz re-checks the role anyway - defense in depth).
    """
    from mate.api.auth import ADMIN_ROLE

    raw = claims.get("scope")
    requested = set(raw.split()) if isinstance(raw, str) else set()
    known = [s for s in ALL_SCOPES if s in requested]
    if not known:
        return READ_SCOPES
    if SCOPE_ADMIN in known and ADMIN_ROLE not in roles:
        known = [s for s in known if s != SCOPE_ADMIN]
    return tuple(known) if known else READ_SCOPES
