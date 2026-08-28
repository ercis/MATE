"""Cursor pagination for MCP list tools.

Uniform envelope: ``{"items": [...], "next_cursor": str | None, "total": int?}``.
The cursor is an opaque base64 token wrapping an offset - opaque so clients
don't build arithmetic on it and we can change the encoding later.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from typing import Any

from mate.api.mcp.errors import CODE_INVALID, tool_error

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        kind, _, value = raw.partition(":")
        if kind != "o":
            raise ValueError(raw)
        offset = int(value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise tool_error(CODE_INVALID, "Malformed pagination cursor.") from exc
    return max(0, offset)


def clamp_limit(limit: int | None, *, default: int = DEFAULT_PAGE_SIZE) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def page_envelope(
    items: Sequence[Any], *, offset: int, limit: int, total: int | None = None
) -> dict[str, Any]:
    """Build the page envelope from an already-sliced ``items`` window.

    ``next_cursor`` is present when the window was full (there may be more) or
    when ``total`` proves there are more rows.
    """
    has_more = (offset + len(items)) < total if total is not None else len(items) >= limit
    out: dict[str, Any] = {
        "items": list(items),
        "next_cursor": encode_cursor(offset + len(items)) if has_more and items else None,
    }
    if total is not None:
        out["total"] = total
    return out
