"""Normalise a Designer-made Perspective view into the dashboard layout.

CLAUDE.md #5 says never invent an Ignition file format -- build one by hand in
the Designer and have code EDIT that known-good shape. That is exactly what
this does. It reads a view.json the Designer wrote, rewrites the parts whose
shape is already present in the file, and writes it back. It never introduces
a property whose schema has not been observed.

WHAT IT CHANGES, and why each one is safe:

  pens[].data.source          string, already present. Only the /drv: segment
                              and the tag path change.
  pens[].data.aggregateMode   "MinMax". Verified value: the AggregateMode enum
                              in the module's own JS bundle is
                              Default|Average|MinMax|LastValue|SimpleAverage|
                              Sum|Minimum|Maximum|DurationOn|DurationOff...
  pens[].display.interpolation "curveStepAfter". Verified from the same bundle:
                              curveLinear|curveStep|curveStepAfter|
                              curveStepBefore|curveBasis|curveCardinal*|
                              curveMonotoneX|curveMonotoneY|curveNatural.
  pens[].display.styles.*     colours, already present at every style key.
  pens[].name                 string, already present.
  table props.data            typed ["array","dataset"] by the component's
                              own schema in ia.components.json, read out of
                              perspective-common on BOTH gateways. Bound to a
                              DataSet tag using the same binding shape the
                              Designer wrote for the labels.

WHAT IT DOES NOT DO: add `axes` or `plots` arrays. Those are real sibling
props -- the bundle lists Axes|Pens|Plots|Columns as settings categories --
but neither appears in a Designer-saved view until configured, so their object
shape is unobserved here. Rather than reconstruct it from the Designer's UI
code, the second panel is made by CLONING the existing power-chart node and
giving it different pens and a different position. Both `position` and `pens`
are keys the file already carries, so nothing is guessed.

Consequence worth knowing: Blocked gets its own CHART rather than its own
axis on the same chart. Visually that is the same outcome -- and arguably
better, since a separate panel with a fixed small height makes a spike from 0
to 2 unmissable instead of a wobble near the baseline.

Usage:
    python scripts/build_view.py --container 81-GW1-1 --view GwThreadingTrends \\
        --provider PostgresDBConnection --drv gw1
    python scripts/build_view.py --container 81-GW2-1 --view GWThreadTrends \\
        --provider PostgreSQLHistorian --drv gw2 --restart

Runs on CPython 3 on the host -- tooling, not gateway code.
"""
import argparse
import copy
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from thread_monitor import tagpaths, taxonomy  # noqa: E402 -- after sys.path

PROJECT_DIR = ("/usr/local/bin/ignition/data/projects/%s/"
               "com.inductiveautomation.perspective/views")

POWERCHART = "ia.chart.powerchart"

# Six pools, not twelve. A legend with twelve entries is a reading exercise;
# the per-pool table answers "which pool" far better than a twelfth colour.
#
# webserver is the hero: it is the series that correlates with a human saying
# the gateway feels slow. Everything else is deliberately lower-contrast so
# the eye lands on webserver first. Colours are muted rather than saturated
# because saturation here is reserved -- see BLOCKED_PANEL.
SERIES = [
    ("webserver", "#5B9BD5"),
    ("executor", "#C08457"),
    ("opcua", "#6A9955"),
    ("scheduler", "#9E7FB8"),
    ("history", "#C9A227"),
    ("platform", "#7F8894"),
]

TAG_ROOT = "gatewayhealth/threads"

# Verified against the AggregateMode enum in the Perspective module's own JS.
#
# MinMax, not Average. Ignition down-samples on a wide range, and averaging is
# exactly wrong for this data: a 60-second webserver spike averaged into an
# hour bucket vanishes. MinMax keeps the envelope, so a spike survives zooming
# out to a week -- which is the whole point of historizing it.
AGGREGATE_MODE = "MinMax"

# Verified against the same bundle's curve list.
#
# The history is stored OnChange with a 5-minute floor, so two adjacent points
# can be minutes apart. curveLinear draws a diagonal between them, inventing
# values that never existed and turning a step change into a gentle slope.
# StepAfter holds the value until the next sample, which is what actually
# happened.
INTERPOLATION = "curveStepAfter"


def source_for(provider, drv, tag):
    """A tag-history pen source.

    Format taken verbatim from the Designer-written pens already in the file:
        histprov:<provider>:/drv:<gatewayName>:<tagProvider>:/tag:<path>
    """
    return "histprov:%s:/drv:%s:default:/tag:%s" % (provider, drv, tag)


def restyle(pen, color):
    """Recolour every style variant a pen carries.

    Perspective writes normal/highlighted/muted/selected, each with its own
    fill and stroke and its own opacity. Setting only `normal` leaves a pen
    that changes colour when you hover it.
    """
    styles = pen["display"]["styles"]
    for state in styles:
        for part in ("fill", "stroke"):
            if part in styles[state]:
                styles[state][part]["color"] = color
    return pen


def make_pen(template, name, tag, color, provider, drv):
    pen = copy.deepcopy(template)
    pen["name"] = name
    pen["data"]["source"] = source_for(provider, drv, tag)
    pen["data"]["aggregateMode"] = AGGREGATE_MODE
    pen["display"]["interpolation"] = INTERPOLATION
    pen["axis"] = ""
    pen["plot"] = 0
    pen["enabled"] = True
    pen["visible"] = True
    restyle(pen, color)
    return pen


def find_chart(node):
    if node.get("type") == POWERCHART:
        return node
    for child in node.get("children") or []:
        found = find_chart(child)
        if found is not None:
            return found
    return None


