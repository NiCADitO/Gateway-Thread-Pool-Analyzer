"""Writing to tags. The only module that calls system.tag.

Jython 2.7: no f-strings, no comprehensions.
"""
import sys

from thread_monitor import snapshot
from thread_monitor import tagpaths

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
        # Set when the PoolTable DataSet could not be built. Kept separate
        # from `error` on purpose: the 80 scalar tags are what the trend runs
        # on, and they may well have written perfectly. Folding a table
        # failure into `error` would report a healthy pipeline as dead.
        self.dataset_error = ""

    def ok(self):
        return not self.error and not self.bad_paths and not self.dataset_error

    def summary(self):
        if self.error:
            return "write failed: %s" % (self.error,)
        if self.bad_paths:
            return "%d of %d writes rejected, first: %s" % (
                len(self.bad_paths), self.attempted, self.bad_paths[0])
        if self.dataset_error:
            return "%d tags written, but the pool table was skipped: %s" % (
                self.good, self.dataset_error)
        return "%d tags written" % (self.good,)


def build_pool_dataset(snap, dataset_system=None):
    """Turn the snapshot's per-pool rows into a real Ignition Dataset.

    `system.dataset.toDataSet(headers, rows)` -- verified by reflection on
    BOTH gateways, where it resolves to the same
    `toDataSet(PySequence, PySequence)` overload. (There is a second overload
    taking a single Dataset; the two-argument form is the one wanted here.)

    Returns (dataset, error). A None dataset with a non-empty error means the
    scalar write should still go ahead without it.
    """
    target = dataset_system
    if target is None:
        if system is None:
            return None, "no `system` available -- not on a gateway"
        target = system.dataset

    headers, rows = snapshot.pool_table(snap)
    try:
        return target.toDataSet(headers, rows), ""
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        return None, "%s" % (sys.exc_info()[1],)


def write_snapshot(snap, tag_system=None, dataset_system=None):
    """Write one Snapshot to its tags in a single batched call.

    One writeBlocking for the whole sample rather than one per tag: ~81 round
    trips per sample at a 10 second cadence would be a self-inflicted load
    problem on the very thing being measured.

    The PoolTable DataSet rides along in the SAME batch rather than getting a
    second writeBlocking. That is not just tidiness: two calls could land
    either side of the next sample, and then the table on screen would be
    describing a different instant from the tiles above it.
    """
    result = WriteResult()

    target = tag_system
    if target is None:
        if system is None:
            result.error = "no `system` available -- not on a gateway"
            return result
        target = system.tag

    paths, values = snapshot.flatten_for_write(snap)

    dataset, dataset_error = build_pool_dataset(snap, dataset_system)
    if dataset is None:
        # Skipped, not fatal. The table goes stale; the 80 trended tags -- the
        # entire reason this project exists -- still get written.
        result.dataset_error = dataset_error
    else:
        paths = paths + tagpaths.dataset_paths()
        values = values + [dataset]

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
