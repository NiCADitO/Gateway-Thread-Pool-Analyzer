"""The single inventory of every external signature this project relies on.

CLAUDE.md #4: never guess a `system.*` or Java signature. A wrong call compiles
fine under Jython and fails on a production gateway, which is the worst
available place to discover it. If what you need is not documented here, add a
`TODO(human): confirm signature for x.y`, stub it, and move on.

This module also provides the CPython test doubles, so the same code paths run
under pytest with no gateway.

===========================================================================
java.lang.management -- verified against the Java 17 JavaDoc, 2026-07-31
===========================================================================

ManagementFactory.getThreadMXBean()
    -> ThreadMXBean. Always available; the threading MXBean is mandatory in
       every JVM implementation.

ThreadMXBean.getAllThreadIds()
    -> long[] of LIVE thread ids.

    IMPORTANT: this is NOT every thread in a `kill -3` dump. VM-internal
    threads -- GC Thread#N, G1 *, VM Thread, VM Periodic Task Thread -- are
    not reported. Measured on 8.1.11: the dump listed 130, this returns 117.

ThreadMXBean.getThreadInfo(long[] ids)
    -> ThreadInfo[]. "with no stack trace. This method is equivalent to
       calling: getThreadInfo(ids, 0)".

    So the one-argument form is ALREADY the cheap form -- it does not capture
    and discard stack traces. Worth stating because assuming otherwise is the
    obvious mistake, and it is the one this project's own plan made before
    checking. We call the two-argument form purely so the call site says so.

ThreadMXBean.getThreadInfo(long[] ids, int maxDepth)
    -> ThreadInfo[], stack trace limited to maxDepth elements. maxDepth=0
       means no stack trace. Entries are null for ids that are no longer
       live -- a thread can die between getAllThreadIds() and this call, so
       null entries are normal and MUST be skipped, not treated as an error.

ThreadInfo.getThreadName()  -> String
ThreadInfo.getThreadState() -> java.lang.Thread.State (an enum)

    Compare states as STRINGS via .name() or str(). thread_monitor/ is
    forbidden from importing java.lang.Thread (CLAUDE.md #1), and the enum
    would not survive the boundary anyway.

    ThreadInfo carries NO daemon flag. Per-pool daemon counts are therefore
    not available by this route; only the gateway-wide total is.

ThreadMXBean.getThreadCount()       -> int, live threads (daemon + non-daemon)
ThreadMXBean.getDaemonThreadCount() -> int, live daemon threads
ThreadMXBean.getPeakThreadCount()   -> int, peak since JVM start or last reset

    We never call resetPeakThreadCount(): it mutates state shared with every
    other tool looking at this JVM.

ThreadMXBean.findDeadlockedThreads()
    -> long[] of deadlocked thread ids, "if any; NULL otherwise."

    Returns null, NOT an empty array, when there is no deadlock. Under Jython
    that arrives as None, and `len(None)` raises. This is the single most
    common way this metric ends up either permanently zero or throwing every
    sample.

===========================================================================
system.* -- Ignition scripting API
===========================================================================

system.tag.writeBlocking(paths, values)
    -> list of QualityCode, one per path, in the same order.

    paths:  list of fully-qualified tag path strings
    values: list of values, same length

    Writes in one round trip and returns once complete. Chosen over
    writeAsync so the write is accounted for inside SampleDurationMs rather
    than completing invisibly after the sample returns.

    A path that does not exist does NOT raise -- it comes back as a bad
    QualityCode in the result list while the call itself succeeds. Silent
    unless the result is inspected, which tags.py does.

    Replaces system.tag.write, deprecated in 8.0.

    TODO(human): confirm whether 8.3 still accepts the 2-arg form. The 8.1
    docs also show writeBlocking(paths, values, timeout). Not used here.

system.tag.configure(basePath, tags, collisionPolicy)
    -> list of QualityCode.

    collisionPolicy: 'a' abort, 'o' overwrite, 'm' merge, 'd' delete-and-replace
    tags: list of dicts matching Ignition's tag JSON export shape.

    TODO(human): confirm signature and collision-policy letters on 8.3 before
    M3 provisioning runs against it. Verified on neither gateway yet.

system.date.now()
    -> java.util.Date. Used only for the LastSampleTime diagnostic tag.

===========================================================================
"""
import sys


# ---------------------------------------------------------------------------
# Gateway detection
# ---------------------------------------------------------------------------