BLOCKED_CHART_NAME = "blockedChart"
TABLE = "ia.display.table"

# FIXED heights, never derived from the chart's current size.
#
# Deriving was a real bug: the code read the existing height, subtracted the
# blocked panel, and wrote the remainder back -- so every run shrank the chart
# again. 592 -> 410 -> 220 across three runs, until the plot area collapsed to
# nothing and only the pen table was left. The file looked fine each time; the
# only symptom was a screenshot with no graph in it.
MAIN_HEIGHT = 360

# Was 150, which rendered a 24-PIXEL plot. Measured in the live DOM:
#
#     chart-header   56 px   <- fixed, regardless of panel height
#     chart-body     92 px   of which the time-axis labels take ~68
#     plot svg       24 px   <- what is actually left to draw on
#
# The header is the reason, and it does not shrink with the panel: 56px of a
# 150px panel is 37% spent on a toolbar. Raising the height alone would not
# have fixed this -- the fix is removing the chrome (see BLOCKED_VISIBILITY)
# AND giving it more room.
BLOCKED_HEIGHT = 210
PANEL_GAP = 12

# One content width for every panel, on both gateways.
#
# Also fixed rather than read from the file, for the same reason the heights
# are. The Designer left 8.1's chart at 1222 and 8.3's at 1343, and 1343 in a
# fixed-mode coord container overflows a 1280 viewport -- so the 8.3 dashboard
# had a horizontal scrollbar and a chart running off the right edge, while 8.1
# looked fine from identical code.
CONTENT_WIDTH = 1222

# Deterministic table geometry. `rows.height` defaults to "auto", which makes
# the component's height depend on its content -- fine in isolation, wrong in
# a coord container where the surrounding layout is computed in pixels.
TABLE_ROW_HEIGHT = 28
TABLE_HEADER_HEIGHT = 38

# Verified from the module's own destructuring of the visibility object:
#   let {showPenControlDisplay: g, showDateRangeSelector: y,
#        showTagBrowser: R} = m
#
# Both hidden panels are RUNTIME EDITING affordances, not dashboard content.
# The pen-control table in particular is what was eating the vertical space --
# it is a grid of every pen with min/max/average, which is genuinely useful
# while you build a chart and pure noise once it is built. The date-range
# selector stays: changing the window is the one thing a viewer actually does.
CHART_VISIBILITY = {
    "showTagBrowser": False,
    "showPenControlDisplay": False,
    "showDateRangeSelector": True,
}

# The Blocked panel gets NO chrome at all.
#
# It is a short panel, it shares the main chart's window, and a second copy of
# the range selector is both redundant and the single biggest consumer of its
# height. `showDateRangeSelector` is documented as "Flag representing the
# VISIBLE STATE of the ... date range selector" -- it hides the control, it
# does not change the range, which stays whatever props.config says.
#
# `buttons` is a real sibling of the show* flags: additionalProperties:false
# with exactly these eight keys, identical on 2.1.11 and 3.3.8. They are the
# toolbar (pan/zoom, x-trace, range brush, annotations, fullscreen, settings),
# every one of which is an INTERACTION affordance rather than dashboard
# content -- and the main chart above still has all of them.
BLOCKED_VISIBILITY = {
    "showTagBrowser": False,
    "showPenControlDisplay": False,
    "showDateRangeSelector": False,
    "buttons": {
        "showTagBrowserButton": False,
        "showPanZoomButton": False,
        "showXTraceButton": False,
        "showRangeBrushButton": False,
        "showAnnotationButton": False,
        "showFullscreenButton": False,
        "showSettingsButton": False,
        "showMoreButton": False,
    },
}

# THE PLOT BACKGROUND IS A PROP, NOT CSS.
#
# This was chased the long way round first. The plot surface is an SVG rect
# the component paints with an inline fill, so it looked like a styling
# problem -- but the schema in
# perspective-common-*.jar!/perspective-timeseries.components.json says:
#
#   plots.items.properties.color = {"type":"string","format":"color",
#     "description":"The background color of the plot.","default":"#FFFFFF"}
#
# A Designer-made Power Chart has NO `plots` key at all, so the runtime
# injects one default plot -- and that default is #FFFFFF. That is the white
# rectangle. Setting the prop fixes it on BOTH versions, including 8.3 where
# the CSS route could not reach.
#
# plots.items is additionalProperties:false with exactly four keys
# (relativeWeight, color, markers, style). `style` is a TRAP: the plot-config
# assembly returns it and then discards it in both versions, so it is left
# unset here rather than set and silently ignored.
PLOT_BACKGROUND = "#1A1F28"
CHART_SURFACE = "#12161C"

# X-axis colours. Deliberately NOT axes[N].color: the schema is identical in
# both versions, but on 2.1.11 buildAxisWithDefaults calls getStringValue with
# the default in the VALUE position and throws away whatever view.json
# supplied -- a genuine silent no-op. timeAxis has no such defect.
AXIS_LINE = "#2E3641"
AXIS_TEXT = "#8B94A3"

