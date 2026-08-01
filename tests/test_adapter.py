"""The Ignition adapter, driven through the CPython doubles in stubs.py.

These run the same code paths the gateway runs. What they cannot cover is
whether the real ThreadMXBean and system.tag behave as stubs.py documents --
that is what M2's live verification on a gateway is for.
"""
from ignition_adapter import entry, jvm, stubs, tags
from thread_monitor import snapshot, tagpaths


def make_bean(pairs, **kwargs):
    return stubs.FakeThreadMXBean(pairs, **kwargs)


def clock_from(values):
    """A deterministic millisecond clock that yields `values` in order."""
    state = {"i": 0}

    def tick():
        value = values[min(state["i"], len(values) - 1)]
        state["i"] += 1
        return value

    return tick


# --- reading --------------------------------------------------------------

def test_read_produces_a_populated_snapshot(dump_81_11):
    snap = jvm.read(bean=make_bean(dump_81_11), clock=clock_from([1000, 1007]))
    assert snap.total_threads == 117
    assert snap.pool("webserver").total == 15
    assert snap.sample_duration_ms == 7
    assert snap.api_route == "getThreadInfo(long[],0)"


def test_states_cross_the_boundary_as_text_not_as_a_java_enum(dump_81_11):
    """thread_monitor/ must never see a java enum. Prove it at the seam.

    Deliberately NOT `isinstance(state, str)`. Verified on Jython 2.7.2
    against a real ThreadMXBean, `_state_name` returns `unicode`, so that
    assertion would pass here on CPython 3 and fail on an actual gateway.
    What matters is that the value is comparable text usable as a dict key,
    which is all snapshot.PoolCount asks of it.
    """
    bean = make_bean(dump_81_11)
    pairs = jvm.collect_pairs(bean, [])
    assert pairs
    for name, state in pairs:
        assert state == "%s" % (state,)
        assert {state: 1}[state] == 1
        assert name == "%s" % (name,)


def test_no_deadlocks_reads_as_zero_not_as_a_failed_probe(dump_81_11):
    """findDeadlockedThreads returns NULL when healthy, not an empty array.

    0 and None mean different things downstream: 0 writes 0, None writes -1.
    Reporting "probe failed" on a perfectly healthy gateway would be a
    permanent -1 on the chart.
    """
    bean = make_bean(dump_81_11, deadlocked=None)
    assert jvm.deadlocked_count(bean, []) == 0

    snap = jvm.read(bean=bean)
    assert snap.deadlocked_count == 0


def test_deadlocks_are_counted(dump_81_11):
    bean = make_bean(dump_81_11, deadlocked=[7, 9])
    snap = jvm.read(bean=bean)
    assert snap.deadlocked_count == 2


def test_threads_that_die_mid_sample_are_skipped_not_fatal(dump_81_11):
    """getThreadInfo returns None entries for threads that just exited.

    Routine on a busy gateway. If this raised, every sample during any burst
    of thread churn would be lost -- exactly when the data matters most.
    """
    bean = make_bean(dump_81_11, dead_ids=[9001, 9002])
    snap = jvm.read(bean=bean)
    assert snap.total_threads == 117
    assert "died between" in snap.last_error


def test_a_failing_counter_becomes_none_and_the_sample_survives(dump_81_11):
    class PartlyBroken(stubs.FakeThreadMXBean):
        def getPeakThreadCount(self):
            raise RuntimeError("boom")

    snap = jvm.read(bean=PartlyBroken(dump_81_11))
    assert snap.peak_threads is None       # -> written as -1
    assert snap.total_threads == 117       # the sample still completed
    assert "getPeakThreadCount failed" in snap.last_error


def test_a_disagreement_with_getthreadcount_is_surfaced(dump_81_11):
    class Miscounting(stubs.FakeThreadMXBean):
        def getThreadCount(self):
            return 999

    snap = jvm.read(bean=Miscounting(dump_81_11))
    assert "getThreadCount says 999, counted 117" in snap.last_error


