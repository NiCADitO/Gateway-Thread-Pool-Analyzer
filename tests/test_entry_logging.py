"""The timer's log behaviour.

Before any tags exist, the gateway log is the ONLY evidence the timer is
running -- a timer script's return value is discarded by the gateway. So this
is load-bearing, not cosmetic, and it has one hard requirement pulling against
another: say enough to prove liveness, without saying the same thing 8,640
times a day.
"""
from ignition_adapter import entry, tags


def make_result(good=0, bad=0, error=""):
    result = tags.WriteResult()
    result.attempted = good + bad
    result.good = good
    result.error = error
    for index in range(bad):
        result.bad_paths.append("path%d (Bad_NotFound)" % (index,))
    return result


class Recorder(object):
    def __init__(self):
        self.lines = []

    def install(self):
        self._original = entry._log
        entry._log = lambda level, message: self.lines.append((level, message))
        entry._count[0] = 0
        entry._last_logged[0] = None

    def restore(self):
        entry._log = self._original


def run(results):
    """Feed a sequence of WriteResults through the log logic."""
    recorder = Recorder()
    recorder.install()
    try:
        for result in results:
            entry._count[0] += 1
            entry._log_sample(result, "summary/%s" % (entry._count[0],))
    finally:
        recorder.restore()
    return recorder.lines


def test_first_sample_always_logs():
    """A deploy must be confirmable in seconds, not after a heartbeat."""
    lines = run([make_result(good=69)])
    assert len(lines) == 1
    assert lines[0][0] == "info"


def test_a_persistent_fault_is_not_relogged_every_sample():
    """The unprovisioned-tags case: identical failure, forever."""
    lines = run([make_result(bad=69) for _ in range(20)])
    assert len(lines) == 1, lines
    assert lines[0][0] == "warn"
    assert "summary/1" in lines[0][1]


def test_a_steady_healthy_run_only_heartbeats():
    lines = run([make_result(good=69)
                 for _ in range(entry.HEARTBEAT_EVERY * 2)])
    # sample 1, then the two heartbeats
    assert len(lines) == 3, lines
    for level, _message in lines:
        assert level == "info"


def test_recovery_is_logged_immediately():
    """Going from broken to working must not wait for a heartbeat."""
    lines = run([make_result(bad=69)] * 5 + [make_result(good=69)])
    assert len(lines) == 2, lines
    assert lines[0][0] == "warn"
    assert lines[1][0] == "info"
    assert "recovered" in lines[1][1]


def test_a_different_fault_speaks_up():
    """'Broken differently' is news; 'broken the same way' is not."""
    lines = run([make_result(bad=69)] * 3
                + [make_result(bad=5, good=64)] * 3
                + [make_result(error="connection lost")] * 3)
    assert len(lines) == 3, lines
    for level, _message in lines:
        assert level == "warn"


def test_changing_thread_counts_do_not_defeat_deduplication():
    """The fault key must ignore the numbers.

    Thread counts change every single sample. Keying the log on the full
    summary text would make every sample look like a state change and
    reproduce exactly the flood this logic exists to prevent.
    """
    recorder = Recorder()
    recorder.install()
    try:
        # Stop one short of the heartbeat, so this measures deduplication
        # alone rather than deduplication plus the periodic liveness line.
        for index in range(entry.HEARTBEAT_EVERY - 1):
            entry._count[0] += 1
            entry._log_sample(make_result(bad=69),
                              "69 of 69 rejected (%d threads, 12ms)"
                              % (100 + index,))
    finally:
        recorder.restore()
    assert len(recorder.lines) == 1, recorder.lines


def test_a_persistent_fault_still_heartbeats():
    """Dedup must not mean total silence.

    If a broken timer went completely quiet, 'nothing in the log' would mean
    both 'healthy' and 'dead', which is the ambiguity this logging exists to
    remove. The heartbeat still fires through a persistent fault.
    """
    lines = run([make_result(bad=69) for _ in range(entry.HEARTBEAT_EVERY)])
    assert len(lines) == 2, lines
    assert [level for level, _ in lines] == ["warn", "warn"]


def test_fault_key_distinguishes_the_states_that_matter():
    assert entry._fault_key(make_result(good=69)) == "ok"
    assert entry._fault_key(make_result(bad=69)) != "ok"
    assert entry._fault_key(make_result(bad=5)) != \
        entry._fault_key(make_result(bad=69))
    assert entry._fault_key(make_result(error="x")).startswith("error:")
