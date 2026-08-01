"""The counting algorithm. The whole of it.

Takes a list of (thread_name, state_name) string pairs and returns a Snapshot.
No Ignition, no Java, no clock, no I/O -- which is what lets the entire
catalog be tested against real captured thread dumps with no gateway running.

Jython 2.7: no f-strings, no comprehensions.
"""
from thread_monitor import snapshot
from thread_monitor import taxonomy


def classify(name, specs=None):
    """The key of the first PoolSpec that matches `name`.

    First match wins, and taxonomy.POOL_SPECS ends with a catch-all, so this
    always returns a key.
    """
    if specs is None:
        specs = taxonomy.POOL_SPECS
    for spec in specs:
        if spec.matches(name):
            return spec.key
    return taxonomy.OTHER_KEY


def count(samples, specs=None):
    """Bucket and count `samples`.

    samples: list of (thread_name, state_name) pairs. state_name is the
             java.lang.Thread.State name as a string -- 'RUNNABLE',
             'TIMED_WAITING' and so on. Strings rather than the Java enum on
             purpose: it keeps this module free of any java.* import and
             makes the fixtures plain text.

    Returns a Snapshot with one PoolCount per spec, in catalog order --
    including buckets that counted zero, because a pool that drops to zero is
    itself a signal and a missing series on a chart is not.
    """
    if specs is None:
        specs = taxonomy.POOL_SPECS

    snap = snapshot.Snapshot()

    by_key = {}
    for spec in specs:
        entry = snapshot.PoolCount(spec.key)
        by_key[spec.key] = entry
        snap.pools.append(entry)

    for pair in samples:
        name = pair[0]
        state = pair[1]

        key = classify(name, specs)
        by_key[key].add(state)
        snap.total_threads = snap.total_threads + 1

        if key == taxonomy.OTHER_KEY:
            if len(snap.unmatched) < snapshot.UNMATCHED_LIMIT:
                if name not in snap.unmatched:
                    snap.unmatched.append(name)

    return snap