# The visible window. ONE HOUR by default -- and a CLI flag, because this
# script kept resetting a range that had been set by hand in the Designer.
#
# It is still written on every run rather than read back from the file: this
# script normalises the view, and "preserve whatever is already there" is the
# read-then-write pattern that silently shrank the chart heights 592 -> 410 ->
# 220 across three runs. The fix for stomping a human's choice is to make the
# choice an argument, not to make the script guess.
#
# realtime = a rolling window ending now, which is what the chart's own
# selector sets. The historical alternative needs fixed start/end dates, which
# is the wrong shape for a dashboard that should still be right tomorrow.
#
# Verified against perspective-timeseries.components.json in BOTH modules --
# note this is a DIFFERENT schema file from ia.components.json, which does not
# contain the Power Chart at all:
#   mode           enum ['realtime', 'historical']
#   unitOfTime     number, default 8
#   measureOfTime  enum ['seconds','minutes','hours','days','weeks',
#                        'months','years']      <- plural, all of them
#
# config.rangeStartDate / rangeEndDate are left alone on purpose: both schema
# descriptions literally begin "READ-ONLY:".
RANGE_MEASURES = ["seconds", "minutes", "hours", "days", "weeks", "months",
                  "years"]

# 60 minutes rather than 1 hour. Identical window; the chart renders the
# selector's label straight from these two values, so unitOfTime=1 puts
# "Last 1 hours" on the dashboard. Every enum member is plural and there is no
# singular form, so the only way to avoid the bad grammar is not to use 1.
DEFAULT_RANGE_UNIT = 60
DEFAULT_RANGE_MEASURE = "minutes"


def chart_range(unit, measure):
    return {"mode": "realtime", "unitOfTime": unit, "measureOfTime": measure}


def strip_generated(root):
    """Remove anything a previous run of this script added.

    Without this the script is not idempotent and the failure is visual
    nonsense rather than an error: each run clones the chart again, so a
    second run leaves three power charts stacked in the same coordinate
    space, each squashing the others' plot area to nothing. Observed on 8.1
    after running twice.

    Generated nodes are identified by meta.name, which is why they get one.
    Anything the Designer made is left alone.
    """
    kept = []
    kept_chart = []
    removed = 0
    for child in root.get("children") or []:
        name = (child.get("meta") or {}).get("name", "")
        if name == BLOCKED_CHART_NAME or name.startswith("cap_") \
                or name.startswith("val_"):
            removed = removed + 1
            continue
        kept.append(child)
    root["children"] = kept
    return removed


