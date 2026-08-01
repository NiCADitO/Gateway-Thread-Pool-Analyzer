"""Tag provisioning.

The failure this suite is really guarding against is not "provisioning threw"
-- that is loud and easy. It is "provisioning reported success and the tags
are wrong", because that surfaces as an empty chart hours later with every
signal saying healthy.
"""
from ignition_adapter import provisioning, stubs
from thread_monitor import tagpaths, taxonomy

PROVIDER = "PostgresDBConnection"


class RecordingTags(stubs.FakeTagSystem):
    """Records configure() calls and can be told to reject specific tags."""

    def __init__(self, bad_names=None, short_by=0, return_null=False,
                 raise_on=None):
        stubs.FakeTagSystem.__init__(self)
        self._bad_names = set(bad_names or [])
        self._short_by = short_by
        self._return_null = return_null
        self._raise_on = raise_on

    def configure(self, base_path, tags, collision_policy):
        self.configured.append((base_path, tags, collision_policy))
        if self._raise_on and self._raise_on in base_path:
            raise RuntimeError("gateway said no")
        if self._return_null:
            return None
        qualities = []
        for tag in tags:
            qualities.append(stubs.FakeQuality(
                tag["name"] not in self._bad_names))
        if self._short_by:
            qualities = qualities[:-self._short_by] or []
        return qualities

    def created_paths(self):
        """Every leaf path this fake was asked to create."""
        paths = []
        for base_path, tags, _policy in self.configured:
            for tag in tags:
                if tag["tagType"] == provisioning.ATOMIC:
                    paths.append(base_path + "/" + tag["name"])
        return paths

    def tag_at(self, path):
        for base_path, tags, _policy in self.configured:
            for tag in tags:
                if base_path + "/" + tag["name"] == path:
                    return tag
        return None


# --- the happy path, and that it is actually complete --------------------

def test_creates_exactly_the_paths_the_writer_writes():
    """The whole point. all_paths() is what the timer writes every 10s.

    If provisioning creates a different set, the timer writes to a tag that
    does not exist -- which does NOT raise, it comes back as a bad
    QualityCode inside an otherwise-successful call.
    """
    fake = RecordingTags()
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert result.ok(), result.summary()
    assert sorted(fake.created_paths()) == sorted(tagpaths.all_paths())


def test_reports_the_right_tag_count():
    fake = RecordingTags()
    result = provisioning.provision(PROVIDER, tag_system=fake)
    # 69 leaves + the folders (GatewayHealth, Threads, Pools, Diagnostics,
    # and one per pool bucket).
    assert result.tag_count() == len(tagpaths.all_paths()) + 4 + \
        len(taxonomy.POOL_SPECS)


def test_uses_a_legal_collision_policy():
    """'d' is not a collision policy and throws java.lang.IllegalArgument.

    This repo documented 'd' for a while. The legal letters are a/o/i/m/r.
    """
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)
    for _base, _tags, policy in fake.configured:
        assert policy in ("a", "o", "i", "m", "r"), policy


def test_folders_are_created_before_their_contents():
    """configure is not documented to create intermediate folders."""
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)

    order = []
    for base_path, tags, _policy in fake.configured:
        for tag in tags:
            order.append((base_path + "/" + tag["name"], tag["tagType"]))

    seen_folders = set()
    for path, tag_type in order:
        if tag_type == provisioning.FOLDER:
            seen_folders.add(path)
            continue
        parent = path.rsplit("/", 1)[0]
        assert parent in seen_folders, "leaf %s before its folder" % (path,)


def test_every_configure_call_sends_a_flat_list():
    """Nested payloads make the QualityCode mapping ambiguous.

    It is unconfirmed whether a nested call returns one quality per top-level
    dict or one per leaf, so this code never nests and the 1:1 mapping holds.
    """
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)
    for _base, tags, _policy in fake.configured:
        for tag in tags:
            assert "tags" not in tag, "nested payload: %s" % (tag["name"],)


# --- history --------------------------------------------------------------

def test_history_is_on_for_the_64_trended_tags_and_off_for_diagnostics():
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)

    historized = set(tagpaths.historized_paths())
    assert len(historized) == 64

    for path in tagpaths.all_paths():
        tag = fake.tag_at(path)
        assert tag is not None, path
        if path in historized:
            assert tag.get("historyEnabled") is True, path
        else:
            assert "historyEnabled" not in tag, path


def test_history_uses_the_verified_key_names():
    """A wrong key here is silently dropped and the trend is empty.

    These spellings were read out of TagHistoryProps in both gateways' jars.
    The trap is that the sample-rate key is `history*` while the deadband
    keys are `historical*`, and that `maxTimeBetweenSamples` does not exist.
    """
    block = provisioning.history_block(PROVIDER)
    for key in block:
        assert key in (
            "historyEnabled", "historyProvider", "sampleMode",
            "historySampleRate", "historySampleRateUnits",
            "historicalDeadband", "historicalDeadbandMode",
            "historicalDeadbandStyle", "historyTagGroup",
            "historyMaxAge", "historyMaxAgeUnits",
            "historyTimeDeadband", "historyTimeDeadbandUnits",
        ), key


def test_history_does_not_send_the_83_only_property():
    """includeMetadata exists only on 8.3; one payload must serve both."""
    assert "includeMetadata" not in provisioning.history_block(PROVIDER)


