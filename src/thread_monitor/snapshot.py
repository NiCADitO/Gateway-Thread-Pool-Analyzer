"""The value objects a sample produces, and how they flatten into tag writes.

Pure data. No clock, no I/O, no Ignition. The adapter fills in the fields it
alone can know (peak, daemon, deadlocks, duration) and everything else comes
out of sampler.count().

Jython 2.7: no f-strings, no comprehensions, old-style classes.
"""
from thread_monitor import tagpaths

# The four states that get their own tag, plus the two that do not.
# See tagpaths.STATE_MEMBERS for why NEW and TERMINATED are folded into Count.
ALL_STATES = ["NEW", "RUNNABLE", "BLOCKED", "WAITING", "TIMED_WAITING",
              "TERMINATED"]

# How many unmatched thread names to carry. Enough to identify a new pool,
# short enough to fit in a string tag and to read at a glance.
UNMATCHED_LIMIT = 12


class PoolCount(object):
    """One bucket's counts for a single sample."""

    def __init__(self, key):
        self.key = key
        self.total = 0
        self.states = {}
        for state in ALL_STATES:
            self.states[state] = 0

    def add(self, state):
        self.total = self.total + 1
        if state in self.states:
            self.states[state] = self.states[state] + 1
        else:
            # An unrecognised state name still counts toward the total. Losing
            # it from `total` would break the sum-equals-total invariant that
            # test_sampler leans on, and that the gateway trend leans on too.
            self.states[state] = 1

    def state(self, name):
        return self.states.get(name, 0)

    def __repr__(self):
        return "PoolCount(%s, total=%d)" % (self.key, self.total)


class Snapshot(object):
    """One complete sample.

    `pools` is in taxonomy order, so iteration order is the chart legend
    order and the tag write order, and all three stay in step.
    """

    def __init__(self):
        self.pools = []
        self.total_threads = 0
        self.unmatched = []

        # Filled in by the Ignition adapter; None means "not collected".
        # Kept as None rather than 0 so a failed probe is distinguishable
        # from a genuine zero on the trend.
        self.peak_threads = None
        self.daemon_threads = None
        self.deadlocked_count = None
        self.sample_duration_ms = None
        self.api_route = ""
        self.last_error = ""
        self.last_sample_time = None
        # The same instant as last_sample_time, formatted for a human. See
        # tagpaths.LAST_SAMPLE_TEXT for why both exist.
        self.last_sample_text = ""

    def pool(self, key):
        for entry in self.pools:
            if entry.key == key:
                return entry
        return None

    def state_total(self, state):
        """How many threads gateway-wide are in `state`."""
        total = 0
        for entry in self.pools:
            total = total + entry.state(state)
        return total

    def __repr__(self):
        return "Snapshot(total=%d, pools=%d)" % (self.total_threads,
                                                 len(self.pools))


def _int_or_default(value, default):
    if value is None:
        return default
    return value


def flatten_for_write(snap):
    """Return (paths, values) ready for system.tag.writeBlocking.

    Order matches tagpaths.scalar_paths() so the two cannot drift apart
    without a test failing. The PoolTable DataSet is NOT here -- building it
    needs system.dataset.toDataSet, which this package may not touch; the
    adapter appends it to this same batch. See pool_table() below.

    Unset adapter fields are written as -1 rather than skipped. A gap in the
    write list would silently leave the previous sample's value on the tag,
    which on a trend reads as "still fine" -- exactly the wrong story. -1 is
    outside the valid range of every one of these metrics and reads as
    "probe failed" on a chart.
    """
    paths = []
    values = []

    for entry in snap.pools:
        paths.append(tagpaths.pool_member(entry.key, tagpaths.COUNT_MEMBER))
        values.append(entry.total)
        for member, state in tagpaths.STATE_MEMBERS:
            paths.append(tagpaths.pool_member(entry.key, member))
            values.append(entry.state(state))

    paths.append(tagpaths.gateway_tag(tagpaths.TOTAL_COUNT))
    values.append(snap.total_threads)
    paths.append(tagpaths.gateway_tag(tagpaths.PEAK_COUNT))
    values.append(_int_or_default(snap.peak_threads, -1))
    paths.append(tagpaths.gateway_tag(tagpaths.DAEMON_COUNT))
    values.append(_int_or_default(snap.daemon_threads, -1))
    paths.append(tagpaths.gateway_tag(tagpaths.DEADLOCKED_COUNT))
    values.append(_int_or_default(snap.deadlocked_count, -1))
    # Derived from the pool counts in this same snapshot, so it cannot drift
    # out of step with them the way a separate probe could.
    paths.append(tagpaths.gateway_tag(tagpaths.BLOCKED_TOTAL))
    values.append(snap.state_total("BLOCKED"))

    paths.append(tagpaths.diagnostic_tag(tagpaths.SAMPLE_DURATION_MS))
    values.append(_int_or_default(snap.sample_duration_ms, -1))
    paths.append(tagpaths.diagnostic_tag(tagpaths.LAST_SAMPLE_TIME))
    values.append(snap.last_sample_time)
    paths.append(tagpaths.diagnostic_tag(tagpaths.LAST_SAMPLE_TEXT))
    values.append(snap.last_sample_text)
    paths.append(tagpaths.diagnostic_tag(tagpaths.LAST_ERROR))
    values.append(snap.last_error)
    paths.append(tagpaths.diagnostic_tag(tagpaths.API_ROUTE))
    values.append(snap.api_route)
    paths.append(tagpaths.diagnostic_tag(tagpaths.UNMATCHED_NAMES))
    values.append(", ".join(snap.unmatched))

    return paths, values


