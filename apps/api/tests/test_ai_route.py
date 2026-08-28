"""Intent-based navigation routing (``mate.api.ai_nav``).

Pure-logic coverage: keyword pre-filter, target resolution, classifier-output
coercion, and the full ``route_intent`` orchestration with a stubbed LLM.
"""

from __future__ import annotations

import asyncio

from mate.api import ai_nav
from mate.api.ai_config import AiConfigPayload
from mate.api.ai_nav import (
    ROUTING_SCHEMA,
    NavDestination,
    ProcessInfo,
    _coerce_routing,
    _derive_keywords,
    build_destination_catalog,
    current_destination,
    match_process,
    module_destination,
    module_routing_text,
    prefilter,
    resolve_action,
    resolve_targets,
    route_intent,
)

PROCS = [
    ProcessInfo(id="logA", name="Order Process", cases_count=1200, variants_count=42),
    ProcessInfo(id="logB", name="Invoice Flow", cases_count=300, variants_count=18),
]

# A compact, deterministic registry for the unit tests.
PERF = NavDestination(
    id="performance",
    label="Performance",
    kind="module",
    href_template="/processes/{log_id}/modules/performance",
    requires_log=True,
    keywords=["performance", "bottleneck", "cycle time"],
    description="Throughput and bottlenecks.",
)
SETTINGS_AI = NavDestination(
    id="settings.ai",
    label="AI settings",
    kind="page",
    href_template="/settings/ai",
    requires_log=False,
    keywords=["ai settings", "api key", "provider"],
    description="Configure the AI provider.",
)
DESTS = [PERF, SETTINGS_AI]


def _cfg() -> AiConfigPayload:
    return AiConfigPayload(
        selected_provider="openai",
        selected_model="gpt-x",
        openai={"api_key": "sk-test"},
    )


# ── pre-filter ───────────────────────────────────────────────────────────────


def test_prefilter_pure_chat_has_no_cue_and_no_match() -> None:
    pf = prefilter("tell me a joke about cats", DESTS)
    assert pf.has_cue is False
    assert pf.matches == []


def test_prefilter_detects_navigation_verb_and_single_match() -> None:
    pf = prefilter("open the performance module", DESTS)
    assert pf.has_cue is True
    assert pf.matches == ["performance"]


def test_prefilter_matches_multiword_keyword_as_substring() -> None:
    pf = prefilter("where can i set my api key", DESTS)
    assert pf.has_cue is True  # "where can i"
    assert "settings.ai" in pf.matches


def test_prefilter_keyword_without_cue_still_matches_but_no_cue() -> None:
    # A bare mention is a match but not an explicit navigation request.
    pf = prefilter("the cycle time looks high", DESTS)
    assert pf.has_cue is False
    assert pf.matches == ["performance"]


# ── resolution ───────────────────────────────────────────────────────────────


def test_resolve_module_panel_with_log_context() -> None:
    out = resolve_targets("navigate", ["performance"], 0.9, DESTS, log_id="L1")
    assert len(out) == 1
    assert out[0].href == "/processes/L1/modules/performance"
    assert out[0].available is True
    assert out[0].requires_log is False


def test_resolve_module_panel_without_log_falls_back_to_config() -> None:
    out = resolve_targets("navigate", ["performance"], 0.9, DESTS, log_id=None)
    assert out[0].href == "/modules/performance"
    assert out[0].available is False
    assert out[0].requires_log is True


def test_resolve_page_ignores_log() -> None:
    out = resolve_targets("navigate", ["settings.ai"], 0.9, DESTS, log_id=None)
    assert out[0].href == "/settings/ai"
    assert out[0].available is True


def test_resolve_drops_below_confidence_threshold() -> None:
    assert resolve_targets("navigate", ["performance"], 0.5, DESTS, log_id="L1") == []


def test_resolve_chat_intent_yields_nothing() -> None:
    assert resolve_targets("chat", ["performance"], 0.99, DESTS, log_id="L1") == []


def test_resolve_unknown_id_is_skipped() -> None:
    assert resolve_targets("navigate", ["does_not_exist"], 0.9, DESTS, log_id="L1") == []


# ── classifier-output coercion ───────────────────────────────────────────────


def test_coerce_filters_to_valid_ids_and_clamps_confidence() -> None:
    out = _coerce_routing(
        {"intent": "navigate", "targets": ["performance", "ghost"], "confidence": 1.7},
        {"performance", "settings.ai"},
    )
    assert out == {
        "intent": "navigate",
        "targets": ["performance"],
        "process": None,
        "action": None,
        "confidence": 1.0,
    }


