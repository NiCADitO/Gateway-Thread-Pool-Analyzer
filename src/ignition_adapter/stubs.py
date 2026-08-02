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

    Replaces system.tag.write, deprecated in 8.0. CONFIRMED by reflection on
    both gateways: the 2-arg form is still valid on 8.3.8 (the third arg is an
    optional timeout, default 45000 ms). Note 8.3 REMOVED read/readAll/write/
    writeAll/writeSynchronous/writeAllSynchronous entirely -- writeBlocking is
    the only portable route, which is what this project already uses.

system.tag.configure(basePath, tags, collisionPolicy)
    -> java.util.List of QualityCode, one per tag created or edited.

    Signature read by Jython reflection inside BOTH containers, off
    AbstractTagUtilities: @KeywordArgs(names={"basePath","tags",
    "collisionPolicy"}, types={String, Object, String}). Byte-for-byte
    identical on 8.1.11 and 8.3.8.

    collisionPolicy is matched CASE-INSENSITIVELY ON THE FIRST CHARACTER
    against the enum names, so the legal letters are exactly:

        'a'  Abort
        'o'  Overwrite       <- the default when the arg is omitted
        'i'  Ignore
        'm'  MergeOverwrite
        'r'  Rename

    'd' is NOT a collision policy. Passing it throws
    java.lang.IllegalArgumentException; passing "" throws
    StringIndexOutOfBoundsException. An earlier version of this file
    documented "'d' delete-and-replace", which was wrong and would have
    thrown on a customer's gateway -- exactly the failure mode this inventory
    exists to prevent.

    tags: list of dicts. Nesting is under the key "tags" (a list).

    *** tagType DOES NOT VALIDATE. ***
    TagObjectType.fromString() returns Unknown for an unrecognised string
    rather than throwing. "UDTType", "Memory" or "atomictag " all silently
    become Unknown instead of failing. The legal names are Unknown, Property,
    Node, Folder, AtomicTag, UdtInstance, UdtType, TagModel, Provider (plus
    legacy aliases udt_def, udt_inst, scalar, standard). Only ever pass a
    constant from provisioning.py -- never a computed string.

    dataType (identical on both): Int1 Int2 Int4 Int8 Float4 Float8 Boolean
    String DateTime Text and the *Array variants, ByteArray, DataSet,
    Document. Defaults to Int4.

    8.3-ONLY memory-tag properties -- do NOT send these to an 8.1 gateway:
    defaultValue, valuePersistence.

system.tag.exists(tagPath)
    -> boolean.

    VERIFIED BY REFLECTION on both gateways: present on 8.1.11, and on 8.3.8
    it reflects as exists(1 args) -> boolean. Read-only.

    This is provisioning's INDEPENDENT gate. QualityCodes say what `configure`
    thought it did; `exists` says what is actually there. Checking only the
    former means a single misread return value can certify a tag tree that
    was never created -- and the symptom is an empty chart hours later.

    Note it must NOT be used as a write-probe substitute: an earlier design
    proposed proving existence by writing a sentinel, which would have
    scribbled -1 into all 65 live metric tags on every provisioning run.

system.tag.getConfiguration(basePath, recursive)
    -> list of dicts describing the tags at basePath.

    TODO(human): the exact SHAPE of the returned objects is not confirmed on
    either gateway -- whether values are plain dicts, or objects needing an
    accessor, and whether history properties come back at all when they are
    inherited rather than overridden. 8.3 also adds a third keyword arg
    `overridesOnly`. provisioning.py therefore does NOT rely on reading
    configuration back to decide success; see its module docstring.

===========================================================================
Tag history properties -- read out of TagHistoryProps in BOTH gateways' jars
===========================================================================

