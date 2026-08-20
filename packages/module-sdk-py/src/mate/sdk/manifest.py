"""Pydantic schema for `manifest.yaml` (INSTRUCTIONS.md §5.1).

Validated by the SDK so module authors can sanity-check their manifest
locally, and by the platform loader at startup. The loader rejects manifests
with hard-dep cycles, missing required fields, or `inherit:`/`packages:`
overlap (§5.4 inherit-conflict rule).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mate.sdk.errors import ModuleManifestError

ModuleCategory = Literal[
    "foundation", "attribute", "external_input", "advanced", "comparison", "other"
]
IsolationMode = Literal["in_process", "subprocess"]


class EventLogRequirements(BaseModel):
    # Which log model this module operates on. The platform makes a module
    # available only on logs of the matching model - case-centric and
    # object-centric (OCEL) modules never run against each other's logs.
    # Defaults to "case_centric" so every existing module stays case-centric.
    log_model: Literal["case_centric", "object_centric"] = "case_centric"
    required_columns: list[str] = Field(default_factory=list)
    optional_columns: list[str] = Field(default_factory=list)
    min_events: int | None = None
    min_cases: int | None = None


class OptionalModuleDep(BaseModel):
    id: str
    reason: str | None = None


class Requirements(BaseModel):
    event_log: EventLogRequirements = Field(default_factory=EventLogRequirements)
    modules: list[str] = Field(default_factory=list)
    optional_modules: list[OptionalModuleDep] = Field(default_factory=list)


class DependenciesPython(BaseModel):
    requires_python: str | None = Field(default=None, alias="requires-python")
    packages: list[str] = Field(default_factory=list)
    inherit: list[str] = Field(default_factory=list)
    isolation: IsolationMode = "in_process"

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _no_inherit_conflict(self) -> Self:
        # `pandas` cannot appear in both `packages` and `inherit`.
        pkg_names = {p.split(">=", 1)[0].split("==", 1)[0].split("<", 1)[0].split("~", 1)[0].strip().lower() for p in self.packages}
        for name in self.inherit:
            if name.lower() in pkg_names:
                raise ModuleManifestError(
                    f"`{name}` is in both dependencies.python.inherit and dependencies.python.packages - "
                    "pick one. Inherit reuses the platform's version; packages installs a private copy."
                )
        return self


class Dependencies(BaseModel):
    python: DependenciesPython = Field(default_factory=DependenciesPython)
    npm: list[str] = Field(default_factory=list)


class WidgetEntry(BaseModel):
    """A reusable frontend widget ("card") a module exposes.

    Beyond the `id`/`entry` the bundler needs, the optional display fields are
    surfaced by the platform's card catalog (`GET /api/v1/modules/cards`) so
    the Dashboards palette can list every module's cards without loading their
    bundles. `default_w`/`default_h` are react-grid-layout cells (12-col grid).
    """

    id: str
    entry: str
    title: str | None = None
    description: str | None = None
    # Lucide icon name (e.g. "Activity"); the frontend maps it to a glyph and
    # falls back to a generic chart icon when unknown or absent.
    icon: str | None = None
    default_w: int = 6
    default_h: int = 8
    # Whether the user may resize the card on a dashboard.
    #   resizable: true  -> card can be resized; `min_w`/`min_h` are the floor and
    #                       `default_w`/`default_h` the initial (>= min) drop size.
    #   resizable: false -> card is a FIXED size: it can be moved but not resized,
    #                       and `default_w`/`default_h` ARE that fixed size.
    # Either way the relevant size (the minimum, or the fixed size) must be large
    # enough to show all of the widget's information.
    resizable: bool = True
    # Smallest size (react-grid-layout cells) a *resizable* card may be shrunk to.
    # The Dashboards canvas feeds these to the grid item's `minW`/`minH` and also
    # grows an under-sized placed card up to them on load. Ignored when
    # `resizable` is false (the card is locked to `default_w`/`default_h`).
    min_w: int = 2
    min_h: int = 3
    # Optional per-card settings, declared in the same JSON-Schema-flavoured
    # dialect as a module's top-level `config_schema` (`{properties: {...}}`
    # with `type`/`title`/`enum`/`minimum`/`ui.widget` ...). The Dashboards
    # palette surfaces it (`/modules/cards`) and renders a settings form per
    # placed card in edit mode; the chosen values land in the placement's
    # `config` and are passed to the widget as its `config` prop.
    config_schema: dict[str, Any] | None = None
    # Which log data model(s) this card applies to. A dashboard is created for
    # one model (case-centric vs object-centric/OCEL) and its palette only
    # offers cards whose `log_models` include the board's model. Defaults to
    # case-centric so every existing widget keeps working unchanged.
    log_models: list[Literal["case_centric", "object_centric"]] = Field(
        default_factory=lambda: ["case_centric"]
    )

    @model_validator(mode="after")
    def _clamp_defaults_to_min(self) -> Self:
        # For a resizable card the initial drop size must never be below its own
        # minimum, or RGL would immediately bounce it up. For a fixed card
        # (`resizable=false`) `default_w/_h` is the authoritative size, so leave
        # it untouched.
        if self.resizable:
            self.default_w = max(self.default_w, self.min_w)
            self.default_h = max(self.default_h, self.min_h)
        return self


class PageLayoutSection(BaseModel):
    section: str
    widgets: list[str] = Field(default_factory=list)


class ManifestFrontend(BaseModel):
    panel: str | None = None
    side_rail: str | None = None
    widgets: list[WidgetEntry] = Field(default_factory=list)
    page_layout: list[PageLayoutSection] = Field(default_factory=list)


class AiModelSlot(BaseModel):
    """One labelled (provider, model) selector exposed on the module's
    settings page. The actual API keys come from the platform's global
    Settings → AI; the module only persists the user's chosen pair."""

    title: str
    description: str | None = None


