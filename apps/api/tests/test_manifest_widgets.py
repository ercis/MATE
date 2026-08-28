"""Widget manifest contract - the fields dashboard cards are declared with.

`manifest.yaml` is a public authoring format: third-party modules ship their
own. So the load path has to stay backward compatible in both directions - an
old manifest keeps loading against the new SDK, and fields the SDK has since
dropped must be ignored rather than fatal.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from mate.sdk.manifest import GRID_COLS, Manifest, WidgetEntry, WidgetHelp


def _write(tmp_path: Path, body: str) -> Path:
    # Dedent the body on its own: dedenting header+body together would compare
    # their (different) indentation and strip the wrong common prefix.
    path = tmp_path / "manifest.yaml"
    header = "id: demo\nname: Demo\nversion: 0.1.0\ncategory: foundation\nentrypoint: module.py\n"
    path.write_text(header + textwrap.dedent(body))
    return path


# ── backward compatibility ───────────────────────────────────────────────────


def test_pre_rework_widget_still_loads(tmp_path: Path) -> None:
    """A manifest written before help/kpis/views/drill/min_px existed."""
    path = _write(
        tmp_path,
        """\
        frontend:
          panel: ./panel/index.tsx
          widgets:
            - id: old-card
              entry: ./widgets/Old.tsx
              title: Old card
              description: One-line blurb.
              default_w: 6
              default_h: 8
              min_w: 4
              min_h: 5
        """,
    )
    widget = Manifest.load_yaml(path).frontend.widgets[0]
    assert widget.id == "old-card"
    assert (widget.min_w, widget.min_h) == (4, 5)
    # Everything new is absent, not defaulted into something surprising.
    assert widget.help is None
    assert widget.drill is None
    assert widget.settings_entry is None
    assert widget.views == []
    assert widget.kpis == []
    assert (widget.min_px_w, widget.min_px_h) == (0, 0)


def test_removed_frontend_fields_are_ignored_not_fatal(tmp_path: Path) -> None:
    """`page_layout` and `side_rail` were deleted; manifests still declare them.

    Two bundled modules did at the time of removal, and third-party ones may
    forever. Loading must not fail.
    """
    path = _write(
        tmp_path,
        """\
        frontend:
          panel: ./panel/index.tsx
          side_rail: ./panel/rail.tsx
          widgets:
            - id: a
              entry: ./widgets/A.tsx
          page_layout:
            - section: Fidelity
              widgets: [a]
        """,
    )
    frontend = Manifest.load_yaml(path).frontend
    assert frontend.panel == "./panel/index.tsx"
    assert [w.id for w in frontend.widgets] == ["a"]
    assert not hasattr(frontend, "page_layout")
    assert not hasattr(frontend, "side_rail")


# ── sizing ───────────────────────────────────────────────────────────────────


def test_pixel_floor_raises_the_default_height() -> None:
    """A widget declaring only `min_px_h` must not drop shorter than its floor.

    Rows are 18px with an 8px gap, so a 220px floor needs 9 rows - more than
    the declared default of 4, which would otherwise be bounced up on drop.
    """
    widget = WidgetEntry(id="a", entry="./A.tsx", default_h=4, min_px_h=220)
    assert widget.default_h == 9


def test_pixel_floor_does_not_shrink_a_taller_default() -> None:
    widget = WidgetEntry(id="a", entry="./A.tsx", default_h=20, min_px_h=100)
    assert widget.default_h == 20


def test_default_is_clamped_up_to_grid_minimum() -> None:
    widget = WidgetEntry(id="a", entry="./A.tsx", default_w=2, default_h=2, min_w=5, min_h=6)
    assert (widget.default_w, widget.default_h) == (5, 6)


def test_widths_cannot_exceed_the_grid() -> None:
    widget = WidgetEntry(id="a", entry="./A.tsx", default_w=40, min_w=30)
    assert widget.default_w == GRID_COLS
    assert widget.min_w == GRID_COLS


def test_fixed_size_card_keeps_its_declared_size() -> None:
    """`resizable: false` makes default_w/h authoritative, so no min clamping."""
    widget = WidgetEntry(
        id="a", entry="./A.tsx", resizable=False, default_w=3, default_h=2, min_w=8, min_h=9
    )
    assert (widget.default_w, widget.default_h) == (3, 2)


# ── help / kpis / views / drill ──────────────────────────────────────────────


def test_help_requires_what() -> None:
    """A help popover with no "what" is worse than no help at all."""
    with pytest.raises(ValidationError):
        WidgetHelp.model_validate({"read": "Taller bars are slower."})

    helped = WidgetHelp.model_validate({"what": "Time per activity.", "read": "Taller is slower."})
    assert helped.what == "Time per activity."
    assert helped.computed is None


def test_full_widget_declaration_round_trips(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
        frontend:
          panel: ./panel/index.tsx
          panel_help:
            what: Where the process spends time.
          widgets:
            - id: kpis
              entry: ./widgets/Kpis.tsx
              min_px_w: 320
              min_px_h: 220
              help:
                what: Headline cycle-time figures.
                read: Lower is faster.
                computed: Median over completed cases.
                docs_url: https://example.invalid/perf
              kpis:
                - id: median
                  title: Median cycle time
                  info: Half of cases finish faster than this.
                - id: p95
                  title: P95 cycle time
                  default: false
              views:
                - id: summary
                  title: Summary
                  exposes: [top_n]
              drill:
                module_id: performance
                params: {view: bottlenecks}
              settings_entry: ./widgets/KpisSettings.tsx
        """,
    )
    frontend = Manifest.load_yaml(path).frontend
    assert frontend.panel_help is not None
    assert frontend.panel_help.what == "Where the process spends time."

    widget = frontend.widgets[0]
    assert widget.help is not None and widget.help.docs_url == "https://example.invalid/perf"
    assert [k.id for k in widget.kpis] == ["median", "p95"]
    assert widget.kpis[0].default is True and widget.kpis[1].default is False
    assert widget.views[0].exposes == ["top_n"]
    assert widget.drill is not None
    assert widget.drill.module_id == "performance"
    assert widget.drill.params == {"view": "bottlenecks"}
    assert widget.settings_entry == "./widgets/KpisSettings.tsx"
    # The pixel floor bumped the default height (220px -> 9 rows).
    assert widget.default_h == 9


def test_bundled_manifests_all_load() -> None:
    """Every shipped module must survive the contract change."""
    root = Path(__file__).resolve().parents[3] / "modules"
    manifests = sorted(root.glob("*/manifest.yaml"))
    assert manifests, "no bundled manifests found"
    for path in manifests:
        Manifest.load_yaml(path)  # raises on failure