def build(view, provider, drv, window):
    root = view["root"]
    strip_generated(root)
    chart = find_chart(root)
    if chart is None:
        raise SystemExit("no %s in this view -- add one in the Designer first"
                         % (POWERCHART,))

    pens = chart["props"].get("pens")
    if not pens:
        raise SystemExit("the power chart has no pens to use as a template")
    template = pens[0]

    # --- panel 1: pool counts -------------------------------------------
    count_pens = []
    for name, color in SERIES:
        count_pens.append(make_pen(
            template, name, "%s/pools/%s/count" % (TAG_ROOT, name),
            color, provider, drv))
    chart["props"]["pens"] = count_pens

    # --- panel 2: blocked, cloned from the same known-good node ----------
    #
    # Same six pools, same six colours. Matching the colours is deliberate:
    # a red spike would tell you something is blocked, but the SAME colour as
    # the count panel tells you WHICH pool without reading a legend.
    blocked = copy.deepcopy(chart)
    blocked_pens = []
    for name, color in SERIES:
        blocked_pens.append(make_pen(
            template, name + " blocked",
            "%s/pools/%s/blocked" % (TAG_ROOT, name),
            color, provider, drv))
    blocked["props"]["pens"] = blocked_pens
    # Always set, never conditional on the original having a meta block. The
    # clone MUST carry a name or strip_generated() cannot find it on the next
    # run, and the script silently stops being idempotent -- which is how 8.1
    # ended up with three charts stacked on top of each other.
    blocked["meta"] = dict(blocked.get("meta") or {})
    blocked["meta"]["name"] = BLOCKED_CHART_NAME

    # Hide the runtime editing panels, darken the surfaces, set the window.
    # Both panels share the same window; only the main one keeps the selector.
    for panel, wanted in ((chart, CHART_VISIBILITY),
                          (blocked, BLOCKED_VISIBILITY)):
        config = dict(panel["props"].get("config") or {})
        visibility = dict(config.get("visibility") or {})
        visibility.update(wanted)
        config["visibility"] = visibility
        config.update(window)
        panel["props"]["config"] = config

        # The fix for the white plot area. One plot, explicitly coloured,
        # instead of the implicit #FFFFFF one the runtime injects.
        panel["props"]["plots"] = [
            {"relativeWeight": 1, "color": PLOT_BACKGROUND},
        ]

        style = dict(panel["props"].get("style") or {})
        style["backgroundColor"] = CHART_SURFACE
        panel["props"]["style"] = style

        time_axis = dict(panel["props"].get("timeAxis") or {})
        time_axis["color"] = AXIS_LINE
        tick = dict(time_axis.get("tick") or {})
        tick["color"] = AXIS_LINE
        tick_label = dict(tick.get("label") or {})
        font = dict(tick_label.get("font") or {})
        font["color"] = AXIS_TEXT
        tick_label["font"] = font
        tick["label"] = tick_label
        time_axis["tick"] = tick
        panel["props"]["timeAxis"] = time_axis

    # Blocked gets a short fixed panel. On a healthy gateway it is a flat line
    # on the floor, so it costs little screen; when it lifts off zero, the
    # small vertical range makes a 0 -> 2 change enormous rather than a
    # wobble lost against a count axis that runs to 120.
    pos = dict(chart.get("position") or {})
    width = CONTENT_WIDTH

    # Deterministic vertical stack. Every y is derived from the one above it,
    # so the whole layout moves together and nothing can drift out of step.
    #
    # ORDER IS NOW: tiles, TABLE, blocked chart, count chart.
    #
    # The table used to be last, starting at y=850, which on a 1280x720
    # laptop put it entirely below the fold. It is the answer to "which pool
    # is wrong RIGHT NOW", which is the first question anyone asks, and it
    # was the one thing you had to scroll to reach. Now-then-history: the
    # current state first, the trend underneath for when you need to ask when
    # it started.
    #
    # The count chart goes last rather than the blocked chart because Blocked
    # is the rarer and more urgent signal, and its panel is shorter, so it
    # costs less of the space above the fold.
    tiles_bottom = TILE_TOP + TILE_HEIGHT
    table_head_y = tiles_bottom + 18
    table_y = table_head_y + 34
    table_bottom = table_y + table_height()
    head2_y = table_bottom + 20
    blocked_y = head2_y + 38
    head1_y = blocked_y + BLOCKED_HEIGHT + 20
    chart_y = head1_y + 38
    legend_y = chart_y + MAIN_HEIGHT + 8

    chart["position"] = dict(pos, x=TILE_LEFT, y=chart_y, width=width,
                             height=MAIN_HEIGHT)
    blocked["position"] = dict(pos, x=TILE_LEFT, y=blocked_y, width=width,
                               height=BLOCKED_HEIGHT)
    layout = {"head1_y": head1_y, "legend_y": legend_y, "head2_y": head2_y,
              "table_head_y": table_head_y, "table_y": table_y,
              "width": width}

    # Insert the clone as a sibling of the original.
    parent = find_parent(root, chart)
    if parent is None:
        raise SystemExit("could not find the chart's parent container")
    kids = parent["children"]
    kids.insert(kids.index(chart) + 1, blocked)

    # FIND THE TEMPLATE BEFORE build_tiles() RUNS.
    #
    # This used to sit after the call, and build_tiles() removes every
    # root-level label as its first act -- so the search always found nothing
    # and always fell through to the hardcoded literal. The comment claiming
    # it preferred "a label the Designer actually wrote in THIS view, the most
    # authoritative shape available for this gateway version" described
    # behaviour that had never once executed.
    label_template = find_label_template(root)

    tiles, warning = build_tiles(root, "[default]GatewayHealth/Threads/",
                                 label_template)
    tiles = tiles + build_status_card(root, label_template, drv,
                                      "[default]GatewayHealth/Threads/")

    chrome = []

    # Table first, directly under the tiles.
    rows = build_table(root, TILE_LEFT, layout["table_y"], layout["width"])
    if rows:
        chrome.extend(panel_header(
            label_template, "table", "Current state by pool",
            "All %d pools, live. The charts below are for when it started."
            % (rows,),
            TILE_LEFT, layout["table_head_y"], layout["width"]))

    chrome.extend(panel_header(
        label_template, "blocked", "Blocked, all pools",
        "Its own panel and its own scale. Flat zero is healthy.",
        TILE_LEFT, layout["head2_y"], layout["width"]))

    # Honest about the count: the chart plots 6 of the 14 pools in the table
    # above it. Saying so beats letting someone conclude the other 8 do not
    # exist. The old subtitle said "Six series, not twelve", which was both
    # vague and, since the taxonomy grew to 14, wrong.
    chrome.extend(panel_header(
        label_template, "pools", "Pool counts over time",
        "The %d busiest pools of %d. Stepped, min/max aggregated."
        % (len(SERIES), len(taxonomy.spec_keys())),
        TILE_LEFT, layout["head1_y"], layout["width"]))
    chrome.extend(build_legend(label_template, TILE_LEFT, layout["legend_y"],
                               layout["width"]))

    root["children"] = chrome + list(root["children"])
    return len(count_pens) + len(blocked_pens), tiles, warning, rows


LABEL = "ia.display.label"

# Used only when a view has no label to clone. NOT invented -- every part of
# this was verified against the gateway's own Perspective module:
#
#   the component id `ia.display.label` is registered in
#     perspective-gateway/mounted/js/PerspectiveComponents*.js
#   the node shape {version, type, props, meta, position} appears verbatim in
#     the module's own example view, gateway/comm/dateLabelProject.json
#   the binding keys mode/direct/fallbackDelay/publishInitial/tag are the
#     constants in common/config/constants/TagBindingConstants
#
# Checked in the 2.1.11 module that ships with 8.1.11, i.e. the OLDER of the
# two gateways -- so it is not a shape borrowed from 8.3 and hoped for.
LABEL_TEMPLATE = {
    "version": 0,
    "type": LABEL,
    "props": {},
    "meta": {"name": "label"},
    "position": {"x": 0, "y": 0, "width": 100, "height": 30},
}

# The four numbers worth reading before you look at any chart. Order is
# left-to-right; `alarm` marks the ones that are zero on a healthy gateway and
# therefore mean something the instant they are not.
TILES = [
    ("TotalCount", "TOTAL THREADS", "PeakCount", False),
    ("Pools/webserver/Count", "WEBSERVER", "Pools/webserver/Runnable", False),
    ("BlockedTotal", "BLOCKED", None, True),
    ("DeadlockedCount", "DEADLOCKED", None, True),
]

TILE_TOP = 20
TILE_LEFT = 16
TILE_WIDTH = 190
TILE_HEIGHT = 78
TILE_GAP = 12
CAPTION_HEIGHT = 14
VALUE_HEIGHT = 32

# A coord container used as a card. The shape -- type/props/meta/position with
# a children list -- is the one in the Perspective module's own example view
# (gateway/comm/dateLabelProject.json), and `props.style` is set there too, so
# this is an observed shape rather than an assumed one.
CONTAINER_TEMPLATE = {
    "version": 0,
    "type": "ia.container.coord",
    "props": {},
    "meta": {"name": "card"},
    "position": {"x": 0, "y": 0, "width": 190, "height": 78},
    "children": [],
}