# Column headers for the Perspective table.
#
# These become the DataSet's column names, and with no `props.columns` on the
# table component the component derives its headers from them -- so this list
# is the only place the table's headers are written. Human-spaced ("Timed
# Waiting") rather than tag-member-spelled ("TimedWaiting") because nothing
# downstream matches on them: they are read by people, not by code.
TABLE_HEADERS = ["Pool", "Count", "Runnable", "Blocked", "Waiting",
                 "Timed Waiting"]


def pool_table(snap):
    """Return (headers, rows) for the per-pool current-state table.

    Pure: lists of strings and ints. The adapter turns this into a real
    Dataset with system.dataset.toDataSet, because nothing in this package may
    touch system.* -- see tagpaths.scalar_paths for why that split exists.

    Row order is catalog order, the same order as `snap.pools`, which is also
    the Power Chart's legend order. Deliberately NOT sorted by count: sorting
    would put the busiest pool on top, but the rows would then reshuffle every
    time two pools swapped places, and a table that rearranges itself under
    someone reading it is worse than one where the interesting row is third.

    The first column is the pool KEY, not its label. The key is what appears
    in the chart legend and in the tag path, so a row here can be traced to
    both without a translation step.
    """
    rows = []
    for entry in snap.pools:
        rows.append([
            entry.key,
            entry.total,
            entry.state("RUNNABLE"),
            entry.state("BLOCKED"),
            entry.state("WAITING"),
            entry.state("TIMED_WAITING"),
        ])
    return TABLE_HEADERS, rows


def format_report(snap):
    """A plain-text table of a snapshot, for the script console.

    This is what M2's read-only `dump()` prints, and what someone runs first
    when the trend looks wrong.
    """
    lines = []
    lines.append("Threads: %d total" % (snap.total_threads,))
    if snap.peak_threads is not None:
        lines.append("Peak: %d   Daemon: %s   Deadlocked: %s" % (
            snap.peak_threads,
            snap.daemon_threads,
            snap.deadlocked_count))
    if snap.sample_duration_ms is not None:
        lines.append("Sample took %d ms via %s" % (snap.sample_duration_ms,
                                                   snap.api_route))
    lines.append("")
    header = "%-14s %6s %9s %8s %8s %13s" % (
        "POOL", "COUNT", "RUNNABLE", "BLOCKED", "WAITING", "TIMED_WAITING")
    lines.append(header)
    lines.append("-" * len(header))
    for entry in snap.pools:
        lines.append("%-14s %6d %9d %8d %8d %13d" % (
            entry.key,
            entry.total,
            entry.state("RUNNABLE"),
            entry.state("BLOCKED"),
            entry.state("WAITING"),
            entry.state("TIMED_WAITING")))
    if snap.unmatched:
        lines.append("")
        lines.append("Unmatched (add a PoolSpec for these):")
        for name in snap.unmatched:
            lines.append("  " + name)
    if snap.last_error:
        lines.append("")
        lines.append("ERROR: " + snap.last_error)
    return "\n".join(lines)
