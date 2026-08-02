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