# Colours match the mockup: near-black page, one step lighter for cards, a
# hairline border rather than a shadow. Flat, because a SCADA screen left on a
# wall wants contrast, not depth.
CARD_STYLE = {
    "backgroundColor": "#1A1F28",
    "border": "1px solid #262D38",
    "borderRadius": "8px",
}
ALARM_CARD_STYLE = {
    "backgroundColor": "#1E1A1B",
    "border": "1px solid #4A2F2E",
    "borderRadius": "8px",
}

CAPTION_STYLE = {
    "fontSize": "10px", "letterSpacing": "0.09em", "fontWeight": 600,
    "color": "#7D8796", "textTransform": "uppercase",
}
VALUE_STYLE = {"fontSize": "26px", "fontWeight": 650, "color": "#E6EBF2"}
SUB_STYLE = {"fontSize": "10px", "color": "#9AA3B1"}

# Its own constant so the contrast audit can see it. As an inline literal
# inside build_table() it was invisible to the audit, which kept its own
# hardcoded copy and therefore reported a colour the code no longer used.
TABLE_EMPTY_STYLE = {"fontSize": "11px", "color": "#9AA3B1"}

PANEL_TITLE_STYLE = {
    "fontSize": "11px", "letterSpacing": "0.07em", "fontWeight": 600,
    "color": "#8B94A3", "textTransform": "uppercase",
}
PANEL_SUB_STYLE = {"fontSize": "10px", "color": "#8B94A3"}

LEGEND_STYLE = {"fontSize": "10px", "color": "#98A1AF"}
LEGEND_ITEM_WIDTH = 108


def tag_label(template, name, tag_path, x, y, width, height, style):
    """A label bound to a tag, cloned from one the Designer wrote.

    The binding shape here is copied verbatim from the Designer's own output
    -- `{"type": "tag", "config": {"mode": "direct", "tagPath": ...}}` -- and
    only the tagPath changes.
    """
    node = copy.deepcopy(template)
    node["meta"] = {"name": name}
    node["position"] = {"x": x, "y": y, "width": width, "height": height}
    node["propConfig"] = {
        "props.text": {
            "binding": {
                "type": "tag",
                "config": {
                    "fallbackDelay": 2.5,
                    "mode": "direct",
                    "publishInitial": False,
                    "tagPath": tag_path,
                },
            }
        }
    }
    node["props"] = {"style": style}
    return node


def static_label(template, name, text, x, y, width, height, style):
    node = copy.deepcopy(template)
    node["meta"] = {"name": name}
    node["position"] = {"x": x, "y": y, "width": width, "height": height}
    node.pop("propConfig", None)
    node["props"] = {"text": text, "style": style}
    return node


def table_height():
    """Pixel height of the state table. One place, so the stack cannot drift."""
    return (TABLE_HEADER_HEIGHT
            + TABLE_ROW_HEIGHT * len(taxonomy.spec_keys()) + 2)


def find_label_template(root):
    """A label the Designer wrote, if this view has one.

    Must be called BEFORE build_tiles(), which deletes every root-level label.
    """
    for child in root.get("children") or []:
        if child.get("type") == LABEL:
            return child
    return copy.deepcopy(LABEL_TEMPLATE)


# The status card, in the 414px of dead space to the right of the tile row.
#
# It answers the two questions the dashboard could not answer at all:
#
#   WHICH GATEWAY am I looking at? Both gateways render a pixel-identical
#   page and write byte-identical tag paths into one shared historian. A
#   wrong-gateway misread already happened once in this project's history,
#   when 8.3's chart silently plotted 8.1's threads.
#
#   IS THIS DATA FRESH? If the timer dies, every tile keeps showing its last
#   value and both charts hold a flat line -- which is also exactly what a
#   calm gateway looks like. Without a timestamp on screen, a dead monitor
#   and a healthy one are indistinguishable.
#
# The gateway name is written statically at build time from --drv, because
# that is the same string the chart pens use to select their history, so the
# label cannot disagree with the data being plotted.
#
# The age is NOT computed on the gateway. A sampler that writes its own
# "seconds since last sample" freezes that number at 10 when it dies, and the
# screen then reports healthy forever. The raw timestamp is shown and the
# reader subtracts.
STATUS_CAPTION_STYLE = {
    "fontSize": "10px", "letterSpacing": "0.09em", "fontWeight": 600,
    "color": "#7D8796", "textTransform": "uppercase",
}
STATUS_VALUE_STYLE = {"fontSize": "13px", "fontWeight": 600,
                      "color": "#D3D9E2"}
STATUS_COLS = [
    ("GATEWAY", None),
    ("LAST SAMPLE", "Diagnostics/LastSampleTime"),
    ("SAMPLE MS", "Diagnostics/SampleDurationMs"),
]


