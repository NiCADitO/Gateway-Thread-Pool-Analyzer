"""Tag paths, and the write list that has to stay in step with them."""
from thread_monitor import sampler, snapshot, tagpaths, taxonomy


def test_all_paths_covers_every_pool_and_member():
    paths = tagpaths.all_paths()
    expected = (len(taxonomy.POOL_SPECS) * len(tagpaths.UDT_MEMBERS)
                + len(tagpaths.GATEWAY_TAGS)
                + len(tagpaths.DIAGNOSTIC_TAGS)
                + len(tagpaths.DATASET_TAGS))
    assert len(paths) == expected


def test_all_paths_is_exactly_the_scalars_plus_the_datasets():
    """Nothing may exist in one list and not the other.

    Provisioning creates all_paths(); the timer writes scalar_paths() plus the
    datasets. A path that fell out of both would be created and never written,
    which reads on a chart as a flat line rather than as a bug.
    """
    assert tagpaths.all_paths() == (tagpaths.scalar_paths()
                                    + tagpaths.dataset_paths())


def test_the_pool_table_is_a_dataset_and_is_not_historized():
    """A DataSet in the historian is a blob nothing can chart.

    Every number in it is already one of the 65 historized scalars it was
    assembled from, so historizing it would duplicate all of them and trend
    none of them.
    """
    path = tagpaths.gateway_tag(tagpaths.POOL_TABLE)
    assert tagpaths.datatype_for(path) == tagpaths.DATATYPE_DATASET
    assert path not in tagpaths.historized_paths()
    assert path in tagpaths.all_paths()
    assert path not in tagpaths.scalar_paths()


def test_all_paths_are_unique():
    paths = tagpaths.all_paths()
    assert len(paths) == len(set(paths))


def test_paths_are_fully_qualified():
    for path in tagpaths.all_paths():
        assert path.startswith("[default]GatewayHealth/Threads/"), path


def test_write_list_matches_scalar_paths_exactly(dump_81_11):
    """flatten_for_write and scalar_paths must not drift apart.

    Provisioning creates all_paths(); the timer writes flatten_for_write()
    plus the datasets. If they disagree, the timer writes to a tag that does
    not exist -- which on 8.1 is a silent BadRequest per path inside an
    otherwise-successful batched call.
    """
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    assert paths == tagpaths.scalar_paths()
    assert len(values) == len(paths)


# --- the per-pool table ---------------------------------------------------

def test_pool_table_has_one_row_per_pool_in_catalog_order(dump_81_11):
    snap = sampler.count(dump_81_11)
    headers, rows = snapshot.pool_table(snap)
    assert headers == snapshot.TABLE_HEADERS
    assert len(rows) == len(taxonomy.POOL_SPECS)
    keys = []
    for row in rows:
        keys.append(row[0])
    assert keys == taxonomy.spec_keys()


def test_pool_table_rows_are_all_the_width_of_the_header(dump_81_11):
    """A ragged row is what system.dataset.toDataSet rejects."""
    snap = sampler.count(dump_81_11)
    headers, rows = snapshot.pool_table(snap)
    for row in rows:
        assert len(row) == len(headers)


def test_pool_table_numbers_are_ints_not_formatted_strings(dump_81_11):
    """Column types are inferred from the values.

    A column of strings sorts "10" before "9" in the Perspective table, and
    the sort is a click away from anyone reading it.
    """
    snap = sampler.count(dump_81_11)
    _headers, rows = snapshot.pool_table(snap)
    for row in rows:
        assert isinstance(row[0], str)
        for value in row[1:]:
            assert isinstance(value, int)


def test_pool_table_agrees_with_the_tags_written_from_the_same_snapshot(
        dump_81_11):
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    _headers, rows = snapshot.pool_table(snap)
    for row in rows:
        key = row[0]
        assert by_path[tagpaths.pool_member(key, "Count")] == row[1]
        assert by_path[tagpaths.pool_member(key, "Runnable")] == row[2]
        assert by_path[tagpaths.pool_member(key, "Blocked")] == row[3]
        assert by_path[tagpaths.pool_member(key, "Waiting")] == row[4]
        assert by_path[tagpaths.pool_member(key, "TimedWaiting")] == row[5]


def test_pool_table_row_total_matches_the_gateway_total(dump_81_11):
    snap = sampler.count(dump_81_11)
    _headers, rows = snapshot.pool_table(snap)
    total = 0
    for row in rows:
        total = total + row[1]
    assert total == snap.total_threads


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


# --- BlockedTotal ---------------------------------------------------------

def test_blocked_total_is_the_sum_of_every_pool(dump_81_11):
    """Derived from the same snapshot, so it cannot disagree with the pools.

    A separately-probed total could drift out of step with the per-pool tags
    by one sample, and then the tile and the chart tell different stories at
    exactly the moment someone is trying to diagnose something.
    """
    snap = sampler.count(dump_81_11)
    # Force some blocked threads across two different pools.
    snap.pool("webserver").states["BLOCKED"] = 2
    snap.pool("executor").states["BLOCKED"] = 3

    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.BLOCKED_TOTAL)] == 5


def test_blocked_total_is_zero_on_a_healthy_gateway(dump_81_11):
    snap = sampler.count(dump_81_11)
    paths, values = snapshot.flatten_for_write(snap)
    by_path = dict(zip(paths, values))
    assert by_path[tagpaths.gateway_tag(tagpaths.BLOCKED_TOTAL)] == 0


def test_blocked_total_is_historized():
    """It is the tag you would alarm on, so it needs history behind it."""
    assert tagpaths.gateway_tag(tagpaths.BLOCKED_TOTAL) in \
        tagpaths.historized_paths()
