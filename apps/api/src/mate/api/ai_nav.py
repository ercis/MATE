"""Intent-based navigation routing for MATE AI.

Turns a free-text chat message into zero or more *navigation suggestions* -
clickable targets that drop the user inside a module panel or a platform page.

Pipeline (see ``route_intent``):

1. Build a per-user **destination registry** (``build_user_destinations``): the
   static platform pages plus every module the user has *enabled*. Tenant
   isolation is preserved - only the requesting user's installs are visible.
2. A cheap **local keyword pre-filter** (``prefilter``) provides a single fast
   path: an explicit navigation verb *and* exactly one matching destination →
   navigate straight there without an LLM call.
3. Every other message goes to the **LLM classifier** (``classify_intent``).
   We deliberately do *not* short-circuit "pure chat" on keyword absence -
   that misses non-English wording and paraphrases - so recall is prioritised
   over saving a (cheap) classifier call. It reuses ``structured_completion``
   so UniGPT/custom backends get the prompted-JSON fallback for free, and runs
   on ``classifier_model`` (a cheaper model, same provider) when configured.
4. The chosen target ids are **resolved** to concrete hrefs (``resolve_targets``),
   handling the module-panel-needs-a-log case.

Kept out of ``routes/`` for the same import-cycle reason as ``ai_config`` and
``ai_guidance``.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.ai_config import AiConfigPayload
from mate.api.ai_guidance import GuidanceError, structured_completion

log = structlog.get_logger(__name__)

# A suggestion below this confidence is dropped - navigation is additive, so we
# err towards *not* nagging the user with a low-confidence guess.
NAV_CONFIDENCE_THRESHOLD = 0.7
# Confidence we assign to a deterministic (non-LLM) pre-filter hit.
PREFILTER_CONFIDENCE = 0.95


# ── Destination model ───────────────────────────────────────────────────────


class NavDestination(BaseModel):
    """One thing the user can be routed to (a module panel or a platform page)."""

    id: str
    label: str
    kind: Literal["module", "page"]
    # ``str.format(log_id=...)`` template. Pages ignore the placeholder.
    href_template: str
    requires_log: bool
    keywords: list[str] = []
    description: str = ""


class NavTarget(BaseModel):
    """A resolved, clickable suggestion handed to the frontend."""

    id: str
    label: str
    kind: str
    href: str
    # True when the target needs a process/log that the current context lacks -
    # the frontend renders it as a hint ("open a process first") rather than a
    # dead link, and falls back ``href`` to the module's config page.
    requires_log: bool
    available: bool


class ActionTarget(BaseModel):
    """A resolved, whitelisted settings change the user can apply with one click."""

    setting: str  # canonical id from SETTING_WHITELIST
    value: str | bool  # validated/coerced value
    label: str  # human chip label, e.g. "Switch to Light mode"
    target: str  # which client subsystem applies it: "theme" | "ui" | "onboarding"


class RoutingResult(BaseModel):
    intent: Literal["chat", "navigate", "both"]
    confidence: float
    targets: list[NavTarget]
    actions: list[ActionTarget] = []


# ── Static platform pages ───────────────────────────────────────────────────

# Routes mirror the web app's sidebar + settings tabs. Keywords are curated so
# the pre-filter and the LLM both have strong signal for non-module surfaces.
PLATFORM_PAGES: list[NavDestination] = [
    NavDestination(
        id="processes",
        label="Processes",
        kind="page",
        href_template="/processes",
        requires_log=False,
        keywords=["process", "processes", "event log", "logs", "cases", "upload log", "prozesse"],
        description="Browse, open and manage uploaded event logs / processes.",
    ),
    NavDestination(
        id="processes.import",
        label="Import a process",
        kind="page",
        href_template="/processes/import",
        requires_log=False,
        keywords=["import", "upload", "new log", "add log", "csv", "xes", "ingest", "importieren"],
        description="Upload a new XES/CSV event log.",
    ),
    NavDestination(
        id="dashboards",
        label="Dashboards",
        kind="page",
        href_template="/dashboards",
        requires_log=False,
        keywords=["dashboard", "dashboards", "board", "widgets", "overview", "übersicht"],
        description="Create and view dashboards composed of module widgets.",
    ),
    NavDestination(
        id="modules",
        label="Modules",
        kind="page",
        href_template="/modules",
        requires_log=False,
        keywords=["module", "modules", "install module", "enable module", "plugins", "module"],
        description="Install, enable/disable and configure analysis modules.",
    ),
    NavDestination(
        id="modules.import",
        label="Install a module",
        kind="page",
        href_template="/modules/import",
        requires_log=False,
        keywords=["install module", "upload module", "add module", "new module", "modul installieren"],
        description="Upload/install a new module package.",
    ),
    NavDestination(
        id="settings.general",
        label="General settings",
        kind="page",
        href_template="/settings/general",
        requires_log=False,
        keywords=["settings", "preferences", "general", "einstellungen", "theme", "appearance"],
        description="General platform preferences.",
    ),
    NavDestination(
        id="settings.ai",
        label="AI settings",
        kind="page",
        href_template="/settings/ai",
        requires_log=False,
        keywords=[
            "ai settings",
            "ai config",
            "api key",
            "model",
            "provider",
            "anthropic",
            "openai",
            "unigpt",
            "system prompt",
            "ki einstellungen",
        ],
        description="Configure the AI provider, API key, models and system prompt.",
    ),
    NavDestination(
        id="settings.privacy",
        label="Privacy settings",
        kind="page",
        href_template="/settings/privacy",
        requires_log=False,
        keywords=["privacy", "data collection", "analytics", "tracking", "datenschutz", "consent"],
        description="Data-collection and privacy preferences.",
    ),
    NavDestination(
        id="settings.about",
        label="About",
        kind="page",
        href_template="/settings/about",
        requires_log=False,
        keywords=["about", "version", "build", "license", "über"],
        description="Version and build information.",
    ),
    NavDestination(
        id="profile",
        label="Profile",
        kind="page",
        href_template="/profile",
        requires_log=False,
        keywords=["profile", "account", "my account", "password", "sign out", "profil", "konto"],
        description="Your user profile and account.",
    ),
]


# ── Registry construction ───────────────────────────────────────────────────


def _derive_keywords(name: str, description: str | None, provides: list[str]) -> list[str]:
    """Fallback keywords when a manifest declares none."""
    words: list[str] = []
    words.extend(re.findall(r"[a-z0-9]+", name.lower()))
    if description:
        words.extend(re.findall(r"[a-z0-9]+", description.lower())[:12])
    # Capability ids like "discovery.petri_net.alpha" → "discovery", "petri", ...
    for cap in provides:
        words.extend(re.findall(r"[a-z0-9]+", cap.lower()))
    # De-dupe, drop trivially short tokens, keep order.
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if len(w) < 3 or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:20]


async def build_user_destinations(session: AsyncSession, user_id: str) -> list[NavDestination]:
    """Static pages + the modules this user has installed *and* enabled."""
    dests = list(PLATFORM_PAGES)

    # Local imports keep this module free of the loader's lifecycle at import
    # time (the loader pulls in the whole module subsystem).
    from fastapi import HTTPException

    from mate.api.db.models import ModuleConfig
    from mate.api.modules import get_module_loader
    from mate.api.modules.installs import user_module_ids

    try:
        loader = get_module_loader()
    except HTTPException:
        return dests

    manifests = loader.manifests()
    if not manifests:
        return dests

    owned = await user_module_ids(session, user_id)
    rows = await session.execute(
        select(ModuleConfig.module_id, ModuleConfig.enabled).where(ModuleConfig.user_id == user_id)
    )
    enabled_map: dict[str, bool] = {mid: en for mid, en in rows.all()}

    for m in manifests:
        if m.id not in owned:
            continue
        if not enabled_map.get(m.id, m.default_enabled):
            continue
        keywords = list(m.keywords) or _derive_keywords(m.name, m.description, list(m.provides))
        dests.append(
            NavDestination(
                id=m.id,
                label=m.name,
                kind="module",
                href_template="/processes/{log_id}/modules/" + m.id,
                requires_log=True,
                keywords=keywords,
                description=(m.description or m.name)[:300],
            )
        )
    return dests


def build_destination_catalog(destinations: list[NavDestination]) -> str:
    """Render the registry as a compact list for the classifier system prompt."""
    lines: list[str] = []
    for d in destinations:
        kw = ", ".join(d.keywords[:8])
        lines.append(f"- id={d.id} | {d.label} ({d.kind}): {d.description} | keywords: {kw}")
    return "\n".join(lines)


# ── Processes (sensitive - only used when the user enables process-data access) ─

# Cap how many processes we send to the LLM, newest first, to bound prompt size.
_MAX_PROCESSES = 50


class ProcessInfo(BaseModel):
    """A user's event log / process and its cheap row-level stats."""

    id: str
    name: str
    log_model: str = "case_centric"
    cases_count: int | None = None
    events_count: int | None = None
    variants_count: int | None = None
    objects_count: int | None = None
    object_types_count: int | None = None
    date_min: str | None = None
    date_max: str | None = None