def test_history_enum_values_are_legal():
    block = provisioning.history_block(PROVIDER)
    assert block["sampleMode"] in ("OnChange", "Periodic", "TagGroup")
    assert block["historicalDeadbandStyle"] in (
        "Auto", "Analog_Compressed", "Discrete")
    assert block["historicalDeadbandMode"] in ("Absolute", "Percent")
    assert block["historyMaxAgeUnits"] in (
        "MS", "SEC", "MIN", "HOUR", "DAY", "WEEK", "MONTH", "YEAR")


def test_deadband_is_zero():
    """Integer counts: any deadband >= 1 hides every single-thread change.

    Including Blocked going 0 -> 1, which is the most valuable event this
    project exists to catch.
    """
    assert provisioning.history_block(PROVIDER)["historicalDeadband"] == 0.0


def test_max_age_is_set_because_the_default_disables_it():
    """historyMaxAge defaults to 0 = disabled.

    Left at the default, a pool whose count never changes writes nothing
    after its first sample and its series just stops -- indistinguishable
    from a dead monitor.
    """
    assert provisioning.history_block(PROVIDER)["historyMaxAge"] > 0


def test_refuses_to_run_without_a_history_provider():
    """A blank provider would create 64 tags that look historized.

    historyProvider defaults to "" and a blank one is not known to store
    anything, so this must fail loudly rather than half-work.
    """
    fake = RecordingTags()
    result = provisioning.provision("", tag_system=fake)
    assert not result.ok()
    assert "history provider" in result.error
    assert fake.configured == [], "must not write anything"


def test_diagnostic_tags_get_their_real_datatypes():
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)
    assert fake.tag_at(tagpaths.diagnostic_tag(tagpaths.LAST_ERROR))[
        "dataType"] == "String"
    assert fake.tag_at(tagpaths.diagnostic_tag(tagpaths.LAST_SAMPLE_TIME))[
        "dataType"] == "DateTime"
    assert fake.tag_at(tagpaths.diagnostic_tag(
        tagpaths.SAMPLE_DURATION_MS))["dataType"] == "Int4"
    assert fake.tag_at(tagpaths.pool_member("webserver", "Count"))[
        "dataType"] == "Int4"


def test_tag_types_are_exact_constants():
    """TagObjectType.fromString does NOT throw on a typo -- it returns Unknown.

    So "AtomicTag " or "Memory" silently produces a tag of type Unknown.
    Nothing but the exact constants may ever reach configure.
    """
    fake = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=fake)
    for _base, tags, _policy in fake.configured:
        for tag in tags:
            assert tag["tagType"] in ("AtomicTag", "Folder"), tag["tagType"]


# --- failing loudly -------------------------------------------------------

def test_a_rejected_tag_makes_the_whole_run_not_ok():
    fake = RecordingTags(bad_names=["Blocked"])
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert not result.ok()
    assert result.problems()
    assert "Blocked" in result.summary()


def test_a_short_quality_list_is_a_failure_not_a_success():
    """The docstring promises one quality per tag created or edited.

    Fewer means something was not created. Scoring that as success is exactly
    how a half-provisioned tree ends up looking healthy.
    """
    fake = RecordingTags(short_by=1)
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert not result.ok()


def test_an_empty_quality_list_is_a_failure():
    fake = RecordingTags(short_by=99)
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert not result.ok()


def test_a_null_return_is_a_failure():
    fake = RecordingTags(return_null=True)
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert not result.ok()


def test_a_throwing_configure_is_captured_not_propagated():
    fake = RecordingTags(raise_on="Pools/webserver")
    result = provisioning.provision(PROVIDER, tag_system=fake)
    assert not result.ok()
    assert "gateway said no" in result.summary()


def test_no_steps_at_all_is_not_success():
    result = provisioning.ProvisionResult()
    assert not result.ok()


def test_uninterrogatable_quality_fails_closed():
    class Weird(object):
        def isGood(self):
            raise RuntimeError("nope")

        def __str__(self):
            return "???"

    assert provisioning._is_good(Weird()) is False


# --- idempotency ----------------------------------------------------------

def test_running_twice_sends_identical_payloads():
    """Re-provisioning must be a no-op in effect.

    Overwrite is safe here only because the payload is the COMPLETE desired
    configuration. If this ever stops being true, overwrite starts destroying
    settings on every run.
    """
    first = RecordingTags()
    second = RecordingTags()
    provisioning.provision(PROVIDER, tag_system=first)
    provisioning.provision(PROVIDER, tag_system=second)
    assert first.configured == second.configured


def test_adding_a_pool_bucket_needs_no_provisioning_change():
    """A 13th PoolSpec must bring its own tags with it."""
    extra = taxonomy.PoolSpec(
        "newpool", "New", taxonomy.matchers.prefix("brand-new-"),
        "A bucket added later, to prove provisioning follows the catalog.")
    taxonomy.POOL_SPECS.insert(-1, extra)
    try:
        fake = RecordingTags()
        result = provisioning.provision(PROVIDER, tag_system=fake)
        assert result.ok()
        created = set(fake.created_paths())
        for member in tagpaths.UDT_MEMBERS:
            assert tagpaths.pool_member("newpool", member) in created
    finally:
        taxonomy.POOL_SPECS.remove(extra)
