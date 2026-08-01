"""Counting logic, driven by real captured gateway thread dumps.

The synthetic tests pin the invariants; the fixture-driven tests prove the
catalog actually works against a live 8.1 gateway's threads rather than
against names I imagined.
"""
from thread_monitor import sampler, snapshot, taxonomy


# --- invariants -----------------------------------------------------------

def test_every_thread_lands_in_exactly_one_bucket(dump_pairs):
    snap = sampler.count(dump_pairs)
    bucket_sum = sum(entry.total for entry in snap.pools)
    assert bucket_sum == len(dump_pairs)
    assert snap.total_threads == len(dump_pairs)


def test_pool_totals_equal_sum_of_their_states(dump_pairs):
    snap = sampler.count(dump_pairs)
    for entry in snap.pools:
        assert entry.total == sum(entry.states.values()), entry.key


def test_every_bucket_is_present_even_when_empty(dump_pairs):
    """A pool that drops to zero must still write a zero, not vanish.

    A missing series on a Power Chart looks like "no data yet", which is the
    opposite of what a pool collapsing to zero threads actually means.
    """
    snap = sampler.count(dump_pairs)
    assert [e.key for e in snap.pools] == taxonomy.spec_keys()


def test_unknown_state_still_counts_toward_the_total():
    snap = sampler.count([("webserver-1", "SOMETHING_NEW")])
    pool = snap.pool("webserver")
    assert pool.total == 1
    assert pool.state("SOMETHING_NEW") == 1


def test_unmatched_names_are_captured_and_capped():
    pairs = [("mystery-pool-%d" % i, "RUNNABLE") for i in range(50)]
    snap = sampler.count(pairs)
    assert snap.pool("other").total == 50
    assert len(snap.unmatched) == snapshot.UNMATCHED_LIMIT


def test_unmatched_names_are_deduplicated():
    pairs = [("mystery", "RUNNABLE")] * 20
    snap = sampler.count(pairs)
    assert snap.unmatched == ["mystery"]


def test_empty_sample_is_not_an_error():
    snap = sampler.count([])
    assert snap.total_threads == 0
    assert snap.pools  # all buckets still present
    assert snap.unmatched == []


# --- the real 8.1.11 gateway ----------------------------------------------

def test_matches_the_gateways_own_thread_count(dump_81_11):
    """117 is what the 8.1.11 JVM reported in its own SMR thread list."""
    snap = sampler.count(dump_81_11)
    assert snap.total_threads == 117


def test_the_catalog_recognises_essentially_everything(dump_81_11):
    """`other` is the discovery signal, so it must be near-empty in practice.

    If this fails after capturing a fresh dump, that is the tool working:
    read the unmatched names and add a PoolSpec.
    """
    snap = sampler.count(dump_81_11)
    assert snap.pool("other").total == 0, snap.unmatched


def test_known_pools_have_the_threads_we_expect(dump_81_11):
    snap = sampler.count(dump_81_11)
    # 13 webserver-N workers plus 2 named acceptor threads.
    assert snap.pool("webserver").total == 15
    # 12 gateway-shared-exec-engine + 2 platform-executor + 1 ForkJoinPool
    # + 2 DefaultDispatcher-worker.
    assert snap.pool("executor").total == 17
    assert snap.pool("opcua").total == 13
    assert snap.pool("perspective").total == 3
    # 8 store-and-forward (2 engines x 4 datasources) + 1 tags-history.
    assert snap.pool("history").total == 9
    assert snap.pool("scripting").total == 2


def test_state_totals_match_the_dump(dump_81_11):
    snap = sampler.count(dump_81_11)
    assert snap.state_total("RUNNABLE") == 27
    assert snap.state_total("WAITING") == 34
    assert snap.state_total("TIMED_WAITING") == 56
    assert snap.state_total("BLOCKED") == 0


def test_vm_internal_threads_are_absent_from_the_fixture(dump_81_11):
    """ThreadMXBean does not report them, so the fixture must not either.

    If a future capture lets 'GC Thread#0' or 'VM Thread' into a fixture, the
    counts stop being what a live sample would produce and every expected
    total above becomes quietly wrong.
    """
    names = [name for name, _state in dump_81_11]
    for forbidden in ("GC Thread#0", "VM Thread", "VM Periodic Task Thread",
                      "G1 Refine#0"):
        assert forbidden not in names


# --- classify -------------------------------------------------------------

def test_classify_is_first_match_wins():
    assert sampler.classify("webserver-42") == "webserver"
    assert sampler.classify("webserver-59-acceptor-0@abc") == "webserver"
    assert sampler.classify("gateway-storeforward-pipeline[X]-engine[Y]") == \
        "history"
    assert sampler.classify("milo-netty-event-loop-3") == "opcua"


def test_classify_falls_through_to_other():
    assert sampler.classify("something-nobody-has-seen") == "other"


def test_no_bare_gateway_catch_all_exists():
    """A bare 'gateway-' prefix would swallow every subsystem below it.

    Unknown gateway threads must reach `other` so Diagnostics/UnmatchedNames
    stays a working discovery mechanism. See taxonomy.py's module docstring.
    """
    assert sampler.classify("gateway-brand-new-subsystem-1") == "other"