def build_status_card(root, template, drv, provider_root):
    """Gateway identity and data freshness, beside the tiles."""
    x = TILE_LEFT + len(TILES) * (TILE_WIDTH + TILE_GAP)
    width = CONTENT_WIDTH + TILE_LEFT - x
    if width < 200:
        return 0

    card = copy.deepcopy(CONTAINER_TEMPLATE)
    card["meta"] = {"name": "cap_tile_STATUS"}
    card["position"] = {"x": x, "y": TILE_TOP,
                        "width": width, "height": TILE_HEIGHT}
    card["props"] = {"style": dict(CARD_STYLE)}

    kids = []
    col_w = (width - 24) // len(STATUS_COLS)
    cx = 12
    for caption, tag in STATUS_COLS:
        kids.append(static_label(template, "cap_" + caption, caption,
                                 cx, 10, col_w - 8, CAPTION_HEIGHT,
                                 STATUS_CAPTION_STYLE))
        if tag is None:
            kids.append(static_label(template, "val_" + caption, drv,
                                     cx, 30, col_w - 8, 20,
                                     STATUS_VALUE_STYLE))
        else:
            kids.append(tag_label(template, "val_" + caption,
                                  provider_root + tag,
                                  cx, 30, col_w - 8, 20,
                                  STATUS_VALUE_STYLE))
        cx = cx + col_w

    card["children"] = kids
    root["children"] = [card] + list(root["children"])
    return 1


def build_tiles(root, provider_root, template=None):
    """Replace whatever labels exist with a clean, complete tile row.

    Rebuilt rather than patched: the Designer leaves labels wherever they were
    dropped, and a row of stat tiles only reads as a row if the spacing is
    uniform. Any label already in the view is used as the clone template so
    the node shape stays the Designer's, not mine.
    """
    # `template` comes in from find_label_template(), which the caller runs
    # BEFORE this function. It has to: the loop below deletes every
    # root-level label, so anything searching for one afterwards finds none.
    if template is None:
        template = copy.deepcopy(LABEL_TEMPLATE)

    for stale in list(root.get("children") or []):
        if stale.get("type") == LABEL:
            root["children"].remove(stale)

    made = []
    x = TILE_LEFT
    for tag, caption, subtag, is_alarm in TILES:
        card_style = dict(CARD_STYLE)
        if is_alarm:
            # A tile that is ALWAYS red is a tile people stop seeing, so the
            # alarm styling here is a slightly warmer border rather than a
            # permanent alert. Making it react to the value needs an
            # expression binding, whose shape this project has not verified.
            card_style = dict(ALARM_CARD_STYLE)

        card = copy.deepcopy(CONTAINER_TEMPLATE)
        card["meta"] = {"name": "cap_tile_" + caption}
        card["position"] = {"x": x, "y": TILE_TOP,
                            "width": TILE_WIDTH, "height": TILE_HEIGHT}
        card["props"] = {"style": card_style}

        kids = [static_label(template, "capText", caption, 12, 10,
                             TILE_WIDTH - 24, CAPTION_HEIGHT, CAPTION_STYLE)]

        # DELIBERATELY NOT red-when-alarm. The alarm tiles used to render
        # their value in ALARM_VALUE_STYLE unconditionally, so BLOCKED and
        # DEADLOCKED sat in alert red at value 0, forever, on a healthy
        # gateway. The comment above this block already argued against
        # exactly that -- "a tile that is ALWAYS red is a tile people stop
        # seeing" -- and then the code did it anyway.
        #
        # Two costs, not one. Red that is always on carries zero bits, so a
        # genuine BlockedTotal of 6 looked identical to yesterday's 0. And
        # #D9534F on the warm card measured 4.35:1, under the 4.5:1 WCAG AA
        # floor, so the alarm colour was also the least readable text on the
        # screen.
        #
        # The card keeps its warmer border, which is a permanent hint that
        # this tile is the one to watch. The NUMBER stays neutral until
        # something can actually make it change -- which needs an expression
        # binding, a shape this project has not verified.
        value_style = dict(VALUE_STYLE)
        kids.append(tag_label(template, "valText", provider_root + tag,
                              12, 26, TILE_WIDTH - 24, VALUE_HEIGHT,
                              value_style))
        if subtag:
            kids.append(tag_label(template, "subText", provider_root + subtag,
                                  12, 60, TILE_WIDTH - 24, 14, SUB_STYLE))
        card["children"] = kids
        made.append(card)
        x = x + TILE_WIDTH + TILE_GAP

    # Tiles first so they render above the charts in the coord container.
    root["children"] = made + list(root["children"])
    return len(TILES), None


def panel_header(template, key, title, subtitle, x, y, width):
    """A title and one line of guidance above a panel.

    The subtitle is not decoration. Someone opening this screen cold needs to
    be told that flat zero on the Blocked panel is the healthy state, or they
    will read an empty chart as a broken one.
    """
    made = [static_label(template, "cap_h_" + key, title, x, y,
                         width, 18, PANEL_TITLE_STYLE)]
    if subtitle:
        made.append(static_label(template, "cap_s_" + key, subtitle, x,
                                 y + 17, width, 14, PANEL_SUB_STYLE))
    return made


def build_legend(template, x, y, width):
    """A legend built out of labels, because the chart will not give us one.

    The chart's own pen table is hidden -- it is an editing grid, not a
    legend -- and no separate legend prop has been verified. Rather than
    guess at one, the legend is drawn from the same primitives already proven
    to work: a small label with a background colour as the swatch, and a
    label beside it for the name.

    It also means the legend is guaranteed to match SERIES, since both come
    from the same list.
    """
    made = []
    cursor = x
    for name, color in SERIES:
        made.append(static_label(
            template, "cap_sw_" + name, "", cursor, y + 6, 14, 3,
            {"backgroundColor": color, "borderRadius": "2px"}))
        made.append(static_label(
            template, "cap_lg_" + name, name, cursor + 20, y,
            LEGEND_ITEM_WIDTH - 20, 14, LEGEND_STYLE))
        cursor = cursor + LEGEND_ITEM_WIDTH
    return made