async def list_user_processes(session: AsyncSession, user_id: str) -> list[ProcessInfo]:
    """The user's ready event logs with row-level stats (no expensive queries)."""
    from sqlalchemy import desc

    from mate.api.db.models import EventLog

    rows = await session.execute(
        select(EventLog)
        .where(
            EventLog.user_id == user_id,
            EventLog.deleted_at.is_(None),
            EventLog.status == "ready",
        )
        .order_by(desc(EventLog.created_at))
        .limit(_MAX_PROCESSES)
    )
    out: list[ProcessInfo] = []
    for r in rows.scalars().all():
        out.append(
            ProcessInfo(
                id=r.id,
                name=r.name,
                log_model=r.log_model,
                cases_count=r.cases_count,
                events_count=r.events_count,
                variants_count=r.variants_count,
                objects_count=r.objects_count,
                object_types_count=r.object_types_count,
                date_min=r.date_min.isoformat() if r.date_min else None,
                date_max=r.date_max.isoformat() if r.date_max else None,
            )
        )
    return out


def build_process_catalog(processes: list[ProcessInfo]) -> str:
    """Compact process list for the classifier so it can fill the 'process' field.

    Only id + name - never stats. Navigation by process name is always allowed,
    so this is sent regardless of the process-data toggle; the sensitive stats
    (variants/cases/…) are gated separately and never appear here.
    """
    # Lead with the human name so the model copies that into "process" (it tends
    # to echo whatever it sees first); the id is secondary context.
    return "\n".join(f'- "{p.name}" (internal id: {p.id})' for p in processes)


