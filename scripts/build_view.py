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
BLOCKED_HEIGHT = 150
PANEL_GAP = 12

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

# "Last 24 hours", the window the mockup asks for.
#
# realtime here means a rolling window ending now, which is what the chart's
# own "Last 8 hours" selector sets -- not "poll the live tag". The historical
# alternative needs fixed startDate/endDate, which is the wrong shape for a
# dashboard that should still be right tomorrow.
#
# config.rangeStartDate / rangeEndDate are left alone on purpose: both schema
# descriptions literally begin "READ-ONLY:".
CHART_RANGE = {
    "mode": "realtime",
    "unitOfTime": 24,
    "measureOfTime": "hours",
}


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


def build(view, provider, drv):
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
    for panel in (chart, blocked):
        config = dict(panel["props"].get("config") or {})
        visibility = dict(config.get("visibility") or {})
        visibility.update(CHART_VISIBILITY)
        config["visibility"] = visibility
        config.update(CHART_RANGE)
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
    width = pos.get("width", 1222)

    # Deterministic vertical stack. Every y is derived from the one above it,
    # so the whole layout moves together and nothing can drift out of step.
    tiles_bottom = TILE_TOP + TILE_HEIGHT
    head1_y = tiles_bottom + 18
    chart_y = head1_y + 38
    legend_y = chart_y + MAIN_HEIGHT + 8
    head2_y = legend_y + 26
    blocked_y = head2_y + 38
    table_head_y = blocked_y + BLOCKED_HEIGHT + 20
    table_y = table_head_y + 34

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

    tiles, warning = build_tiles(root, "[default]GatewayHealth/Threads/")

    # Headers and legend, from the same label template the tiles used.
    label_template = None
    for child in root.get("children") or []:
        if child.get("type") == LABEL:
            label_template = child
            break
    if label_template is None:
        label_template = copy.deepcopy(LABEL_TEMPLATE)

    chrome = []
    chrome.extend(panel_header(
        label_template, "pools", "Pool counts",
        "Stepped, min/max aggregated. Six series, not twelve.",
        TILE_LEFT, layout["head1_y"], layout["width"]))
    chrome.extend(build_legend(label_template, TILE_LEFT, layout["legend_y"],
                               layout["width"]))
    chrome.extend(panel_header(
        label_template, "blocked", "Blocked — all pools",
        "Its own panel and its own scale. Flat zero is healthy.",
        TILE_LEFT, layout["head2_y"], layout["width"]))

    # Table below the charts, sized to the pool list.
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from thread_monitor import taxonomy as _tax
    pool_keys = _tax.spec_keys()
    cells = build_table(root, "[default]GatewayHealth/Threads/", pool_keys)
    has_table = False
    for child in root["children"]:
        if child.get("type") == TABLE:
            has_table = True
            child["position"] = {
                "x": TILE_LEFT, "y": layout["table_y"],
                "width": layout["width"],
                "height": 44 + 26 * len(pool_keys)}
    if has_table:
        chrome.extend(panel_header(
            label_template, "table", "Current state by pool",
            "The right-now answer, so the chart is only for history.",
            TILE_LEFT, layout["table_head_y"], layout["width"]))

    root["children"] = chrome + list(root["children"])
    return len(count_pens) + len(blocked_pens), tiles, warning, cells


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
ALARM_VALUE_STYLE = {"fontSize": "26px", "fontWeight": 650,
                     "color": "#D9534F"}
SUB_STYLE = {"fontSize": "10px", "color": "#6C7583"}

PANEL_TITLE_STYLE = {
    "fontSize": "11px", "letterSpacing": "0.07em", "fontWeight": 600,
    "color": "#8B94A3", "textTransform": "uppercase",
}
PANEL_SUB_STYLE = {"fontSize": "10px", "color": "#626B78"}

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


def build_tiles(root, provider_root):
    """Replace whatever labels exist with a clean, complete tile row.

    Rebuilt rather than patched: the Designer leaves labels wherever they were
    dropped, and a row of stat tiles only reads as a row if the spacing is
    uniform. Any label already in the view is used as the clone template so
    the node shape stays the Designer's, not mine.
    """
    labels = []
    for child in root.get("children") or []:
        if child.get("type") == LABEL:
            labels.append(child)
    if labels:
        # Prefer a label the Designer actually wrote in THIS view -- it is
        # the most authoritative shape available for this gateway version.
        template = labels[0]
    else:
        template = copy.deepcopy(LABEL_TEMPLATE)

    for stale in labels:
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

        value_style = dict(VALUE_STYLE)
        if is_alarm:
            value_style = dict(ALARM_VALUE_STYLE)
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


def build_table(root, provider_root, pool_keys):
    """Turn the Designer's stock table into a live per-pool state grid.

    Perspective ships tables with city/country/population sample data, which
    stays there until something replaces it.

    TRIED AND REVERTED: binding each cell via a `props.data[<row>].<column>`
    prop path. The file wrote correctly -- 12 rows, 60 bindings, right column
    names -- and Perspective rendered the headers and NO ROWS, with no error
    anywhere. Prop-path bindings into an array element are not a shape this
    project ever verified, and it failed exactly the way CLAUDE.md #5 says an
    unverified shape fails: silently, and not diagnosable from the file.

    So this now only strips the stock city/country/population sample data and
    leaves the table empty rather than lying. Making it live needs `props.data`
    bound to a single DataSet -- most likely a DataSet-typed tag the sampler
    writes each cycle, which would use only the tag binding already verified.
    That is a change to the tag set, not to this script.
    """
    table = None
    for child in root.get("children") or []:
        if child.get("type") == TABLE:
            table = child
            break
    if table is None:
        return 0

    props = dict(table.get("props") or {})
    props["data"] = []
    table["props"] = props

    config = dict(table.get("propConfig") or {})
    for existing in list(config.keys()):
        if existing.startswith("props.data["):
            del config[existing]
    table["propConfig"] = config

    return 0


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


def backup(container, remote):
    """Keep the Designer's original next to it. It is the known-good shape."""
    subprocess.check_call(["docker", "exec", container, "sh", "-c",
                           "test -f '%s.orig' || cp '%s' '%s.orig'"
                           % (remote, remote, remote)])


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
    args = parser.parse_args()

    view, remote = read_view(args.container, args.project, args.view)
    backup(args.container, remote)

    pens, tiles, warning, cells = build(view, args.provider, args.drv)
    write_view(args.container, remote, view)

    print("%s/%s: %d pens across 2 panels, %d tiles, %d table cells, "
          "provider=%s drv=%s"
          % (args.container, args.view, pens, tiles, cells, args.provider,
             args.drv))
    if warning:
        print("  WARNING: %s" % (warning,))
    print("  original kept at %s.orig" % (remote,))

    if args.restart:
        subprocess.check_call(["docker", "restart", args.container],
                              stdout=subprocess.DEVNULL)
        print("  restarted (8.3 loads project resources at boot only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