def test_coerce_defaults_garbage_to_chat() -> None:
    out = _coerce_routing({"intent": "weird", "confidence": "n/a"}, {"performance"})
    assert out == {
        "intent": "chat",
        "targets": [],
        "process": None,
        "action": None,
        "confidence": 0.0,
    }


def test_coerce_extracts_process_hint() -> None:
    out = _coerce_routing(
        {
            "intent": "navigate",
            "targets": ["performance"],
            "process": "Order Process",
            "confidence": 0.9,
        },
        {"performance"},
    )
    assert out["process"] == "Order Process"


# ── keyword derivation ───────────────────────────────────────────────────────


def test_derive_keywords_from_name_and_provides() -> None:
    kws = _derive_keywords("Performance", "Throughput and bottlenecks.", ["perf.kpis"])
    assert "performance" in kws
    assert "kpis" in kws
    assert all(len(k) >= 3 for k in kws)


# ── module routing context (about / description) ─────────────────────────────


def test_module_routing_text_prefers_about() -> None:
    assert module_routing_text("rich about", "terse desc", "Name") == "rich about"
    assert module_routing_text(None, "terse desc", "Name") == "terse desc"
    assert module_routing_text(None, None, "Name") == "Name"


def test_module_destination_uses_about_as_routing_context() -> None:
    # A manifest whose ONLY bottleneck signal lives in `about`, not the terse
    # `description` and with no explicit keywords: the routing context (and the
    # derived pre-filter keywords) must come from `about`.
    dest = module_destination(
        "performance",
        "Performance",
        about="Find out where your process loses time and locate slow bottlenecks.",
        description="Speed metrics.",
        keywords=None,
        provides=["perf.kpis"],
    )
    assert dest.kind == "module"
    assert dest.requires_log is True
    assert dest.href_template == "/processes/{log_id}/modules/performance"
    # `about` (not the terse description) is what the classifier catalogue sees.
    assert "loses time" in dest.description
    # Its distinctive tokens leak into the derived pre-filter keywords.
    assert "bottlenecks" in dest.keywords


def test_module_destination_falls_back_to_description_without_about() -> None:
    dest = module_destination(
        "complexity",
        "Complexity",
        about=None,
        description="Entropy-based complexity metrics over variants.",
        keywords=None,
        provides=[],
    )
    assert dest.description == "Entropy-based complexity metrics over variants."
    assert "entropy" in dest.keywords


def test_module_destination_explicit_keywords_win() -> None:
    dest = module_destination(
        "discovery",
        "Discovery",
        about="Mine your event log into a process map.",
        description="Process model.",
        keywords=["dfg", "petri net", "bpmn"],
        provides=["discovery.dfg"],
    )
    # Author-declared keywords are used verbatim, not derived from about/name.
    assert dest.keywords == ["dfg", "petri net", "bpmn"]
    # `about` still supplies the classifier catalogue description.
    assert "process map" in dest.description


def test_build_destination_catalog_surfaces_about() -> None:
    # The classifier prompt must carry the module's `about` so a stated goal can
    # be matched to the right module by the LLM.
    dest = module_destination(
        "conformance",
        "Conformance Checking",
        about="Check whether reality follows the rules by replaying cases.",
        description="Fitness and precision.",
        keywords=None,
        provides=[],
    )
    catalog = build_destination_catalog([dest])
    assert "reality follows the rules" in catalog


def test_route_intent_routes_stated_goal_to_module_from_about(monkeypatch) -> None:
    # End-to-end: a goal ("find where we lose time") reaches the classifier, which
    # picks the performance module built purely from its `about` text; the target
    # resolves to that module's panel for the current log.
    perf = module_destination(
        "performance",
        "Performance",
        about="Find out where your process loses time and locate bottlenecks.",
        description="Speed metrics.",
        keywords=None,
        provides=["perf.kpis"],
    )

    async def _fake(cfg, *, message, destinations, processes=None):
        # Assert the destination the classifier is handed actually carries `about`.
        assert any("loses time" in d.description for d in destinations)
        return {"intent": "navigate", "targets": ["performance"], "confidence": 0.9}

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(
            _cfg(),
            message="where does my process lose the most time",
            destinations=[perf],
            log_id="L9",
        )
    )
    assert res.intent == "navigate"
    assert res.targets[0].href == "/processes/L9/modules/performance"