8.1.11 has exactly these 13. 8.3.8 has the same 13 plus `includeMetadata`
(left unset here so one payload works on both).

    historyEnabled              Boolean, default False
    historyProvider             String,  default ""   <- must be set explicitly
    sampleMode                  OnChange | Periodic | TagGroup
    historySampleRate           Integer
    historySampleRateUnits      TimeUnits
    historicalDeadband          Float,   default 0.0
    historicalDeadbandMode      Absolute | Percent
    historicalDeadbandStyle     Auto | Analog_Compressed | Discrete
    historyTagGroup             String
    historyMaxAge               Integer, default 0 (= disabled)
    historyMaxAgeUnits          TimeUnits
    historyTimeDeadband         Integer, default 1
    historyTimeDeadbandUnits    TimeUnits

    TimeUnits: MS SEC MIN HOUR DAY WEEK MONTH YEAR

    *** THE NAMING TRAPS, all confirmed against the jars: ***
    - It is `historySampleRate`, NOT `historicalSampleRate`.
    - But the deadbands ARE `historical*`: historicalDeadband,
      historicalDeadbandMode, historicalDeadbandStyle.
    - There is NO key `maxTimeBetweenSamples`. The Designer field
      "Max Time Between Samples" is `historyMaxAge` (+ Units), and
      "Min Time Between Samples" is `historyTimeDeadband` (+ Units).
    - The Designer dropdown labelled "Analog" is the constant
      `Analog_Compressed`, not "Analog".

    A wrong key here is silently DROPPED -- no error, no bad QualityCode --
    and the trend is empty hours later. That is why these were read out of
    the gateway's own bytecode rather than from documentation.

    `sampleMode` is a HISTORY property despite its generic name.

===========================================================================
Other system.* calls
===========================================================================

system.date.now()
    -> java.util.Date. Used only for the LastSampleTime diagnostic tag.

system.dataset.toDataSet(headers, data)
    -> com.inductiveautomation.ignition.common.Dataset

    VERIFIED BY REFLECTION on both gateways. DatasetUtilities exposes exactly
    two overloads, and they resolve identically on 2.1.11 and 3.3.8:

        toDataSet(org.python.core.PySequence, org.python.core.PySequence)
        toDataSet(Dataset)          <- 8.1 types this as its PyDataSet subclass

    The TWO-ARGUMENT form is the one this project wants: headers as a sequence
    of strings, data as a sequence of row sequences. Both args are PySequence,
    so a Python list of lists is exactly right and no Java array conversion is
    needed.

    Column TYPES are inferred from the values, not declared. That is why
    snapshot.pool_table() returns real ints rather than pre-formatted strings:
    a column of strings would sort "10" before "9" in the Perspective table.

    The resulting Dataset is written to a tag whose dataType is DataSet -- see
    the enum note under `dataType` above, where DataSet is a confirmed
    constant on both gateways.

system.util.getLogger(name)
    -> LoggerEx, with .trace/.debug/.info/.warn/.error(String).

    Output goes to the gateway's wrapper log, which the container symlinks to
    /dev/stdout -- so `docker logs <container>` shows it. That is the ONLY
    channel this project has for observing the gateway timer before any tags
    exist, which is why sample_and_write() logs rather than only returning a
    string (a timer script's return value is discarded).

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


class FakeDataset(object):
    """Stands in for a com.inductiveautomation.ignition.common.Dataset.

    Only carries what the tests need to assert on. It deliberately does NOT
    try to imitate Dataset's API -- nothing in this project reads a dataset
    back, it only builds one and hands it to writeBlocking.
    """

    def __init__(self, headers, rows):
        self.headers = list(headers)
        # Written out rather than a comprehension: stubs.py lives under src/,
        # so it is Jython 2.7 like everything else there (CLAUDE.md #2).
        self.rows = []
        for row in rows:
            self.rows.append(list(row))

    def getColumnCount(self):
        return len(self.headers)

    def getRowCount(self):
        return len(self.rows)

    def __repr__(self):
        return "FakeDataset(%dx%d)" % (len(self.rows), len(self.headers))


class FakeDatasetSystem(object):
    """Stands in for system.dataset."""

    def toDataSet(self, headers, data):
        # The real one raises if a row is the wrong width, so this does too --
        # a ragged dataset is the mistake most likely to be made here, and it
        # should fail in the test suite rather than on a gateway.
        for row in data:
            if len(row) != len(headers):
                raise ValueError(
                    "row has %d values, expected %d" % (len(row),
                                                        len(headers)))
        return FakeDataset(headers, data)


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
