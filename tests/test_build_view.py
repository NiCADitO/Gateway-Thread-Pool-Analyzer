"""The one safety property of the Designer-original archive.

`build_view.py` is host tooling, not gateway code, so most of it is only
exercised by running it. This covers the single decision that is expensive to
get wrong: whether a view is still the Designer's, and therefore safe to
archive as the known-good shape CLAUDE.md #5 depends on.

The failure it guards is silent. The old code archived `view.json.orig` beside
the view, inside a directory the Designer owns and clears on save, with a
"create only if missing" guard -- so once the Designer deleted the backup, the
next run copied the already-normalised file into its place and the "original"
became a copy of the script's own output. Nothing errors; the fallback is just
quietly gone.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import build_view  # noqa: E402

ORIGINALS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ignition-project", "designer-originals")


def normalised_view():
    """A view carrying the nodes build_view creates."""
    return {"root": {"type": "ia.container.coord", "children": [
        {"type": "ia.display.label", "meta": {"name": "cap_h_pools"}},
        {"type": "ia.chart.powerchart", "meta": {"name": "PowerChart"}},
        {"type": "ia.chart.powerchart",
         "meta": {"name": build_view.BLOCKED_CHART_NAME}},
    ]}}


def designer_view():
    """What the Designer writes: a chart in a container, nothing else."""
    return {"root": {"type": "ia.container.coord", "children": [
        {"type": "ia.chart.powerchart", "meta": {"name": "PowerChart"}},
    ]}}


def test_a_designer_view_is_pristine():
    assert build_view.is_pristine(designer_view())


def test_a_normalised_view_is_not_pristine():
    """The important direction. A false positive here destroys the archive."""
    assert not build_view.is_pristine(normalised_view())


def test_each_generated_node_kind_defeats_pristine():
    """Every marker strip_generated() removes must also block archiving.

    If the two ever disagree, a view could be stripped-and-rebuilt yet still
    look Designer-made on the next run.
    """
    for name in ("cap_tile_TOTAL THREADS", "cap_h_blocked", "cap_sw_webserver",
                 "val_anything", build_view.BLOCKED_CHART_NAME):
        view = {"root": {"children": [{"type": "ia.display.label",
                                       "meta": {"name": name}}]}}
        assert not build_view.is_pristine(view), name


def test_pristine_survives_a_view_with_no_children():
    """An empty root must not crash the check."""
    assert build_view.is_pristine({"root": {}})
    assert build_view.is_pristine({"root": {"children": None}})


def layout():
    """The vertical stack, recomputed exactly as build() computes it."""
    tiles_bottom = build_view.TILE_TOP + build_view.TILE_HEIGHT
    table_head_y = tiles_bottom + 18
    table_y = table_head_y + 34
    table_bottom = table_y + build_view.table_height()
    head2_y = table_bottom + 20
    blocked_y = head2_y + 38
    head1_y = blocked_y + build_view.BLOCKED_HEIGHT + 20
    chart_y = head1_y + 38
    return {"table_y": table_y, "table_bottom": table_bottom,
            "blocked_y": blocked_y, "chart_y": chart_y}


FOLD = 720  # the shortest viewport this is expected to be read on


def test_the_state_table_fits_entirely_above_the_fold():
    """The "which pool is wrong right now" answer must not need a scroll.

    The table used to start at y=850 and run to 1282, so on a 1280x720
    laptop it was invisible until you scrolled past both charts. It is the
    first question anyone asks, and it was the last thing on the page.
    """
    lay = layout()
    assert lay["table_bottom"] <= FOLD, (
        "state table runs to y=%d, past the %dpx fold"
        % (lay["table_bottom"], FOLD))


def test_current_state_comes_before_history():
    """Now, then when-did-it-start. Ordering is the whole point of stage 2."""
    lay = layout()
    assert lay["table_y"] < lay["blocked_y"] < lay["chart_y"]


def test_the_status_card_fits_the_gap_beside_the_tiles():
    """It occupies dead space; if the tile row grows it must not overlap."""
    x = build_view.TILE_LEFT + len(build_view.TILES) * (
        build_view.TILE_WIDTH + build_view.TILE_GAP)
    width = build_view.CONTENT_WIDTH + build_view.TILE_LEFT - x
    assert width >= 200, "no room left for the status card: %d px" % (width,)


def test_the_committed_archives_are_actually_pristine():
    """Whatever is in designer-originals/ must be a Designer shape.

    This is the regression test for the bug itself: if a normalised view ever
    gets committed as an "original", this fails rather than sitting there
    looking authoritative.
    """
    if not os.path.isdir(ORIGINALS):
        return
    found = [f for f in os.listdir(ORIGINALS) if f.endswith(".json")]
    for name in found:
        with open(os.path.join(ORIGINALS, name), encoding="utf-8") as handle:
            view = json.load(handle)
        assert build_view.is_pristine(view), name