# ── full orchestration (LLM stubbed) ─────────────────────────────────────────


def test_route_intent_classifies_when_no_fast_path(monkeypatch) -> None:
    # No nav verb + no keyword match no longer short-circuits to chat: we always
    # consult the classifier so non-English / paraphrased intent isn't missed.
    called = {"n": 0}

    async def _fake(cfg, *, message, destinations, processes=None):
        called["n"] += 1
        return {"intent": "chat", "targets": [], "confidence": 0.1}

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(_cfg(), message="tell me a joke", destinations=DESTS, log_id=None)
    )
    assert called["n"] == 1
    assert res.intent == "chat"
    assert res.targets == []


def test_route_intent_routes_non_english_intent_via_llm(monkeypatch) -> None:
    # The exact regression: a German message mentioning "Complexität" has no
    # English keyword match and no nav verb, so it must reach the classifier.
    called = {"n": 0}

    async def _fake(cfg, *, message, destinations, processes=None):
        called["n"] += 1
        return {"intent": "both", "targets": ["performance"], "confidence": 0.85}

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(
            _cfg(),
            message="Ich möchte mehr über die Complexität erfahren",
            destinations=DESTS,
            log_id="L1",
        )
    )
    assert called["n"] == 1
    assert res.intent == "both"
    assert res.targets[0].id == "performance"


def test_route_intent_skips_llm_for_unambiguous_nav(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("classifier must not be called for unambiguous nav")

    monkeypatch.setattr(ai_nav, "classify_intent", _boom)
    res = asyncio.run(
        route_intent(_cfg(), message="open the performance module", destinations=DESTS, log_id="L1")
    )
    assert res.intent == "navigate"
    assert [t.id for t in res.targets] == ["performance"]
    assert res.targets[0].href == "/processes/L1/modules/performance"


def test_route_intent_calls_llm_for_ambiguous_message(monkeypatch) -> None:
    called = {"n": 0}

    async def _fake(cfg, *, message, destinations, processes=None):
        called["n"] += 1
        return {"intent": "both", "targets": ["performance"], "confidence": 0.9}

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(_cfg(), message="the cycle time looks high", destinations=DESTS, log_id="L1")
    )
    assert called["n"] == 1
    assert res.intent == "both"
    assert res.targets[0].href == "/processes/L1/modules/performance"


# ── current location ─────────────────────────────────────────────────────────


def test_current_destination_matches_page() -> None:
    assert current_destination("/settings/ai", DESTS) is SETTINGS_AI
    assert current_destination("/settings/ai/", DESTS) is SETTINGS_AI


def test_current_destination_matches_module_panel() -> None:
    assert current_destination("/processes/L1/modules/performance", DESTS) is PERF


def test_current_destination_none_for_unrelated_path() -> None:
    assert current_destination("/profile", DESTS) is None
    assert current_destination(None, DESTS) is None


def test_route_intent_drops_target_for_current_page(monkeypatch) -> None:
    async def _fake(cfg, *, message, destinations, processes=None):
        return {"intent": "navigate", "targets": ["settings.ai"], "confidence": 0.95}

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    # Already on /settings/ai → the suggestion to go there is dropped.
    res = asyncio.run(
        route_intent(
            _cfg(),
            message="open the ai settings",
            destinations=DESTS,
            log_id=None,
            current_path="/settings/ai",
        )
    )
    assert res.targets == []
    # From elsewhere the same request keeps the target.
    res2 = asyncio.run(
        route_intent(
            _cfg(),
            message="open the ai settings",
            destinations=DESTS,
            log_id=None,
            current_path="/profile",
        )
    )
    assert [t.id for t in res2.targets] == ["settings.ai"]


# ── settings actions ─────────────────────────────────────────────────────────


def test_resolve_action_enum_bool_tz_delimiter() -> None:
    a = resolve_action({"setting": "theme", "value": "dark"})
    assert a is not None and a.setting == "theme" and a.value == "dark" and a.target == "theme"
    assert "dark" in a.label.lower()

    b = resolve_action({"setting": "notifications_muted", "value": "off"})
    assert b is not None and b.value is False and b.target == "ui"

    c = resolve_action({"setting": "timezone", "value": "Europe/Berlin"})
    assert c is not None and c.value == "Europe/Berlin"

    d = resolve_action({"setting": "csv_delimiter", "value": "semicolon"})
    assert d is not None and d.value == ";"

    e = resolve_action({"setting": "experience_level", "value": "expert"})
    assert e is not None and e.target == "onboarding" and e.value == "expert"


