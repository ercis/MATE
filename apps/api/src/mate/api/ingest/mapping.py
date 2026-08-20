"""Canonical column-role resolution for event-log ingest.

The platform contract is that every imported log exposes the three mandatory
roles - ``case_id`` / ``activity`` / ``timestamp`` - plus optional
``end_timestamp`` / ``resource`` / ``cost`` / ``role`` / ``lifecycle``. Source
files name those columns however they like, so at import we map source columns
onto the canonical roles.

Resolution is **best-effort and never blocks an import**. For each role we try,
in order:

1. an explicit user override (from the log's settings → "Column roles"),
2. an *exact* header match (case- and punctuation-insensitive) against the
   role name or one of its known aliases,
3. a *fuzzy* alias match (substring containment),
4. a *type* heuristic over a data sample (a parseable datetime for ``timestamp``,
   a high-repetition column for ``case_id``, a moderate-cardinality string for
   ``activity``).

Anything resolved by (3) or (4) - or a required role that stays unresolved -
marks the result ``needs_review`` so the UI can prompt the user to confirm the
mapping. This is the source of truth shared by every parser and the re-map flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

REQUIRED_ROLES: tuple[str, ...] = ("case_id", "activity", "timestamp")
OPTIONAL_ROLES: tuple[str, ...] = ("end_timestamp", "resource", "cost", "role", "lifecycle")
ALL_ROLES: tuple[str, ...] = REQUIRED_ROLES + OPTIONAL_ROLES

# Header aliases per role. The XES standard keys (`concept:name`,
# `time:timestamp`, …) normalise into these too, so a non-standard XES log that
# slips past the parser's standard-key map is still recoverable here.
ROLE_ALIASES: dict[str, list[str]] = {
    "case_id": ["case_id", "case", "case concept name", "trace_id", "trace", "caseid", "id"],
    "activity": ["activity", "task", "concept name", "event", "action", "operation", "step"],
    "timestamp": [
        "timestamp",
        "time",
        "datetime",
        "date",
        "time timestamp",
        "start_timestamp",
        "start",
        "start time",
        "event time",
    ],
    "end_timestamp": [
        "end_timestamp",
        "complete_timestamp",
        "time complete",
        "completion",
        "end",
        "end time",
        "finish",
        "complete",
    ],
    "resource": ["resource", "org resource", "user", "agent", "performer", "employee", "worker"],
    "cost": ["cost", "cost total", "amount", "price", "value", "total"],
    "role": ["role", "org role", "group", "position", "department"],
    "lifecycle": ["lifecycle", "lifecycle transition", "transition", "event type", "status"],
}

Quality = Literal["user", "exact", "fuzzy", "fallback"]

_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def normalise_ident(value: str) -> str:
    """Lowercase + strip non-alphanumerics so 'Case ID', 'case-id',
    'Case:Concept:Name', 'caseConceptName' all collapse to a comparable form."""
    return _NORMALISE_RE.sub("", str(value).lower())


@dataclass
class RoleResolution:
    """The outcome of resolving source columns onto canonical roles."""

    roles: dict[str, str] = field(default_factory=dict)  # role -> source column name
    quality: dict[str, Quality] = field(default_factory=dict)  # role -> how it matched

    @property
    def unresolved_required(self) -> list[str]:
        return [r for r in REQUIRED_ROLES if r not in self.roles]

    @property
    def needs_review(self) -> bool:
        """True when a human should confirm the mapping: any required role was
        guessed (fuzzy/fallback) or could not be resolved at all."""
        if self.unresolved_required:
            return True
        return any(self.quality.get(r) in ("fuzzy", "fallback") for r in REQUIRED_ROLES)

    def as_dict(self) -> dict[str, Any]:
        return {"roles": dict(self.roles), "quality": dict(self.quality)}


def resolve_roles(
    columns: list[str],
    *,
    sample: Any | None = None,
    overrides: dict[str, str] | None = None,
) -> RoleResolution:
    """Resolve canonical roles from a list of source column names.

    ``sample`` is an optional pandas DataFrame used only for the type-heuristic
    fallback. ``overrides`` maps role → source column and wins outright (the
    user picked it). A source column is claimed by at most one role.
    """
    res = RoleResolution()
    claimed: set[str] = set()
    by_norm: dict[str, list[str]] = {}
    for c in columns:
        by_norm.setdefault(normalise_ident(c), []).append(c)

    def claim(role: str, col: str, quality: Quality) -> None:
        res.roles[role] = col
        res.quality[role] = quality
        claimed.add(col)

    # 1) Explicit user overrides.
    if overrides:
        for role in ALL_ROLES:
            col = overrides.get(role)
            if col and col in columns and col not in claimed:
                claim(role, col, "user")

    # 2) Exact match (role name or any alias), then 3) fuzzy substring.
    for exact in (True, False):
        for role in ALL_ROLES:
            if role in res.roles:
                continue
            wanted = [normalise_ident(a) for a in [role, *ROLE_ALIASES.get(role, [])]]
            wanted = [w for w in wanted if w]
            match = _find_column(columns, claimed, wanted, exact=exact)
            if match is not None:
                claim(role, match, "exact" if exact else "fuzzy")

    # 4) Type heuristics for any still-missing *required* role.
    if sample is not None:
        for role in REQUIRED_ROLES:
            if role in res.roles:
                continue
            col = _heuristic_for(role, columns, claimed, sample)
            if col is not None:
                claim(role, col, "fallback")

    return res


def _find_column(
    columns: list[str], claimed: set[str], wanted_norms: list[str], *, exact: bool
) -> str | None:
    for col in columns:
        if col in claimed:
            continue
        norm = normalise_ident(col)
        for w in wanted_norms:
            if (norm == w) if exact else (w in norm or norm in w):
                return col
    return None


def _heuristic_for(role: str, columns: list[str], claimed: set[str], sample: Any) -> str | None:
    """Last-resort, data-driven guess for a required role."""
    import pandas as pd

    avail = [c for c in columns if c not in claimed and c in getattr(sample, "columns", [])]
    if not avail:
        return None

    if role == "timestamp":
        # First column where the majority of non-null values parse as a date.
        best: tuple[float, str] | None = None
        for c in avail:
            series = sample[c].dropna()
            if series.empty:
                continue
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
            ratio = float(parsed.notna().mean())
            if ratio >= 0.7 and (best is None or ratio > best[0]):
                best = (ratio, c)
        return best[1] if best else None

    if role == "case_id":
        # Identifiers repeat across a case's events → lowest distinct ratio
        # (but more than one distinct value, i.e. not a constant column).
        best_ratio = 1.1
        choice: str | None = None
        n = max(len(sample), 1)
        for c in avail:
            distinct = int(sample[c].nunique(dropna=True))
            if distinct <= 1:
                continue
            ratio = distinct / n
            if ratio < best_ratio:
                best_ratio, choice = ratio, c
        return choice

    if role == "activity":
        # A moderate-cardinality, mostly-textual column: more than one value but
        # not unique-per-row.
        n = max(len(sample), 1)
        choice = None
        best_score = -1.0
        for c in avail:
            distinct = int(sample[c].nunique(dropna=True))
            if distinct <= 1 or distinct >= n:
                continue
            # Prefer non-numeric columns and middling cardinality.
            is_text = sample[c].dropna().map(lambda v: not _looks_numeric(v)).mean()
            score = float(is_text) - abs((distinct / n) - 0.1)
            if score > best_score:
                best_score, choice = score, c
        return choice

    return None


def _looks_numeric(value: Any) -> bool:
    try:
        float(str(value))
        return True
    except (TypeError, ValueError):
        return False


def dedupe_case_insensitive_columns(columns: list[str]) -> dict[str, str]:
    """Map columns onto names that are unique *case-insensitively*.

    The events dataset is always queried through DuckDB, whose column
    identifiers are case-insensitive: a frame carrying two columns that differ
    only in case (e.g. a domain ``Activity`` attribute alongside the canonical
    ``activity`` role) collapses on read - DuckDB silently renames the second to
    ``activity_1`` - so modules reading ``activity`` get the wrong column or
    none at all. We therefore guarantee at write time that no two stored columns
    collide.

    Canonical roles win their exact slot; any other column that case-collides is
    moved aside to ``<name>__src`` (``__src2`` … on further collisions). Returns
    an ``old -> new`` rename map (identity for untouched columns).
    """
    taken: dict[str, str] = {}  # lower-cased name -> the column that owns it
    rename: dict[str, str] = {}
    # Reserved canonical roles claim their slot first, so a stray case-variant
    # of a role never displaces the normalised column the platform relies on.
    for col in columns:
        if col in ALL_ROLES and col.lower() not in taken:
            taken[col.lower()] = col
            rename[col] = col
    for col in columns:
        if col in rename:
            continue
        low = col.lower()
        if low not in taken:
            taken[low] = col
            rename[col] = col
            continue
        candidate = f"{col}__src"
        n = 2
        while candidate.lower() in taken:
            candidate = f"{col}__src{n}"
            n += 1
        taken[candidate.lower()] = candidate
        rename[col] = candidate
    return rename


def apply_roles(df: Any, resolution: RoleResolution) -> Any:
    """Rename a DataFrame's source columns onto their canonical role names.

    Collision-safe: if a *non-chosen* column already carries a canonical name
    (e.g. a stray ``activity`` column while the user mapped activity onto
    ``Activity``), it is moved aside to ``<name>__src`` so the chosen source
    wins the canonical slot. Returns the same frame for chaining.
    """
    targets = {role: src for role, src in resolution.roles.items()}
    chosen = set(targets.values())
    # Displace any column that occupies a target canonical name but isn't the
    # column we picked for that role.
    displace: dict[str, str] = {}
    for role in targets:
        if role in df.columns and df.columns.tolist().count(role) and role not in chosen:
            displace[role] = f"{role}__src"
    if displace:
        df = df.rename(columns=displace)
    rename = {src: role for role, src in targets.items() if src != role}
    if rename:
        df = df.rename(columns=rename)
    return df
