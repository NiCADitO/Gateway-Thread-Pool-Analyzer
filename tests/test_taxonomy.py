"""The catalog itself: no shadowing, no dead specs, no malformed keys."""
import re

from thread_monitor import sampler, taxonomy

KEY_RE = re.compile(r"^[a-z][a-z0-9]*$")


def test_keys_are_unique():
    keys = taxonomy.spec_keys()
    assert len(keys) == len(set(keys))


def test_keys_are_safe_as_tag_path_segments():
    """A key becomes a tag folder name, so no spaces, slashes or brackets."""
    for key in taxonomy.spec_keys():
        assert KEY_RE.match(key), key


def test_other_is_last():
    """First-match-wins means the catch-all must be terminal."""
    assert taxonomy.POOL_SPECS[-1].key == taxonomy.OTHER_KEY


def test_only_other_is_a_catch_all():
    """No spec except OTHER may match an arbitrary string.

    A stray `prefix("")` anywhere above OTHER silently absorbs the rest of the
    catalog, and every bucket after it reads zero forever.
    """
    for spec in taxonomy.POOL_SPECS[:-1]:
        assert not spec.matches("zzz-nothing-should-match-this"), spec.key


def test_every_spec_has_a_why():
    """The `why` is read off a spiking chart. An empty one is a bug."""
    for spec in taxonomy.POOL_SPECS:
        assert spec.why and len(spec.why) > 30, spec.key
        assert spec.label, spec.key


def test_no_spec_is_dead_across_the_whole_corpus(all_dumps):
    """Every bucket except `other` must claim a thread on SOME gateway.

    Deliberately the union, not per-gateway: 8.1.48 here has no datasources,
    so its history and database buckets are legitimately empty. Asserting
    per-fixture would push someone to delete those PoolSpecs, which would then
    read as "no store-and-forward problems" on a gateway that has plenty.

    What this does catch is a typo in a prefix, or a pool that no longer
    exists on any version we have evidence for.
    """
    claimed = set()
    for pairs in all_dumps.values():
        snap = sampler.count(pairs)
        for entry in snap.pools:
            if entry.total > 0:
                claimed.add(entry.key)

    silent = []
    for spec in taxonomy.POOL_SPECS:
        if spec.key == taxonomy.OTHER_KEY:
            continue
        # A CONSTANT spec is expected to be silent here -- that is the whole
        # reason it is marked. Neither lab gateway runs alarm pipelines or has
        # a field device attached, so `alarming` and `drivers` claim nothing.
        # Failing the build for them would push someone to delete two buckets
        # that a real gateway needs.
        if spec.evidence == taxonomy.EVIDENCE_CONSTANT:
            continue
        if spec.key not in claimed:
            silent.append(spec.key)
    assert silent == [], silent


def test_constant_only_specs_name_their_source(all_dumps):
    """A CONSTANT spec has to say where its names came from, and stay honest.

    Two rules, and the second is the one that matters:

    1. Its `why` must name the class the literals were read out of, so the
       claim is re-checkable against the jars.
    2. It must claim NOTHING on any dump we have. If it starts matching real
       threads, the evidence is no longer constant-only -- it has been seen
       live, and leaving the marker on would exempt it from the dead-spec
       check above for no reason.
    """
    claimed = set()
    for pairs in all_dumps.values():
        snap = sampler.count(pairs)
        for entry in snap.pools:
            if entry.total > 0:
                claimed.add(entry.key)

    for spec in taxonomy.POOL_SPECS:
        if spec.evidence != taxonomy.EVIDENCE_CONSTANT:
            continue
        assert "not seen live" in spec.why, spec.key
        assert spec.key not in claimed, (
            "%s is marked EVIDENCE_CONSTANT but now matches real threads -- "
            "promote it to EVIDENCE_DUMP" % (spec.key,))


def test_version_specific_prefixes_are_both_live(all_dumps):
    """The opcua bucket must be non-empty on BOTH gateways.

    8.1.11 spells it `milo-*`, 8.1.48 spells it `opc-ua-*`. This is the
    regression test for the drift that the two-gateway capture found: drop
    either prefix and one version silently trends a flat zero.
    """
    for name, pairs in all_dumps.items():
        snap = sampler.count(pairs)
        assert snap.pool("opcua").total > 0, name
        assert snap.pool("executor").total > 0, name


def test_no_spec_shadows_a_later_one(dump_81_11):
    """Check ordering actually matters nowhere it should not.

    For every real thread name, the winning spec must be the ONLY non-`other`
    spec that matches it. If two specs both claim a name, the catalog is
    ambiguous and the result depends on list order -- which is exactly the
    kind of thing that changes silently when someone appends a bucket.
    """
    ambiguous = {}
    for name, _state in dump_81_11:
        claimants = []
        for spec in taxonomy.POOL_SPECS:
            if spec.key == taxonomy.OTHER_KEY:
                continue
            if spec.matches(name):
                claimants.append(spec.key)
        if len(claimants) > 1:
            ambiguous[name] = claimants
    assert ambiguous == {}, ambiguous


def test_find_returns_none_for_unknown_key():
    assert taxonomy.find("webserver") is not None
    assert taxonomy.find("no-such-pool") is None


def test_describe_renders_for_every_spec():
    """The README pool catalog is generated from these."""
    for spec in taxonomy.POOL_SPECS:
        described = spec.matcher.describe()
        assert isinstance(described, str) and described