def match_process(hint: str | None, processes: list[ProcessInfo]) -> ProcessInfo | None:
    """Resolve a process the user named (by id or fuzzy name) to a ProcessInfo."""
    if not hint:
        return None
    h = hint.strip().strip('"').lower()
    # The model sometimes echoes the catalog's framing - strip leading labels.
    for prefix in ("internal id:", "id=", "id:", "name=", "name:"):
        if h.startswith(prefix):
            h = h[len(prefix):].strip().strip('"')
    if not h:
        return None
    for p in processes:  # exact id
        if p.id.lower() == h:
            return p
    for p in processes:  # exact name
        if p.name.lower() == h:
            return p
    for p in processes:  # substring either way
        n = p.name.lower()
        if h in n or n in h:
            return p
    return None


# ── Settings actions (whitelisted, applied client-side on click) ─────────────

# Canonical setting id -> spec. ONLY settings listed here can ever be produced;
# the frontend has a matching explicit setter map. Anything sensitive - API keys,
# system prompt, provider/model, allow_process_data, analytics/privacy consent,
# onboarding completion, account/email/password, data wipe/export, module
# install/uninstall - is deliberately ABSENT here and rejected, so the AI can
# never change it.
_BOOL_TRUE = {"true", "on", "yes", "enable", "enabled", "1", "mute", "muted",
              "collapse", "collapsed", "show", "shown"}
_BOOL_FALSE = {"false", "off", "no", "disable", "disabled", "0", "unmute",
               "expand", "expanded", "hide", "hidden"}
_DELIMITER_SYNONYMS = {"comma": ",", ",": ",", "semicolon": ";", ";": ";",
                       "tab": "\t", "\\t": "\t", "\t": "\t", "pipe": "|", "|": "|"}

