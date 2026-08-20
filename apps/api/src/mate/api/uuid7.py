"""UUID v7 generator (RFC 9562) - time-ordered identifiers.

Used as the canonical identity for event logs and jobs. UUID v7 sorts naturally
by creation time, which is convenient for indexing and listings while still
being globally unique without coordination.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits

    raw = (
        ts_ms.to_bytes(6, "big")
        + ((0x7 << 12) | rand_a).to_bytes(2, "big")
        + ((0x2 << 62) | rand_b).to_bytes(8, "big")
    )
    return uuid.UUID(bytes=raw)


def uuid7_str() -> str:
    return str(uuid7())
