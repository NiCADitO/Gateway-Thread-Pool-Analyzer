"""Tag paths, and the write list that has to stay in step with them."""
from thread_monitor import sampler, snapshot, tagpaths, taxonomy


def test_all_paths_covers_every_pool_and_member():
    paths = tagpaths.all_paths()
    expected = (len(taxonomy.POOL_SPECS) * len(tagpaths.UDT_MEMBERS)
                + len(tagpaths.GATEWAY_TAGS)
                + len(tagpaths.DIAGNOSTIC_TAGS))
    assert len(paths) == expected


def test_all_paths_are_unique():
    paths = tagpaths.all_paths()
    assert len(paths) == len(set(paths))


def test_paths_are_fully_qualified():
    for path in tagpaths.all_paths():
        assert path.startswith("[default]GatewayHealth/Threads/"), path


def test_write_list_matches_all_paths_exactly(dump_81_11):
    """flatten_for_write and all_paths must not drift apart.

    Provisioning creates all_paths(); the timer writes flatten_for_write().
    If they disagree, the timer writes to a tag that does not exist -- which
    on 8.1 is a silent BadRequest per path inside an otherwise-successful
    batched call.
    """
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    assert paths == tagpaths.all_paths()
    assert len(values) == len(paths)


def test_unprobed_adapter_fields_write_minus_one(dump_81_11):
    """A failed probe must read as -1, never as a stale value or a gap."""
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.PEAK_COUNT)] == -1
    assert by_path[tagpaths.gateway_tag(tagpaths.DEADLOCKED_COUNT)] == -1
    assert by_path[tagpaths.diagnostic_tag(tagpaths.SAMPLE_DURATION_MS)] == -1


def test_populated_adapter_fields_are_written_through(dump_81_11):
    snap = sampler.count(dump_81_11)
    snap.peak_threads = 140
    snap.daemon_threads = 100
    snap.deadlocked_count = 0
    snap.sample_duration_ms = 3
    snap.api_route = "getThreadInfo(long[],int)"
    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.PEAK_COUNT)] == 140
    assert by_path[tagpaths.gateway_tag(tagpaths.DEADLOCKED_COUNT)] == 0
    assert by_path[tagpaths.diagnostic_tag(tagpaths.API_ROUTE)] == \
        "getThreadInfo(long[],int)"


def test_total_count_written_matches_the_sample(dump_81_11):
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.TOTAL_COUNT)] == 117


def test_state_members_map_to_real_thread_states():
    valid = set(snapshot.ALL_STATES)
    for _member, state in tagpaths.STATE_MEMBERS:
        assert state in valid, state


def test_count_is_the_first_udt_member():
    """Order is the chart's column order and the write order. Keep Count first."""
    assert tagpaths.UDT_MEMBERS[0] == tagpaths.COUNT_MEMBER


def test_format_report_renders(dump_81_11):
    snap = sampler.count(dump_81_11)
    text = snapshot.format_report(snap)
    assert "webserver" in text
    assert "117 total" in text
