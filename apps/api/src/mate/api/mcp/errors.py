"""Error taxonomy for MCP tools.

FastMCP surfaces a raised exception's message as the JSON-RPC tool error, so
the contract is a ``ValueError`` subclass whose message carries a stable,
machine-readable ``[code]`` prefix an agent can branch on, followed by a
human-readable explanation. Codes:

    not_found | forbidden | conflict | invalid | rate_limited | timeout |
    read_only | consent_required | scope_missing | confirm_required | internal
"""

from __future__ import annotations

from fastapi import HTTPException

CODE_NOT_FOUND = "not_found"
CODE_FORBIDDEN = "forbidden"
CODE_CONFLICT = "conflict"
CODE_INVALID = "invalid"
CODE_RATE_LIMITED = "rate_limited"
CODE_TIMEOUT = "timeout"
CODE_READ_ONLY = "read_only"
CODE_CONSENT_REQUIRED = "consent_required"
CODE_SCOPE_MISSING = "scope_missing"
CODE_CONFIRM_REQUIRED = "confirm_required"
CODE_INTERNAL = "internal"


class MCPToolError(ValueError):
    """A tool failure with a stable machine-readable code.

    Subclasses ``ValueError`` so FastMCP's error handling (and every existing
    caller/test that catches ``ValueError``) keeps working unchanged.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


def tool_error(code: str, message: str) -> MCPToolError:
    return MCPToolError(code, message)


_STATUS_TO_CODE = {
    400: CODE_INVALID,
    401: CODE_FORBIDDEN,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    409: CODE_CONFLICT,
    413: CODE_INVALID,
    422: CODE_INVALID,
    429: CODE_RATE_LIMITED,
}


def from_http_exception(exc: HTTPException) -> MCPToolError:
    """Translate an internal route-layer HTTPException into a tool error."""
    code = _STATUS_TO_CODE.get(exc.status_code, CODE_INTERNAL)
    return MCPToolError(code, str(exc.detail))