SETTING_WHITELIST: dict[str, dict[str, Any]] = {
    "theme": {"target": "theme", "kind": "enum", "values": ("light", "dark", "system")},
    "notifications_muted": {"target": "ui", "kind": "bool"},
    "confidential_only": {"target": "ui", "kind": "bool"},
    "sidebar_collapsed": {"target": "ui", "kind": "bool"},
    "show_unavailable_modules": {"target": "ui", "kind": "bool"},
    "show_disabled_modules": {"target": "ui", "kind": "bool"},
    "date_format": {"target": "ui", "kind": "enum", "values": ("iso", "us", "eu")},
    "timezone": {"target": "ui", "kind": "tz"},
    "csv_delimiter": {"target": "ui", "kind": "delimiter"},
    "csv_timestamp_format": {"target": "ui", "kind": "str", "maxlen": 64},
    "experience_level": {
        "target": "onboarding",
        "kind": "enum",
        "values": ("beginner", "intermediate", "expert"),
    },
}


def build_settings_catalog() -> str:
    """Allowed settings + value domains for the classifier prompt."""
    lines: list[str] = []
    for sid, spec in SETTING_WHITELIST.items():
        kind = spec["kind"]
        if kind == "enum":
            dom = "|".join(spec["values"])
        elif kind == "bool":
            dom = "true|false"
        elif kind == "tz":
            dom = "an IANA timezone e.g. Europe/Berlin"
        elif kind == "delimiter":
            dom = "comma|semicolon|tab|pipe"
        else:
            dom = "free text"
        lines.append(f"- {sid}: {dom}")
    return "\n".join(lines)


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in _BOOL_TRUE:
        return True
    if v in _BOOL_FALSE:
        return False
    return None


def _action_label(setting: str, value: str | bool) -> str:
    match setting:
        case "theme":
            return "Use system theme" if value == "system" else f"Switch to {value} mode"
        case "notifications_muted":
            return "Mute notifications" if value else "Unmute notifications"
        case "confidential_only":
            return "Show only confidential-safe modules" if value else "Show all modules"
        case "sidebar_collapsed":
            return "Collapse the sidebar" if value else "Expand the sidebar"
        case "show_unavailable_modules":
            return "Show unavailable modules" if value else "Hide unavailable modules"
        case "show_disabled_modules":
            return "Show disabled modules" if value else "Hide disabled modules"
        case "date_format":
            return f"Use {str(value).upper()} date format"
        case "timezone":
            return f"Set timezone to {value}"
        case "csv_delimiter":
            names = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}
            return f"Set CSV delimiter to {names.get(str(value), str(value))}"
        case "csv_timestamp_format":
            return f"Set CSV timestamp format to {value}"
        case "experience_level":
            return f"Set experience level to {str(value).capitalize()}"
        case _:
            return f"Change {setting}"


def resolve_action(raw: Any) -> ActionTarget | None:
    """Validate a classifier 'action' against the whitelist; None on any failure.

    This is the security boundary: a setting not in SETTING_WHITELIST (blocked or
    hallucinated) or a value outside its domain yields no action - so the AI can
    only ever offer safe, well-formed changes.
    """
    if not isinstance(raw, dict):
        return None
    setting = raw.get("setting")
    if not isinstance(setting, str):
        return None
    setting = setting.strip().lower()
    spec = SETTING_WHITELIST.get(setting)
    if spec is None:
        return None
    raw_value = raw.get("value")
    if raw_value is None:
        return None

    kind = spec["kind"]
    value: str | bool
    if kind == "bool":
        b = _coerce_bool(raw_value)
        if b is None:
            return None
        value = b
    elif kind == "enum":
        v = str(raw_value).strip().lower()
        if v not in spec["values"]:
            return None
        value = v
    elif kind == "tz":
        v = str(raw_value).strip()
        try:
            ZoneInfo(v)
        except Exception:
            return None
        value = v
    elif kind == "delimiter":
        mapped = _DELIMITER_SYNONYMS.get(str(raw_value).strip().lower())
        if mapped is None:
            return None
        value = mapped
    else:  # free-text str
        v = str(raw_value).strip()
        if not v or len(v) > int(spec.get("maxlen", 200)):
            return None
        value = v

    return ActionTarget(
        setting=setting,
        value=value,
        label=_action_label(setting, value),
        target=str(spec["target"]),
    )


# ── Local keyword pre-filter ────────────────────────────────────────────────