try:
    import system
except ImportError:
    # Imported on CPython by the test suite, where there is no gateway.
    system = None


def on_gateway():
    """True when running inside Ignition with `system` available."""
    return system is not None


def attempt(strategies, notes, label):
    """Run strategies in order, take the first that yields something.

    Returns (value, name_of_strategy_that_worked). A strategy that raises or
    yields nothing is recorded in `notes` and the next is tried, because "this
    call does not exist on this version" and "this call exists and the answer
    is genuinely empty" have to be told apart later.

    Carried over from tag-history-linkage-scanner's gateway.py. The reason it
    is here rather than inlined: this project targets 8.1 and 8.3 across two
    JVM major versions, and the honest position on any given call is "probably
    identical, but ask and write down the answer" rather than "assume".
    """
    for name, strategy in strategies:
        try:
            value = strategy()
        except:  # noqa: E722 -- MUST stay bare. On Jython a failure from
                # system.* or java.* is a java.lang.Exception, which does NOT
                # subclass Python's Exception, so `except Exception` catches
                # nothing and the gateway timer dies. See CLAUDE.md #3.
            error = sys.exc_info()[1]
            notes.append("%s: %s failed -- %s: %s"
                         % (label, name, error.__class__.__name__, error))
            continue
        if value is not None:
            return (value, name)
        notes.append("%s: %s returned nothing" % (label, name))
    return (None, None)


# ---------------------------------------------------------------------------
# CPython test doubles
# ---------------------------------------------------------------------------


class FakeThreadInfo(object):
    """Stands in for java.lang.management.ThreadInfo."""

    def __init__(self, name, state):
        self._name = name
        self._state = state

    def getThreadName(self):
        return self._name

    def getThreadState(self):
        return FakeThreadState(self._state)


class FakeThreadState(object):
    """Stands in for java.lang.Thread.State, which stringifies to its name."""

    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name

    def __str__(self):
        return self._name


class FakeThreadMXBean(object):
    """Stands in for ThreadMXBean, including its sharp edges.

    Reproduces the two behaviours that are easy to get wrong and impossible to
    notice in a naive double:

    - `findDeadlockedThreads()` returns None, not [], when nothing is
      deadlocked.
    - `getThreadInfo()` may return None entries for threads that died between
      the id list and the info call.
    """

    def __init__(self, pairs, deadlocked=None, dead_ids=None, peak=None,
                 daemon=None):
        self._pairs = pairs
        self._deadlocked = deadlocked
        self._dead_ids = dead_ids or []
        self._peak = peak
        self._daemon = daemon

    def getAllThreadIds(self):
        ids = []
        for index in range(len(self._pairs)):
            ids.append(index + 1)
        for dead in self._dead_ids:
            ids.append(dead)
        return ids

    def getThreadInfo(self, ids, max_depth=0):
        infos = []
        for thread_id in ids:
            if thread_id in self._dead_ids:
                infos.append(None)  # died between the two calls
                continue
            name, state = self._pairs[thread_id - 1]
            infos.append(FakeThreadInfo(name, state))
        return infos

    def getThreadCount(self):
        return len(self._pairs)

    def getDaemonThreadCount(self):
        if self._daemon is None:
            return len(self._pairs) - 1
        return self._daemon

    def getPeakThreadCount(self):
        if self._peak is None:
            return len(self._pairs) + 5
        return self._peak

    def findDeadlockedThreads(self):
        return self._deadlocked  # None when healthy -- deliberately not []


class FakeTagSystem(object):
    """Stands in for system.tag, recording what was written."""

    def __init__(self, bad_paths=None):
        self.writes = []
        self.configured = []
        self._bad_paths = set(bad_paths or [])

    def writeBlocking(self, paths, values):
        self.writes.append((list(paths), list(values)))
        qualities = []
        for path in paths:
            qualities.append(FakeQuality(path not in self._bad_paths))
        return qualities

    def configure(self, base_path, tags, collision_policy):
        self.configured.append((base_path, tags, collision_policy))
        qualities = []
        for _tag in tags:
            qualities.append(FakeQuality(True))
        return qualities


class FakeQuality(object):
    """Stands in for QualityCode."""

    def __init__(self, good):
        self._good = good

    def isGood(self):
        return self._good

    def __str__(self):
        if self._good:
            return "Good"
        return "Bad_NotFound"
