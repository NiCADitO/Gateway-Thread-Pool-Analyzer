"""What the gateway timer and the script console call. No logic of its own.

Three entry points:

    dump()              read and print. Writes nothing. Run this first.
    sample_and_write()  what the gateway timer calls, every 10 seconds.
    diagnose()          dump() plus the tag paths, for provisioning problems.

Jython 2.7: no f-strings, no comprehensions.
"""
import sys

from ignition_adapter import jvm
from ignition_adapter import tags
from thread_monitor import snapshot
from thread_monitor import tagpaths

try:
    import system
except ImportError:
    system = None


# Reentrancy guard. The gateway timer is configured Fixed Delay, so the next
# sample cannot start until this one returns and this should never trip -- but
# "should never" plus a shared JVM plus someone switching the timer to Fixed
# Rate in the Designer is exactly how two samples end up interleaving their
# writes. Cheap insurance for a wrong number that would be very hard to spot.
_sampling = [False]

# Last result, readable from the script console after the timer has been
# running. Saves provisioning a tag just to answer "is it working".
_last = [None]


def dump():
    """Read the gateway's threads and print a report. Writes nothing.

    This is M2's deliverable and the first thing to run on a new gateway:

        from ignition_adapter import entry
        print entry.dump()

    Run it from a GATEWAY scope -- the Script Console in the Designer runs in
    the Designer's own JVM, so it reports the Designer's threads, which look
    entirely plausible and are the wrong answer. Use a Gateway Event Script,
    a Perspective session (which runs in the gateway), or a WebDev endpoint.
    """
    snap = jvm.read()
    _last[0] = snap
    return snapshot.format_report(snap)


def sample_and_write():
    """Take one sample and write it to tags. What the gateway timer calls.

    Never raises. A gateway timer script that throws is disabled by the
    gateway, and the trend simply stops -- which reads on a chart as "the
    problem went away" rather than "the measurement died". Failing quietly to
    Diagnostics/LastError is the lesser evil, and the reason LastError is a
    historized tag rather than just a log line.
    """
    if _sampling[0]:
        return "skipped: previous sample still running"
    _sampling[0] = True
    try:
        try:
            snap = jvm.read()
            _last[0] = snap
            result = tags.write_snapshot(snap)
            return result.summary()
        except:  # noqa: E722 -- bare: see CLAUDE.md #3.
            return "sample failed: %s" % (sys.exc_info()[1],)
    finally:
        _sampling[0] = False


def last_report():
    """The most recent snapshot, formatted. None if nothing has run yet."""
    if _last[0] is None:
        return "no sample taken yet"
    return snapshot.format_report(_last[0])


def diagnose():
    """dump(), plus every tag path this expects to exist.

    For the case where the timer reports writes rejected: the tag tree does
    not match what the code wants, and this prints exactly what it wants.
    """
    lines = [dump(), "", "Expects %d tags:" % (len(tagpaths.all_paths()),)]
    for path in tagpaths.all_paths():
        lines.append("  " + path)
    return "\n".join(lines)
