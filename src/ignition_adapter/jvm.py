"""Read the gateway JVM's live threads via ThreadMXBean.

This is the only module that touches java.lang.management. It collects raw
(name, state) string pairs plus the JVM-level counters, hands them to
thread_monitor.sampler, and returns the resulting Snapshot. It contains no
counting logic of its own -- that all lives on the pure side of the boundary
(CLAUDE.md #1).

Three rules, carried over from tag-history-linkage-scanner's gateway.py and
for the same reasons:

FIRST: every JVM-level fact is optional. A failed probe becomes None with the
reason recorded, and the sample still completes. The whole point of this tool
is to keep reporting while a gateway is unwell, and an unwell gateway is
exactly where an introspection call is most likely to throw. Losing the
thread counts because getPeakThreadCount() had a bad day would be absurd.

SECOND: facts are attempted through documented routes in order and the one
that worked is written to Diagnostics/ApiRoute. This project spans 8.1 and
8.3 across two JVM major versions. Every call used here is Java 8+ and should
behave identically on both -- but "should" is what the provenance is for, and
it makes the first run on any gateway a free experiment that pins the API for
that version.

THIRD: no stack traces, ever. getThreadInfo is called with maxDepth 0. See
stubs.py -- the one-argument form is already equivalent, and this is a scope
guard in CLAUDE.md, not an optimisation.

Jython 2.7: no f-strings, no comprehensions.
"""
import sys

from thread_monitor import sampler

try:
    from java.lang.management import ManagementFactory
except ImportError:
    # CPython test suite. jvm.read() takes an injected bean there.
    ManagementFactory = None


def get_bean():
    """The JVM's ThreadMXBean, or None if it cannot be reached."""
    if ManagementFactory is None:
        return None
    return ManagementFactory.getThreadMXBean()


def collect_pairs(bean, notes):
    """(name, state) string pairs for every live thread.

    Two things here are not optional, and both come from the API contract
    rather than from caution:

    - `getThreadInfo` returns None entries for threads that died between the
      id list and this call. On a busy gateway that is routine, not an error.
      Skipping them is why this loop is not a one-liner.
    - States are converted to STRINGS here, at the boundary. Past this point
      nothing knows java.lang.Thread.State exists.
    """
    ids = bean.getAllThreadIds()
    if not ids:
        notes.append("getAllThreadIds returned nothing")
        return []

    infos = bean.getThreadInfo(ids, 0)
    if infos is None:
        notes.append("getThreadInfo returned null for %d ids" % (len(ids),))
        return []

    pairs = []
    vanished = 0
    for info in infos:
        if info is None:
            vanished = vanished + 1
            continue
        pairs.append((_text(info.getThreadName()),
                      _state_name(info.getThreadState())))

    if vanished:
        # Normal, and worth seeing: a large number means high thread churn.
        notes.append("%d thread(s) died between getAllThreadIds and "
                     "getThreadInfo" % (vanished,))
    return pairs


def _text(value):
    if value is None:
        return ""
    return "%s" % (value,)


def _state_name(state):
    """The Thread.State enum as a plain string.

    `.name()` is the documented accessor; str() on the enum yields the same
    text and is the fallback if the accessor is ever not callable from
    Jython. Both are tried rather than betting on one.

    VERIFIED on Jython 2.7.2 against a real ThreadMXBean: this returns
    `unicode`, not `str`. Harmless -- under Python 2, u'RUNNABLE' compares
    and hashes equal to 'RUNNABLE', so the dict lookups in snapshot.PoolCount
    behave identically. Recorded because `isinstance(x, str)` is False for it
    and a test asserting that would pass on CPython 3 and fail on a gateway.
    """
    if state is None:
        return "UNKNOWN"
    try:
        return "%s" % (state.name(),)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        return "%s" % (state,)


def deadlocked_count(bean, notes):
    """How many threads are deadlocked. None if the probe failed.

    findDeadlockedThreads() returns NULL, not an empty array, when nothing is
    deadlocked -- which arrives as None under Jython, where len(None) raises.
    Getting this wrong is the classic way this metric ends up either
    permanently zero or throwing on every single sample.
    """
    try:
        found = bean.findDeadlockedThreads()
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        notes.append("findDeadlockedThreads failed -- %s"
                     % (sys.exc_info()[1],))
        return None
    if found is None:
        return 0  # healthy, and explicitly not a failed probe
    return len(found)


def _counter(bean, method_name, notes):
    """One int counter, or None with the reason recorded."""
    try:
        method = getattr(bean, method_name)
        return int(method())
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        notes.append("%s failed -- %s" % (method_name, sys.exc_info()[1]))
        return None


def read(bean=None, clock=None):
    """Take one full sample and return a populated Snapshot.

    `bean` and `clock` are injectable so the CPython tests exercise this exact
    code path with the doubles in stubs.py. On a gateway both are left None.

    Never raises. A total failure comes back as an empty Snapshot with
    last_error set, because a gateway timer that throws stops running and
    takes the trend with it -- and the trend going flat is precisely the
    signal someone would misread as "the gateway got better".
    """
    notes = []
    started = _millis(clock)

    if bean is None:
        bean = get_bean()

    if bean is None:
        snap = sampler.count([])
        snap.last_error = ("no ThreadMXBean available -- not running inside "
                           "a JVM?")
        snap.api_route = "unavailable"
        return snap

    pairs = []
    try:
        pairs = collect_pairs(bean, notes)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        notes.append("collect_pairs failed -- %s" % (sys.exc_info()[1],))

    snap = sampler.count(pairs)

    snap.peak_threads = _counter(bean, "getPeakThreadCount", notes)
    snap.daemon_threads = _counter(bean, "getDaemonThreadCount", notes)
    snap.deadlocked_count = deadlocked_count(bean, notes)
    snap.api_route = "getThreadInfo(long[],0)"

    # getThreadCount() is the JVM's own answer; total_threads is ours. They
    # should agree, and a persistent gap means collect_pairs is dropping
    # threads -- worth surfacing rather than quietly trusting our own count.
    reported = _counter(bean, "getThreadCount", notes)
    if reported is not None and reported != snap.total_threads:
        notes.append("getThreadCount says %d, counted %d"
                     % (reported, snap.total_threads))

    # int() rather than the raw difference: on Jython 2.7 the epoch-millis
    # arithmetic yields a Python long (verified -- it printed as `4L`), and
    # the target tag is an Int4. Small values coerce fine either way; this is
    # so the type at the tag boundary is not a surprise.
    snap.sample_duration_ms = int(_millis(clock) - started)
    snap.last_error = "; ".join(notes)
    return snap


def _millis(clock):
    if clock is not None:
        return clock()
    import time
    return int(time.time() * 1000)