class AiModelsManifest(BaseModel):
    """Declares the AI-model selectors a module needs on its settings page.

    Typical usage is ``llm`` (for chat agents) + ``embedding`` (for retrieval),
    but any string-keyed slot is accepted so a module could declare extra
    roles (e.g. a separate vision model).
    """

    model_config = ConfigDict(extra="allow")

    # When true, the module manages its **own** OpenAI API key (persisted under
    # ``module_configs.config_json["ai"]``) and never reads the platform's
    # global Settings → AI. The settings page renders the module's isolated
    # OpenAI card (key + Check + model pickers) instead of the platform-keyed
    # provider/model selectors.
    self_hosted: bool = False

    llm: AiModelSlot | None = None
    embedding: AiModelSlot | None = None


class ModelStoreManifest(BaseModel):
    """Declares that a module accepts large pretrained-model uploads.

    When present, the module's settings page renders a generic "Model files"
    card: users upload an archive (e.g. ``.tar.zst``) that the module extracts
    into platform-shared storage, then pick which uploaded model this account
    uses. The chosen folder name is persisted under ``config_json[config_key]``.

    The actual upload / list / delete is served by the module's own routes
    (``GET``/``POST``/``DELETE`` ``/models``); this block only opts the card in
    and supplies its copy.
    """

    title: str = "Model files"
    description: str | None = None
    # Accepted upload extension(s), passed to the file picker's `accept` attr
    # (e.g. ".tar.zst"). Cosmetic - server-side validation is the route's job.
    accept: str = ".tar.zst"
    # Where the selected model's folder name is stored in the module config.
    config_key: str = "model"


class Manifest(BaseModel):
    """The top-level manifest object - `manifest.yaml`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    version: str
    category: ModuleCategory
    description: str | None = None
    author: str | None = None
    license: str | None = None

    requirements: Requirements = Field(default_factory=Requirements)
    provides: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)
    # Free-form hints (verbs, synonyms, domain terms) that help MATE AI's intent
    # classifier route a user's chat message to this module. Optional - when
    # omitted the platform derives keywords from the name/description/provides.
    keywords: list[str] = Field(default_factory=list)
    dependencies: Dependencies = Field(default_factory=Dependencies)
    frontend: ManifestFrontend = Field(default_factory=ManifestFrontend)
    permissions: list[str] = Field(default_factory=list)
    default_enabled: bool = True
    # Whether the module is safe to run against confidential data - i.e. it
    # processes the event log entirely locally and never ships data to an
    # external service. When the user enables "Show only confidential modules"
    # in platform settings, modules with this set to `false` are hidden.
    # Defaults to `false` so a module is only treated as safe when it
    # explicitly opts in.
    is_confidential_safe: bool = Field(default=False, alias="isConfidentialSafe")
    # JSON-Schema-flavoured dict so module authors write it in YAML. The
    # platform passes it through to the frontend as-is (`/config-schema`);
    # form-rendering and validation are the frontend's responsibility.
    config_schema: dict[str, Any] | None = None
    # Optional declaration of AI-model selectors. When present, the module's
    # settings page renders an "AI models" card and the chosen (provider,
    # model) pairs are persisted under ``module_configs.config_json["ai"]``.
    ai_models: AiModelsManifest | None = None
    # Optional declaration that the module accepts large pretrained-model
    # uploads. When present, the settings page renders a "Model files" card and
    # the selected model's folder name is persisted under
    # ``module_configs.config_json[model_store.config_key]``.
    model_store: ModelStoreManifest | None = None

    @model_validator(mode="after")
    def _validate_id(self) -> Self:
        if not self.id.replace("_", "").isalnum() or not self.id.islower():
            raise ModuleManifestError(
                f"Manifest id {self.id!r} must be lowercase snake_case (letters, digits, underscores)."
            )
        return self

    @classmethod
    def load_yaml(cls, path: Path | str) -> Manifest:
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ModuleManifestError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ModuleManifestError(f"Manifest at {path} is not a YAML mapping.")
        try:
            return cls.model_validate(data)
        except Exception as exc:
            raise ModuleManifestError(f"Manifest validation failed for {path}: {exc}") from exc

    def dependencies_hash(self) -> str:
        """Stable hash of the dependencies block - used to skip `uv sync` on
        unchanged boots (§5.4)."""
        import hashlib
        import json

        payload = json.dumps(self.dependencies.model_dump(by_alias=True), sort_keys=True)
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