# Explicit navigation verbs/phrases (EN + DE). Their presence is a strong signal
# the user wants to *go somewhere*, not just chat about it.
NAV_CUES: tuple[str, ...] = (
    "open ",
    "go to",
    "navigate",
    "take me",
    "show me the",
    "bring me",
    "jump to",
    "switch to",
    "where do i",
    "where can i",
    "öffne",
    "geh zu",
    "geh zur",
    "gehe zu",
    "zeig mir",
    "zeige mir",
    "bring mich",
    "wechsle",
    "spring",
    "navigiere",
    "wo finde ich",
    "wo kann ich",
)

_WORD_RE = re.compile(r"[a-z0-9]+")


class PrefilterResult(BaseModel):
    has_cue: bool
    matches: list[str]  # destination ids, best first


def _destination_matches(text_words: set[str], raw_text: str, dest: NavDestination) -> bool:
    # Multi-word keywords match as substrings; single words match on token
    # boundaries so "ai" doesn't fire inside "maintain".
    label_words = {w for w in _WORD_RE.findall(dest.label.lower())}
    for kw in [*dest.keywords, dest.label.lower(), dest.id.replace(".", " ")]:
        kw = kw.strip().lower()
        if not kw:
            continue
        if " " in kw or "." in kw:
            if kw.replace(".", " ") in raw_text:
                return True
        elif kw in text_words:
            return True
    return bool(label_words & text_words)


def prefilter(message: str, destinations: list[NavDestination]) -> PrefilterResult:
    raw = message.lower()
    words = set(_WORD_RE.findall(raw))
    has_cue = any(cue in raw for cue in NAV_CUES)
    matches = [d.id for d in destinations if _destination_matches(words, raw, d)]
    return PrefilterResult(has_cue=has_cue, matches=matches)


# ── LLM classifier ──────────────────────────────────────────────────────────

ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # All properties listed in `required` (OpenAI strict json_schema needs this);
    # optionality is expressed via nullable types instead.
    "required": ["intent", "targets", "process", "action", "confidence"],
    "properties": {
        "intent": {"enum": ["chat", "navigate", "both"]},
        "targets": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string"},
            "description": "Destination ids from the provided list, best match first.",
        },
        "process": {
            "type": ["string", "null"],
            "description": (
                "If the user names a specific process/event log (and one is listed "
                "under 'Available processes'), put its exact id or name here; "
                "otherwise null."
            ),
        },
        "action": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["setting", "value"],
            "properties": {
                "setting": {"type": ["string", "null"]},
                "value": {"type": ["string", "null"]},
            },
            "description": (
                "A settings change the user EXPLICITLY asked for, using only the "
                "allowed settings listed below. Otherwise null."
            ),
        },
        # No `minimum`/`maximum` here: OpenAI's strict json_schema mode rejects
        # numeric range constraints, which would force a wasteful fallback to
        # prompted-JSON on every call. We clamp to [0,1] in `_coerce_routing`
        # instead. Keep the expected range in the description for the model.
        "confidence": {
            "type": "number",
            "description": "Certainty that navigation is wanted, from 0.0 to 1.0.",
        },
    },
}

_ROUTING_SYSTEM_PROMPT = """\
You are the navigation-intent classifier for MATE, a process-mining platform.
Given a single user chat message, decide whether the user wants to NAVIGATE to a
module or page, just CHAT, or BOTH.

Rules:
- "chat": a question/answer with no intent to move somewhere.
- "navigate": the user clearly wants to open/go to a specific module or page.
- "both": the message is a question AND implies a place to work on it.
- Only use destination ids from the list below. Never invent ids.
- "targets" MUST contain the module/page the user wants to open. Always fill it
  when navigating - even if the user also names a process. The process belongs in
  "process", NEVER in "targets".
    Example: "open the process discovery module of the helpdesk process"
      -> targets=["discovery"], process="helpdesk"
    Example: "show me the complexity of the Order log"
      -> targets=["complexity"], process="Order log"
- If the user names a specific process that appears under 'Available processes',
  set "process" to that process's NAME (not the id). Otherwise set "process" to null.
- If (and only if) the user EXPLICITLY asks to change one of the settings under
  'Allowed settings' below, set "action" to {"setting": <id>, "value": <string>}
  using a setting id and value-domain from that list. For anything not listed
  (API keys, system prompt, provider/model, process-data access, privacy/analytics,
  password/email, deleting data) set "action" to null and do not act on it.
    Example: "switch to dark mode" -> action={"setting":"theme","value":"dark"}
    Example: "mute notifications"  -> action={"setting":"notifications_muted","value":"true"}
- If nothing fits, return intent="chat", targets=[], confidence below 0.5.
- "targets" lists at most 3 ids, the best match first.
- "confidence" is your certainty (0-1) that navigation is genuinely wanted.

Available destinations:
"""


