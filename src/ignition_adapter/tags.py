"""Writing to tags. The only module that calls system.tag.

Jython 2.7: no f-strings, no comprehensions.
"""
import sys

from thread_monitor import snapshot

try:
    import system
except ImportError:
    system = None  # CPython test suite


class WriteResult(object):
    """What a write attempt did, in enough detail to diagnose it.

    `bad_paths` matters more than it looks. system.tag.writeBlocking does NOT
    raise for a path that does not exist -- the call succeeds and that entry
    comes back with a bad QualityCode. Without inspecting the result, a
    completely unprovisioned tag tree looks exactly like a working one, and
    the first sign of trouble is an empty chart hours later.
    """

    def __init__(self):
        self.attempted = 0
        self.good = 0
        self.bad_paths = []
        self.error = ""

    def ok(self):
        return not self.error and not self.bad_paths

    def summary(self):
        if self.error:
            return "write failed: %s" % (self.error,)
        if self.bad_paths:
            return "%d of %d writes rejected, first: %s" % (
                len(self.bad_paths), self.attempted, self.bad_paths[0])
        return "%d tags written" % (self.good,)


def write_snapshot(snap, tag_system=None):
    """Write one Snapshot to its tags in a single batched call.

    One writeBlocking for the whole sample rather than one per tag: ~69 round
    trips per sample at a 10 second cadence would be a self-inflicted load
    problem on the very thing being measured.
    """
    result = WriteResult()

    target = tag_system
    if target is None:
        if system is None:
            result.error = "no `system` available -- not on a gateway"
            return result
        target = system.tag

    paths, values = snapshot.flatten_for_write(snap)
    result.attempted = len(paths)

    try:
        qualities = target.writeBlocking(paths, values)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        result.error = "%s" % (sys.exc_info()[1],)
        return result

    if qualities is None:
        # Undocumented, but a null result is not the same as success and
        # should not be reported as it.
        result.error = "writeBlocking returned null for %d paths" % (
            len(paths),)
        return result

    index = 0
    for quality in qualities:
        path = paths[index] if index < len(paths) else "?"
        index = index + 1
        if _is_good(quality):
            result.good = result.good + 1
        else:
            result.bad_paths.append("%s (%s)" % (path, quality))
    return result


def _is_good(quality):
    """True if this QualityCode is good.

    isGood() is the documented accessor. The string fallback exists because a
    quality object that cannot be interrogated should not be counted as a
    successful write -- failing closed here means a provisioning mistake shows
    up immediately instead of as a mysteriously empty trend.
    """
    try:
        return bool(quality.isGood())
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        return "%s" % (quality,) == "Good"