def test_resolve_action_rejects_invalid_values() -> None:
    assert resolve_action({"setting": "theme", "value": "blue"}) is None
    assert resolve_action({"setting": "timezone", "value": "Not/AZone"}) is None
    assert resolve_action({"setting": "csv_delimiter", "value": "weird"}) is None
    assert resolve_action({"setting": "notifications_muted", "value": "maybe"}) is None
    assert resolve_action({"setting": "csv_timestamp_format", "value": "x" * 200}) is None


def test_resolve_action_rejects_blocked_and_malformed_settings() -> None:
    # Sensitive / non-whitelisted settings must never resolve, even if the model emits them.
    for blocked in (
        "allow_process_data",
        "api_key",
        "system_prompt",
        "analytics_enabled",
        "selected_model",
        "password",
        "email",
        "completed",
    ):
        assert resolve_action({"setting": blocked, "value": "true"}) is None
    assert resolve_action(None) is None
    assert resolve_action("nope") is None
    assert resolve_action({"setting": "theme"}) is None  # missing value
    assert resolve_action({"setting": None, "value": "x"}) is None


def test_routing_schema_stays_openai_strict_safe() -> None:
    # OpenAI strict json_schema rejects numeric range/array-length constraints and
    # requires every property to be in `required`. Guard against regressions.
    forbidden = {"minimum", "maximum", "minItems", "exclusiveMinimum", "exclusiveMaximum"}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert forbidden.isdisjoint(node.keys()), f"forbidden key in schema: {node.keys()}"
            if node.get("type") == "object" and "properties" in node:
                assert set(node["properties"]) == set(node.get("required", []))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(ROUTING_SCHEMA)
    assert "action" in ROUTING_SCHEMA["required"]


def test_route_intent_surfaces_resolved_action(monkeypatch) -> None:
    async def _fake(cfg, *, message, destinations, processes=None):
        return {
            "intent": "chat",
            "targets": [],
            "process": None,
            "action": {"setting": "theme", "value": "light"},
            "confidence": 0.2,
        }

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(_cfg(), message="switch to light mode", destinations=DESTS, log_id=None)
    )
    assert [a.setting for a in res.actions] == ["theme"]
    assert res.actions[0].value == "light"


# ── cross-process navigation ─────────────────────────────────────────────────


def test_match_process_by_name_id_and_substring() -> None:
    assert match_process("Order Process", PROCS) is PROCS[0]
    assert match_process("logB", PROCS) is PROCS[1]
    assert match_process("invoice", PROCS) is PROCS[1]  # substring, case-insensitive
    assert match_process("nonexistent xyz", PROCS) is None
    assert match_process(None, PROCS) is None


def test_resolve_cross_process_module_uses_named_process_log() -> None:
    # User is in process L1 but asks for performance of "Invoice Flow".
    out = resolve_targets(
        "navigate",
        ["performance"],
        0.9,
        DESTS,
        log_id="L1",
        processes=PROCS,
        process_hint="Invoice Flow",
    )
    assert out[0].href == "/processes/logB/modules/performance"
    assert out[0].available is True
    assert "Invoice Flow" in out[0].label


def test_resolve_module_without_hint_uses_current_log() -> None:
    out = resolve_targets(
        "navigate", ["performance"], 0.9, DESTS, log_id="L1", processes=PROCS, process_hint=None
    )
    assert out[0].href == "/processes/L1/modules/performance"


def test_route_intent_cross_process_navigation(monkeypatch) -> None:
    async def _fake(cfg, *, message, destinations, processes=None):
        # classifier identifies the module + the named process
        return {
            "intent": "navigate",
            "targets": ["performance"],
            "process": "Invoice Flow",
            "confidence": 0.92,
        }

    monkeypatch.setattr(ai_nav, "classify_intent", _fake)
    res = asyncio.run(
        route_intent(
            _cfg(),
            message="show me the performance of the invoice flow",
            destinations=DESTS,
            log_id=None,
            current_path="/dashboards",
            processes=PROCS,
        )
    )
    assert res.targets[0].href == "/processes/logB/modules/performance"


def test_route_intent_swallows_classifier_errors(monkeypatch) -> None:
    async def _fail(cfg, *, message, destinations, processes=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_nav, "classify_intent", _fail)
    res = asyncio.run(
        route_intent(_cfg(), message="the cycle time looks high", destinations=DESTS, log_id="L1")
    )
    assert res.intent == "chat"
    assert res.targets == []