# Table styling, in the same palette as the tile cards so the page reads as
# one surface rather than three widgets that happen to share a background.
TABLE_STYLE = {
    "backgroundColor": "#1A1F28",
    "border": "1px solid #262D38",
    "borderRadius": "8px",
}
TABLE_HEADER_STYLE = {
    "backgroundColor": "#12161C",
    "color": "#8B94A3",
    "fontSize": "10px",
    "fontWeight": 600,
    "letterSpacing": "0.08em",
    "textTransform": "uppercase",
}
TABLE_BODY_STYLE = {
    "color": "#D3D9E2",
    "fontSize": "12px",
}
TABLE_STRIPE_EVEN = "#1A1F28"
TABLE_STRIPE_ODD = "#171C24"
TABLE_HIGHLIGHT = "#222A36"

# Shown before the first sample lands, in place of an empty box that reads as
# a broken component.
TABLE_EMPTY_TEXT = ("No sample yet -- the gateway timer writes "
                    "GatewayHealth/Threads/PoolTable every 10 seconds.")


def build_table(root, x, y, width):
    """Point the Designer's table at the PoolTable DataSet tag.

    HOW THIS ARRIVED AT A DATASET, because the wrong route looks right.

    TRIED AND REVERTED: binding each cell via a `props.data[<row>].<column>`
    prop path. The file wrote correctly -- 12 rows, 60 bindings, right column
    names -- and Perspective rendered the headers and NO ROWS, with no error
    anywhere. That is exactly how CLAUDE.md #5 says an unverified shape fails:
    silently, and not diagnosable from the file.

    What replaced it uses only shapes this project has already proven:

      props.data          the component's own schema (ia.components.json,
                          read out of perspective-common on BOTH gateways)
                          types it as ["array","dataset"], described as "Can
                          be a dataset, an array of arrays, or an array of
                          objects". Identical on 2.1.11 and 3.3.8.
      the tag binding     byte-identical in shape to the one the stat tiles
                          use for props.text, which is Designer-written and
                          demonstrably works.
      props.columns       left unset. It defaults to [] and the component
                          derives its columns from the dataset, so the header
                          text comes from snapshot.TABLE_HEADERS and there is
                          no second list to keep in step.

    The Designer ships every new table with city/country/population sample
    data, which persists in the file until something replaces it -- that is
    the "table still showing sample data" symptom, not a binding failure.
    Setting props.data to [] here matters even though the binding overwrites
    it at runtime: if the binding ever breaks, an empty table is an honest
    signal, whereas Tokyo and Jakarta look like a working screen.
    """
    table = None
    for child in root.get("children") or []:
        if child.get("type") == TABLE:
            table = child
            break
    if table is None:
        raise SystemExit(
            "no %s in this view -- drop a Table component anywhere in the "
            "Designer and re-run. This script edits the Designer's node "
            "rather than synthesising one (CLAUDE.md #5)." % (TABLE,))

    pool_keys = taxonomy.spec_keys()

    props = dict(table.get("props") or {})
    props["data"] = []
    props["style"] = TABLE_STYLE
    props["headerStyle"] = TABLE_HEADER_STYLE
    props["bodyStyle"] = TABLE_BODY_STYLE

    # OFF, and this is the difference between twelve rows and none.
    #
    # `virtualized` defaults to True: "only the rows needed at any given time
    # are displayed on screen". The implementation is react-virtualized, which
    # measures its viewport once on mount. This table sits at y=790 in a fixed
    # coord container, so on a 720px-tall viewport it mounts BELOW THE FOLD --
    # the grid measures itself as 0x0 and renders no rows, and it never
    # re-measures when you scroll to it.
    #
    # Observed identically on 2.1.11 and 3.3.8: correct column headers,
    # props.data holding all twelve rows, and a ReactVirtualized__Grid of
    # height 0 / width 0. Nothing in view.json looked wrong, and nothing was
    # logged -- the data was arriving the whole time.
    #
    # Twelve rows do not need virtualizing. Turning it off renders them all
    # eagerly, which is both correct and cheaper than the machinery it removes.
    props["virtualized"] = False

    rows = dict(props.get("rows") or {})
    rows["height"] = TABLE_ROW_HEIGHT
    rows["striped"] = {
        "enabled": True,
        "color": {"even": TABLE_STRIPE_EVEN, "odd": TABLE_STRIPE_ODD},
    }
    rows["highlight"] = {"enabled": True, "color": TABLE_HIGHLIGHT}
    props["rows"] = rows

    # Twelve rows against a default page size of 25: the pager could only ever
    # say "1-12 of 12" and take up 40px doing it.
    pager = dict(props.get("pager") or {})
    pager["bottom"] = False
    pager["top"] = False
    props["pager"] = pager

    empty = dict(props.get("emptyMessage") or {})
    no_data = dict(empty.get("noData") or {})
    no_data["text"] = TABLE_EMPTY_TEXT
    no_data["textStyle"] = dict(TABLE_EMPTY_STYLE)
    empty["noData"] = no_data
    props["emptyMessage"] = empty

    table["props"] = props

    config = dict(table.get("propConfig") or {})
    # Drop the per-cell bindings the reverted approach left behind, so a view
    # that still carries them does not fight the dataset binding.
    for existing in list(config.keys()):
        if existing.startswith("props.data["):
            del config[existing]
    config["props.data"] = {
        "binding": {
            "type": "tag",
            "config": {
                "fallbackDelay": 2.5,
                "mode": "direct",
                "publishInitial": False,
                "tagPath": tagpaths.gateway_tag(tagpaths.POOL_TABLE),
            },
        }
    }
    table["propConfig"] = config

    table["position"] = {
        "x": x, "y": y, "width": width,
        "height": TABLE_HEADER_HEIGHT + TABLE_ROW_HEIGHT * len(pool_keys) + 2,
    }
    return len(pool_keys)