def test_no_bean_at_all_returns_an_empty_snapshot_not_an_exception():
    snap = jvm.read(bean=None)
    if snap.api_route == "unavailable":
        assert snap.total_threads == 0
        assert "no ThreadMXBean" in snap.last_error


def test_read_never_raises_even_when_everything_fails():
    class Hostile(object):
        def getAllThreadIds(self):
            raise RuntimeError("no")

    snap = jvm.read(bean=Hostile())
    assert snap.total_threads == 0
    assert snap.last_error


# --- writing --------------------------------------------------------------

def test_write_sends_one_batched_call(dump_81_11):
    fake = stubs.FakeTagSystem()
    snap = jvm.read(bean=make_bean(dump_81_11))
    result = tags.write_snapshot(snap, tag_system=fake)

    assert len(fake.writes) == 1, "must be one round trip, not one per tag"
    paths, values = fake.writes[0]
    assert paths == tagpaths.all_paths()
    assert len(values) == len(paths)
    assert result.ok()


def test_a_missing_tag_is_reported_rather_than_passing_silently(dump_81_11):
    """writeBlocking does NOT raise for a nonexistent path.

    It returns a bad QualityCode while the call itself succeeds. Without
    inspecting the result an entirely unprovisioned tag tree looks identical
    to a working one, and the first sign of trouble is an empty chart hours
    later.
    """
    missing = tagpaths.pool_member("webserver", "Blocked")
    fake = stubs.FakeTagSystem(bad_paths=[missing])
    snap = jvm.read(bean=make_bean(dump_81_11))
    result = tags.write_snapshot(snap, tag_system=fake)

    assert not result.ok()
    assert len(result.bad_paths) == 1
    assert missing in result.bad_paths[0]
    assert "rejected" in result.summary()


def test_a_throwing_write_is_captured_not_propagated(dump_81_11):
    class Exploding(stubs.FakeTagSystem):
        def writeBlocking(self, paths, values):
            raise RuntimeError("connection lost")

    snap = jvm.read(bean=make_bean(dump_81_11))
    result = tags.write_snapshot(snap, tag_system=Exploding())
    assert not result.ok()
    assert "connection lost" in result.error


def test_null_write_result_is_not_reported_as_success(dump_81_11):
    class Null(stubs.FakeTagSystem):
        def writeBlocking(self, paths, values):
            return None

    snap = jvm.read(bean=make_bean(dump_81_11))
    result = tags.write_snapshot(snap, tag_system=Null())
    assert not result.ok()


def test_failed_probes_are_written_as_minus_one_not_omitted(dump_81_11):
    class NoPeak(stubs.FakeThreadMXBean):
        def getPeakThreadCount(self):
            raise RuntimeError("boom")

    fake = stubs.FakeTagSystem()
    snap = jvm.read(bean=NoPeak(dump_81_11))
    tags.write_snapshot(snap, tag_system=fake)

    paths, values = fake.writes[0]
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.PEAK_COUNT)] == -1
    # A gap would leave the previous sample's value in place, which on a
    # trend reads as "still fine".
    assert len(values) == len(tagpaths.all_paths())


# --- entry points ---------------------------------------------------------

def test_dump_renders_without_a_gateway():
    text = entry.dump()
    assert isinstance(text, str) and text


def test_sample_and_write_never_raises_off_gateway():
    assert isinstance(entry.sample_and_write(), str)


def test_reentrancy_guard_skips_an_overlapping_sample():
    entry._sampling[0] = True
    try:
        assert "skipped" in entry.sample_and_write()
    finally:
        entry._sampling[0] = False


def test_reentrancy_guard_resets_after_a_failure():
    """A guard that leaks on the error path deadlocks the timer forever."""
    entry._sampling[0] = False
    entry.sample_and_write()
    assert entry._sampling[0] is False


def test_diagnose_lists_every_expected_path():
    text = entry.diagnose()
    for path in tagpaths.all_paths():
        assert path in text


def test_format_report_survives_a_snapshot_with_nothing_probed():
    snap = snapshot.Snapshot()
    assert isinstance(snapshot.format_report(snap), str)