def _coerce_routing(obj: Any, valid_ids: set[str]) -> dict[str, Any]:
    """Best-effort validation of the model's raw JSON against our schema."""
    if not isinstance(obj, dict):
        raise GuidanceError("Classifier returned a non-object response.")
    intent = str(obj.get("intent", "chat")).lower()
    if intent not in ("chat", "navigate", "both"):
        intent = "chat"
    raw_targets = obj.get("targets")
    targets: list[str] = []
    if isinstance(raw_targets, list):
        for t in raw_targets:
            tid = str(t)
            if tid in valid_ids and tid not in targets:
                targets.append(tid)
            if len(targets) >= 3:
                break
    raw_process = obj.get("process")
    process = str(raw_process).strip() if isinstance(raw_process, str) and raw_process.strip() else None
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "intent": intent,
        "targets": targets,
        "process": process,
        "action": obj.get("action"),  # validated later by resolve_action
        "confidence": confidence,
    }


async def classify_intent(
    cfg: AiConfigPayload,
    *,
    message: str,
    destinations: list[NavDestination],
    processes: list[ProcessInfo] | None = None,
) -> dict[str, Any]:
    """Run the LLM classifier on the (cheaper) classifier model."""
    # Use the classifier model when set; keep the user's saved system prompt out
    # of the classification so it can't skew the routing decision.
    classifier_cfg = cfg.model_copy(
        update={
            "selected_model": cfg.classifier_model or cfg.selected_model,
            "system_prompt": "",
        }
    )
    system_prompt = _ROUTING_SYSTEM_PROMPT + build_destination_catalog(destinations)
    system_prompt += "\n\nAllowed settings (for 'action'):\n" + build_settings_catalog()
    # The process list is sensitive, so it's only present when the user enabled
    # process-data access (the caller passes an empty list otherwise).
    if processes:
        system_prompt += "\n\nAvailable processes:\n" + build_process_catalog(processes)
    raw = await structured_completion(
        classifier_cfg,
        system_prompt=system_prompt,
        payload={"message": message},
        schema=ROUTING_SCHEMA,
        tool_name="route_intent",
        user_prefix="Classify this user message:",
    )
    return _coerce_routing(raw, {d.id for d in destinations})


# ── Resolution ──────────────────────────────────────────────────────────────


def resolve_targets(
    intent: str,
    target_ids: list[str],
    confidence: float,
    destinations: list[NavDestination],
    log_id: str | None,
    *,
    processes: list[ProcessInfo] | None = None,
    process_hint: str | None = None,
    threshold: float = NAV_CONFIDENCE_THRESHOLD,
) -> list[NavTarget]:
    """Turn classifier output into concrete clickable targets.

    For module panels the target log is, in order of preference: a process the
    user explicitly named (``process_hint`` → matched against ``processes``),
    otherwise the process the user is currently in (``log_id``), otherwise a
    fallback to the module's config page.
    """
    if intent == "chat" or confidence < threshold:
        return []
    matched = match_process(process_hint, processes or [])
    by_id = {d.id: d for d in destinations}
    out: list[NavTarget] = []
    for tid in target_ids:
        d = by_id.get(tid)
        if d is None:
            continue
        if d.requires_log:
            target_log = matched.id if matched else log_id
            # Name the process in the chip label when it isn't the current one.
            label = f"{d.label} - {matched.name}" if matched else d.label
            if target_log:
                out.append(
                    NavTarget(
                        id=d.id,
                        label=label,
                        kind=d.kind,
                        href=d.href_template.format(log_id=target_log),
                        requires_log=False,
                        available=True,
                    )
                )
            else:
                # No process named or in context - fall back to the module's
                # config page and flag that a process is needed for the panel.
                out.append(
                    NavTarget(
                        id=d.id,
                        label=d.label,
                        kind=d.kind,
                        href=f"/modules/{d.id}",
                        requires_log=True,
                        available=False,
                    )
                )
        else:
            out.append(
                NavTarget(
                    id=d.id,
                    label=d.label,
                    kind=d.kind,
                    href=d.href_template,
                    requires_log=False,
                    available=True,
                )
            )
    return out