def find_parent(node, target):
    for child in node.get("children") or []:
        if child is target:
            return node
        found = find_parent(child, target)
        if found is not None:
            return found
    return None


def read_view(container, project, view_name):
    remote = "%s/%s/view.json" % (PROJECT_DIR % (project,), view_name)
    result = subprocess.run(["docker", "exec", container, "sh", "-c",
                             "cat '%s'" % (remote,)],
                            capture_output=True, text=True, errors="replace")
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit("could not read %s from %s:\n%s"
                         % (remote, container, result.stderr.strip()))
    return json.loads(result.stdout), remote


def write_view(container, remote, view):
    payload = json.dumps(view, indent=2) + "\n"
    # Streamed, not `docker cp`: Docker Desktop for Windows rewrites the
    # destination path and the copy fails.
    proc = subprocess.Popen(["docker", "exec", "-i", container, "sh", "-c",
                             "cat > '%s'" % (remote,)],
                            stdin=subprocess.PIPE)
    proc.communicate(payload.encode("utf-8"))
    if proc.returncode != 0:
        raise SystemExit("failed writing %s" % (remote,))


ORIGINALS_DIR = os.path.join(REPO_ROOT, "ignition-project", "designer-originals")


def is_pristine(view):
    """True if this view still looks like the Designer wrote it.

    Detected by the absence of the nodes THIS script creates. Cheap, and it
    only has to be right in one direction: a false "not pristine" refuses to
    archive, which is safe. A false "pristine" would archive our own output as
    the original, which is the failure this whole function exists to prevent.
    """
    for child in (view.get("root", {}).get("children") or []):
        name = (child.get("meta") or {}).get("name", "")
        if name == BLOCKED_CHART_NAME or name.startswith("cap_") \
                or name.startswith("val_"):
            return False
    return True


def archive_original(container, remote, view, view_name):
    """Archive the Designer's version INTO THE REPO, not next to the file.

    The old behaviour kept `view.json.orig` beside the view. That is inside a
    directory the Designer owns, and the Designer deletes files it does not
    recognise when it saves: adding one Table component wiped the 8.1 backup
    entirely. Container rebuilds take it too.

    Worse, the old guard was `test -f X.orig || cp X X.orig` -- create it only
    if missing. So once the Designer had deleted it, the very next run copied
    the ALREADY-NORMALISED file into its place, and the "known-good Designer
    shape" silently became a copy of this script's own output. CLAUDE.md #5
    leans on that file being genuine, so that is a quiet loss of the one
    artifact the rule depends on.

    Now: the archive lives in the repo, under git, and is only ever written
    from a view that still looks Designer-made. If we have no archive and the
    live view is already normalised, that is stated loudly rather than papered
    over -- the original is gone and only a fresh Designer export brings it
    back.
    """
    if not os.path.isdir(ORIGINALS_DIR):
        os.makedirs(ORIGINALS_DIR)
    archive = os.path.join(ORIGINALS_DIR, "%s.json" % (view_name,))

    if os.path.exists(archive):
        return "archived original: %s" % (
            os.path.relpath(archive, REPO_ROOT),)

    if not is_pristine(view):
        return ("WARNING: no Designer original archived for %s, and the live "
                "view is already normalised -- nothing safe to archive. "
                "Re-export from the Designer if you want the fallback back."
                % (view_name,))

    with open(archive, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(view, indent=2) + "\n")
    return "archived the Designer original -> %s" % (
        os.path.relpath(archive, REPO_ROOT),)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--view", required=True,
                        help="view name, e.g. GwThreadingTrends")
    parser.add_argument("--provider", required=True,
                        help="tag history provider name")
    parser.add_argument("--drv", required=True,
                        help="gateway system name as it appears in sqlth_drv")
    parser.add_argument("--project",
                        default="Gateway_Thread_Pool_Analyzer_and_Historizer")
    parser.add_argument("--restart", action="store_true",
                        help="restart the gateway afterwards (needed on 8.3, "
                             "which does not watch the project directory)")
    parser.add_argument("--range", type=int, default=DEFAULT_RANGE_UNIT,
                        dest="range_unit",
                        help="how much time both charts show (default 1)")
    parser.add_argument("--range-units", default=DEFAULT_RANGE_MEASURE,
                        choices=RANGE_MEASURES, dest="range_measure",
                        help="unit for --range (default hours). The list is "
                             "the component schema's own enum.")
    args = parser.parse_args()

    if args.range_unit < 1:
        raise SystemExit("--range must be at least 1")

    window = chart_range(args.range_unit, args.range_measure)

    view, remote = read_view(args.container, args.project, args.view)
    # Archive BEFORE build() mutates `view` in place.
    archived = archive_original(args.container, remote, view, args.view)

    pens, tiles, warning, rows = build(view, args.provider, args.drv, window)
    write_view(args.container, remote, view)

    print("%s/%s: %d pens across 2 panels, %d tiles, %d table rows, "
          "last %d %s, provider=%s drv=%s"
          % (args.container, args.view, pens, tiles, rows, args.range_unit,
             args.range_measure, args.provider, args.drv))
    if warning:
        print("  WARNING: %s" % (warning,))
    print("  %s" % (archived,))

    if args.restart:
        subprocess.check_call(["docker", "restart", args.container],
                              stdout=subprocess.DEVNULL)
        print("  restarted (8.3 loads project resources at boot only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
