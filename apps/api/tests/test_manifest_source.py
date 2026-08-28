"""Cited works (`source`) in a module manifest, and the removed author fields.

A manifest cites up to 20 works under `source`, each `{title, fullCitation,
url?}`. There are no author fields at all - `fullCitation` carries the author
names - so the removed `author`/`author_url`/`authors`/`paper_url`/`papers` keys
are rejected loudly instead of being silently dropped by `extra="ignore"`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import MAX_SOURCES, Manifest

_MODULES_DIR = Path(__file__).resolve().parents[3] / "modules"

# House style: IEEE, DOI omitted (it lives in `url`), no final period.
_CITATION = (
    'C. Pitsch et al., "Hypothesis Testing for Processes," in 2025 7th International '
    "Conference on Process Mining (ICPM), Montevideo, Uruguay, 2025, pp. 1-8"
)


def _base(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "category": "advanced",
    }
    data.update(overrides)
    return data


# ── shape ───────────────────────────────────────────────────────────────────


def test_source_defaults_to_an_empty_list() -> None:
    # Citing anything is optional; a module with no source renders no credits.
    assert Manifest.model_validate(_base()).source == []


def test_source_entries_are_parsed_in_declaration_order() -> None:
    m = Manifest.model_validate(
        _base(
            source=[
                {
                    "title": "Hypothesis Testing for Processes",
                    "fullCitation": _CITATION,
                    "url": "https://doi.org/10.1109/ICPM66919.2025.11220677",
                },
                {"title": "Untitled follow-up", "fullCitation": "N. N., 2026."},
            ]
        )
    )
    assert [(s.title, s.url) for s in m.source] == [
        ("Hypothesis Testing for Processes", "https://doi.org/10.1109/ICPM66919.2025.11220677"),
        ("Untitled follow-up", None),
    ]
    assert m.source[0].full_citation == _CITATION


def test_full_citation_accepts_the_snake_case_spelling_too() -> None:
    # `populate_by_name` - YAML uses `fullCitation`, Python code may use the
    # field name when constructing a Manifest programmatically.
    m = Manifest.model_validate(
        _base(source=[{"title": "T", "full_citation": _CITATION}]),
    )
    assert m.source[0].full_citation == _CITATION


def test_source_serialises_full_citation_as_camel_case() -> None:
    # The manifest route dumps `by_alias=True`, so the web app sees
    # `fullCitation`; keep that the wire spelling.
    m = Manifest.model_validate(_base(source=[{"title": "T", "fullCitation": _CITATION}]))
    assert m.model_dump(by_alias=True)["source"] == [
        {"title": "T", "fullCitation": _CITATION, "url": None}
    ]


def test_source_requires_a_title() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(_base(source=[{"fullCitation": _CITATION}]))


def test_source_requires_a_full_citation() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(_base(source=[{"title": "No citation"}]))


# ── max-20 cap ──────────────────────────────────────────────────────────────


def test_exactly_20_sources_is_allowed() -> None:
    m = Manifest.model_validate(
        _base(source=[{"title": f"T{i}", "fullCitation": f"C{i}"} for i in range(MAX_SOURCES)])
    )
    assert len(m.source) == MAX_SOURCES == 20


def test_more_than_20_sources_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(
            _base(source=[{"title": f"T{i}", "fullCitation": f"C{i}"} for i in range(21)])
        )


# ── removed author/paper fields fail loud ───────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("author", "Jane Doe"),
        ("author_url", "https://github.com/janedoe"),
        ("authors", [{"name": "Jane Doe"}]),
        ("paper_url", "https://doi.org/10.1000/xyz"),
        ("papers", [{"title": "P", "url": "https://doi.org/10.1000/xyz"}]),
    ],
)
def test_removed_credit_fields_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ModuleManifestError) as excinfo:
        Manifest.model_validate(_base(**{field: value}))
    # The error names the offending key and points at the replacement.
    assert field in str(excinfo.value)
    assert "source" in str(excinfo.value)


def test_all_removed_fields_are_reported_at_once() -> None:
    with pytest.raises(ModuleManifestError) as excinfo:
        Manifest.model_validate(_base(author="Jane Doe", papers=[{"url": "https://x"}]))
    message = str(excinfo.value)
    assert "'author'" in message
    assert "'papers'" in message


# ── bundled manifests ───────────────────────────────────────────────────────


def test_bundled_pcomp_manifest_cites_both_works() -> None:
    m = Manifest.load_yaml(_MODULES_DIR / "pcomp" / "manifest.yaml")
    assert [s.title for s in m.source] == [
        "Hypothesis testing for processes",
        "PM4Py: A process mining library for Python",
    ]
    assert m.source[0].full_citation.startswith("C. Pitsch, T. Brockhoff, J. N. Adams")
    assert m.source[1].url == "https://doi.org/10.1016/j.simpa.2023.100556"


def test_bundled_discovery_manifest_cites_the_alpha_miner_paper() -> None:
    m = Manifest.load_yaml(_MODULES_DIR / "discovery" / "manifest.yaml")
    assert m.source[0].url == "https://doi.org/10.1109/TKDE.2004.47"
    assert "van der Aalst" in m.source[0].full_citation


def test_bundled_citations_follow_the_house_style() -> None:
    """IEEE, DOI in `url` only, no final period - see `modules/README.md` §3.

    Mechanical guard: the About box shows citations from different manifests
    side by side, so a single manifest drifting to another style is visible.
    """
    offenders: list[str] = []
    for path in sorted(_MODULES_DIR.glob("*/manifest.yaml")):
        manifest = Manifest.load_yaml(path)
        for entry in manifest.source:
            citation = entry.full_citation
            if "doi:" in citation.lower():
                offenders.append(f"{manifest.id}: DOI belongs in `url` - {citation}")
            if citation.rstrip().endswith("."):
                offenders.append(f"{manifest.id}: drop the final period - {citation}")
    assert not offenders, "\n".join(offenders)