def current_destination(
    path: str | None, destinations: list[NavDestination]
) -> NavDestination | None:
    """The destination the user is currently viewing, matched from the URL path."""
    if not path:
        return None
    p = path.rstrip("/") or "/"
    # Module panel: /processes/<log>/modules/<id>
    if "/modules/" in p:
        mod_id = p.rsplit("/modules/", 1)[1].split("/")[0]
        for d in destinations:
            if d.kind == "module" and d.id == mod_id:
                return d
    # Pages: exact or prefix match; prefer the longest (most specific) href.
    best: NavDestination | None = None
    for d in destinations:
        if d.requires_log:
            continue
        href = d.href_template.rstrip("/")
        if p == href or p.startswith(href + "/"):
            if best is None or len(href) > len(best.href_template.rstrip("/")):
                best = d
    return best


# ── Orchestration ───────────────────────────────────────────────────────────


async def route_intent(
    cfg: AiConfigPayload,
    *,
    message: str,
    destinations: list[NavDestination],
    log_id: str | None,
    current_path: str | None = None,
    processes: list[ProcessInfo] | None = None,
) -> RoutingResult:
    """Full pipeline: navigate fast-path → otherwise LLM → resolve.

    ``processes`` is only passed when the user has enabled process-data access;
    it lets the classifier resolve a named process and the resolver build a
    cross-process module link. Never raises on provider failure - navigation is
    additive, so on any error we degrade to a plain chat result.
    """
    message = message.strip()
    if not message:
        return RoutingResult(intent="chat", confidence=1.0, targets=[])

    # Don't suggest jumping to the page the user is already on. Compare the
    # resolved href against the current path so a cross-process target (same
    # module, different process) is kept.
    cur_path = (current_path or "").rstrip("/")

    def _strip_current(targets: list[NavTarget]) -> list[NavTarget]:
        return [t for t in targets if not cur_path or t.href.rstrip("/") != cur_path]

    pf = prefilter(message, destinations)

    # Fast path: an explicit navigation verb + exactly one matching destination
    # is unambiguous, so we navigate without paying for the LLM. (No named-process
    # resolution here - that needs the classifier; the fast path uses the current
    # process context only.)
    if pf.has_cue and len(pf.matches) == 1:
        targets = _strip_current(
            resolve_targets("navigate", pf.matches, PREFILTER_CONFIDENCE, destinations, log_id)
        )
        if targets:
            return RoutingResult(
                intent="navigate", confidence=PREFILTER_CONFIDENCE, targets=targets
            )

    # Otherwise always ask the classifier. Keyword absence is an unreliable
    # signal of "pure chat" - non-English wording (e.g. "Complexität") or
    # paraphrases would be missed - so we accept one (cheap) classifier call
    # rather than risk a false negative. Provider failure degrades to chat.
    try:
        raw = await classify_intent(
            cfg, message=message, destinations=destinations, processes=processes
        )
    except Exception as exc:
        # Navigation is additive - a failed classifier must never break chat.
        log.info("ai.route.classify_failed", error=str(exc))
        return RoutingResult(intent="chat", confidence=0.0, targets=[])

    targets = _strip_current(
        resolve_targets(
            raw["intent"],
            raw["targets"],
            raw["confidence"],
            destinations,
            log_id,
            processes=processes,
            process_hint=raw.get("process"),
        )
    )
    # A settings change the user explicitly asked for, validated against the
    # whitelist (None → no action chip). Independent of navigation intent.
    action = resolve_action(raw.get("action"))
    actions = [action] if action is not None else []
    # Diagnostic: shows exactly what the classifier returned vs what resolved,
    # so a "no chip appeared" report can be traced to intent/target/process.
    log.info(
        "ai.route.classified",
        intent=raw["intent"],
        targets=raw["targets"],
        process_hint=raw.get("process"),
        action=raw.get("action"),
        confidence=raw["confidence"],
        resolved=[t.href for t in targets],
        resolved_actions=[a.setting for a in actions],
        n_processes=len(processes or []),
        dest_ids=[d.id for d in destinations],
    )
    return RoutingResult(
        intent=raw["intent"], confidence=raw["confidence"], targets=targets, actions=actions
    )
