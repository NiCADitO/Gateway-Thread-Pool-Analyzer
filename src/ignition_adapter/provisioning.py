"""Create the 82 tags this project writes to.

Every API detail used here was read out of the gateways' OWN JARS by Jython
reflection inside the containers, not from documentation -- see stubs.py. That
mattered: the collision policy 'd' this repo previously documented does not
exist and throws, and the history key is `historySampleRate` while the
deadband keys are `historicalDeadband*`. A wrong history key is silently
dropped -- no error, no bad QualityCode -- and the trend is empty hours later.

THREE DESIGN DECISIONS, and why.

1. FLAT TAGS, NOT A UDT.
   The original plan called for a ThreadPool UDT with 14 instances, to keep
   history settings in one place. Reflection showed that UDT definitions live
   under a reserved `_types_` folder and that instances carry a typeId
   relative to it -- but the exact dict shape for a definition, and whether
   history configured on a definition member propagates into instances, are
   INFERRED, not verified.

   The UDT's whole benefit was "history configured once". This code generates
   the history block once in Python and applies it to every tag, so that
   benefit is already had without depending on any unverified semantics.
   Adding a 15th pool is still zero manual work: it comes from
   taxonomy.POOL_SPECS like everything else.

2. ONE `configure` CALL PER FOLDER, WITH A FLAT LIST OF LEAVES.
   `configure` returns one QualityCode per tag created or edited, but for a
   NESTED payload it is unconfirmed whether that is one per top-level dict or
   one per leaf. Sending flat lists makes the mapping unambiguous: N tags in,
   N qualities out, and this module refuses to call a run successful if those
   two numbers disagree.

3. SUCCESS IS NEVER CERTIFIED BY THE QUALITYCODES ALONE.
   Three independent signals have to agree, and none of them is a read-back
   of `getConfiguration` -- whose return shape is unconfirmed on both
   gateways, so leaning on it would mean guessing at a shape and guessing
   generously.

   a) The QualityCodes from `configure`, one per tag, inspected individually.
   b) `audit()`, which asks `system.tag.exists(path)` for all 82 paths.
      Verified by reflection on both gateways. This is a SECOND OPINION: (a)
      says what configure believed it did, (b) says what the provider
      actually holds. It fails closed -- if the audit cannot run, ok() is
      False, because "could not check" must never read as "checked and fine".
   c) The timer's own next sample, which was already working before any of
      this existed: it logs `82 of 82 writes rejected` before provisioning
      and `82 tags written` after.

   History is proven more directly still, by rows appearing in the historian
   database. Nothing here is asserted on faith.

   Note (b) must never be done by WRITING a probe value. An earlier design
   proposed exactly that, which would have scribbled the -1 "probe failed"
   sentinel into all 75 live metric tags on every provisioning run --
   corrupting the very signal this project exists to produce.

Jython 2.7: no f-strings, no comprehensions, bare except only.
"""
import sys

from thread_monitor import taxonomy
from thread_monitor import tagpaths

try:
    import system
except ImportError:
    system = None  # CPython test suite


# 'o' = Overwrite. Legal letters are a/o/i/m/r; 'd' is NOT one and throws.
#
# Overwrite is the right choice for idempotency HERE specifically because the
# payload this module builds is the complete desired configuration of every
# tag. There is nothing to preserve, so "completely replaces a tag's
# configuration" is exactly the wanted behaviour and a re-run is a no-op in
# effect. MergeOverwrite would be the choice if we were patching tags someone
# else owns.
COLLISION_OVERWRITE = "o"

# 'i' = Ignore. FOLDERS ARE NOT OVERWRITTEN, and this is not fussiness.
#
# Overwrite is documented as "completely replaces a tag's configuration".
# What that means for a FOLDER -- whether its children survive -- is nowhere
# in the shipped documentation. Re-provisioning with 'o' on the Pools folder
# therefore risks taking all fourteen pools and their history configuration
# with it, and the symptom would be a chart that silently stops.
#
# A folder has no configuration worth replacing anyway: it is a name. So
# Ignore costs nothing and removes the whole question.
COLLISION_IGNORE = "i"

FOLDER = "Folder"
ATOMIC = "AtomicTag"

# Explicit opt-out for a gateway with no tag historian at all -- the 8.3 lab
# gateway has no database connection, so this is a real configuration, not a
# hypothetical. Tags are still created and still carry live values; they are
# simply not historized.
#
# It has to be a deliberate sentinel rather than an empty string. Blank is
# what an UNSET config looks like, and treating unset as "no history wanted"
# is how you end up with 75 tags that look historized and store nothing.
# Blank refuses; "NONE" proceeds knowingly.
NO_HISTORY = "NONE"

# History settings. Numbers justified against measured behaviour on the live
# 8.1 gateway, not picked for feel:
#
#   sampleMode OnChange       - the timer already decides when values change,
#                               so periodic logging would store 54x more rows
#                               that mostly repeat. Measured change rate is
#                               ~2% of samples.
#   historicalDeadband 0.0    - these are integer counts, so the smallest real
#                               change is 1. ANY deadband >= 1 would suppress
#                               every single-thread transition, including
#                               Blocked going 0 -> 1, which is the single most
#                               diagnostically valuable event this project
#                               exists to catch. At 2% churn there is no flood
#                               for a deadband to solve.
#   Discrete style            - thread counts are step functions. Analog
#                               interpolation would draw a straight ramp
#                               between two on-change points that can be five
#                               minutes apart, inventing intermediate values
#                               that never existed and turning a webserver
#                               spike into a gentle slope.
#   historyMaxAge 5 MIN       - the default is 0, meaning DISABLED, so a pool
#                               whose count never changes would write nothing
#                               after its first sample and its series would
#                               simply stop. That is indistinguishable from a
#                               dead monitor. 5 minutes is a liveness floor.
#                               Costs ~18k rows/day; 1 MIN would cost 5x and
#                               buy nothing, since spike resolution comes from
#                               on-change firing within 10s, not from here.
SAMPLE_MODE_ON_CHANGE = "OnChange"
DEADBAND_STYLE_DISCRETE = "Discrete"
DEADBAND_MODE_ABSOLUTE = "Absolute"
MAX_AGE = 5
MAX_AGE_UNITS = "MIN"


def history_block(history_provider):
    """The history properties applied to every historized tag.

    `includeMetadata` exists only on 8.3 and is deliberately omitted so one
    payload works on both versions. `historyProvider` defaults to "" and a
    blank provider is not known to do anything useful, so it is always set
    explicitly and provisioning refuses to run without one.
    """
    return {
        "historyEnabled": True,
        "historyProvider": history_provider,
        "sampleMode": SAMPLE_MODE_ON_CHANGE,
        "historicalDeadband": 0.0,
        "historicalDeadbandMode": DEADBAND_MODE_ABSOLUTE,
        "historicalDeadbandStyle": DEADBAND_STYLE_DISCRETE,
        "historyMaxAge": MAX_AGE,
        "historyMaxAgeUnits": MAX_AGE_UNITS,
    }


def leaf(name, datatype, historized, history_provider):
    """One memory tag definition."""
    tag = {
        "name": name,
        "tagType": ATOMIC,
        "valueSource": "memory",
        "dataType": datatype,
    }
    if historized:
        tag.update(history_block(history_provider))
    return tag


def folder(name):
    return {"name": name, "tagType": FOLDER}


class Step(object):
    """One configure() call and what came back.

    `advisory` steps do not gate the run. Folder creation is advisory because
    it uses collisionPolicy Ignore, and what QualityCode an existing tag comes
    back with under Ignore is not documented -- so a second provisioning run
    could report folder "failures" that mean nothing. Folders are proved to
    exist by the audit instead, which is a stronger check anyway.
    """

    def __init__(self, base_path, names, advisory=False):
        self.base_path = base_path
        self.names = names
        self.expected = len(names)
        self.good = 0
        self.bad = []
        self.error = ""
        self.advisory = advisory

    def ok(self):
        if self.advisory:
            return not self.error
        return (not self.error
                and not self.bad
                and self.good == self.expected)

    def describe(self):
        if self.error:
            return "%s: FAILED -- %s" % (self.base_path, self.error)
        if self.bad:
            return "%s: %d of %d rejected (%s)" % (
                self.base_path, len(self.bad), self.expected,
                ", ".join(self.bad))
        if self.good != self.expected:
            return "%s: expected %d qualities, got %d" % (
                self.base_path, self.expected, self.good)
        return "%s: %d ok" % (self.base_path, self.good)


class ProvisionResult(object):
    def __init__(self):
        self.steps = []
        self.error = ""
        # Paths the independent audit could not find. None means the audit
        # did not run, which is NOT the same as "found nothing missing".
        self.missing = None
        self.audit_error = ""
        # False when NO_HISTORY was used. Surfaced in the summary so a
        # gateway that is only carrying live values never looks like one
        # that is trending.
        self.historized = True

    def ok(self):
        if self.error:
            return False
        if not self.steps:
            return False
        for step in self.steps:
            if not step.ok():
                return False
        # Fail closed. If the audit could not run, this run is not certified,
        # because "we could not check" must never read the same as "checked
        # and fine".
        if self.audit_error:
            return False
        if self.missing is None:
            return False
        if self.missing:
            return False
        return True

    def tag_count(self):
        total = 0
        for step in self.steps:
            total = total + step.good
        return total

    def problems(self):
        found = []
        for step in self.steps:
            if not step.ok():
                found.append(step.describe())
        return found

    def summary(self):
        if self.error:
            return "provisioning failed: " + self.error
        if self.audit_error:
            return "provisioned, but the audit could not run: " + \
                self.audit_error
        if self.missing:
            return "provisioned, but %d tag(s) are still missing: %s" % (
                len(self.missing), ", ".join(self.missing[:3]))
        if not self.ok():
            return "provisioned with %d problem(s): %s" % (
                len(self.problems()), " | ".join(self.problems()))
        suffix = ""
        if not self.historized:
            suffix = " (NO HISTORY -- live values only)"
        return "provisioned %d tags across %d folders, all %d verified%s" % (
            self.tag_count(), len(self.steps), len(tagpaths.all_paths()),
            suffix)


def _configure(target, base_path, tags, result, policy=COLLISION_OVERWRITE):
    """One configure call, with every returned QualityCode inspected.

    An empty or short quality list is treated as FAILURE, not success. The
    docstring promises one entry per tag created or edited, so a mismatch
    means something was not created -- and scoring that as success is exactly
    how a half-provisioned tree ends up looking healthy.
    """
    names = []
    for tag in tags:
        names.append(tag["name"])
    step = Step(base_path, names, advisory=(policy == COLLISION_IGNORE))
    result.steps.append(step)

    try:
        qualities = target.configure(base_path, tags, policy)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        step.error = "%s" % (sys.exc_info()[1],)
        return step

    if qualities is None:
        step.error = "configure returned null"
        return step

    index = 0
    for quality in qualities:
        name = names[index] if index < len(names) else "?"
        index = index + 1
        if _is_good(quality):
            step.good = step.good + 1
        else:
            step.bad.append("%s (%s)" % (name, quality))
    return step


def audit(target, result):
    """Independently confirm every expected tag is really there.

    `system.tag.exists(path) -> boolean`, verified by reflection on both
    gateways. This is deliberately a SECOND opinion: the QualityCodes say
    what `configure` believed it did, this says what the tag provider
    actually holds. Certifying a run on the first alone means one misread
    return value can bless a tag tree that does not exist.

    Fails closed. A raising or unavailable `exists` sets audit_error, and
    ok() is then False -- "could not check" must never read the same as
    "checked and fine".
    """
    paths = tagpaths.all_paths()
    missing = []
    for path in paths:
        try:
            present = target.exists(path)
        except:  # noqa: E722 -- bare: see CLAUDE.md #3.
            result.audit_error = "exists(%s) failed -- %s" % (
                path, sys.exc_info()[1])
            return
        if not present:
            missing.append(path)
    result.missing = missing


def _is_good(quality):
    try:
        return bool(quality.isGood())
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        # Fail closed. A quality we cannot interrogate must not be counted as
        # a successful write, or a provisioning mistake becomes invisible.
        return "%s" % (quality,) == "Good"


def provision(history_provider, tag_system=None):
    """Create every tag, with history on the 75 that are trended.

    `history_provider` is the name of the tag history provider to write to --
    the same name that appears in the gateway's Tag History Providers list.
    Required: the property defaults to "" and a blank one is not known to
    store anything, so provisioning refuses rather than creating 75 tags that
    look historized and silently are not.
    """
    result = ProvisionResult()

    if not history_provider:
        result.error = ("no history provider given -- pass the name from the "
                        "gateway's Tag History Providers page. Refusing to "
                        "create tags that would look historized and store "
                        "nothing.")
        return result

    target = tag_system
    if target is None:
        if system is None:
            result.error = "no `system` available -- not on a gateway"
            return result
        target = system.tag

    provider = tagpaths.PROVIDER
    root = tagpaths.ROOT               # "GatewayHealth/Threads"
    parts = root.split("/")

    # Folders first, one level at a time. `configure` is not documented to
    # create intermediate folders, and a leaf written into a folder that does
    # not exist is precisely the silent half-failure this module is built to
    # avoid.
    _configure(target, provider, [folder(parts[0])], result, COLLISION_IGNORE)
    _configure(target, provider + parts[0], [folder(parts[1])], result,
               COLLISION_IGNORE)

    base = provider + root
    _configure(target, base,
               [folder(tagpaths.POOLS_FOLDER),
                folder(tagpaths.DIAGNOSTICS_FOLDER)],
               result, COLLISION_IGNORE)

    pools_base = provider + root + "/" + tagpaths.POOLS_FOLDER
    pool_folders = []
    for key in taxonomy.spec_keys():
        pool_folders.append(folder(key))
    _configure(target, pools_base, pool_folders, result, COLLISION_IGNORE)

    # Then the leaves, one flat list per folder so N tags in means N
    # qualities out and the mapping is unambiguous.
    historize = history_provider != NO_HISTORY
    result.historized = historize

    for key in taxonomy.spec_keys():
        members = []
        for member in tagpaths.UDT_MEMBERS:
            members.append(leaf(member, tagpaths.DATATYPE_INT, historize,
                                history_provider))
        _configure(target, pools_base + "/" + key, members, result)

    gateway_leaves = []
    for name in tagpaths.GATEWAY_TAGS:
        gateway_leaves.append(leaf(name, tagpaths.DATATYPE_INT, historize,
                                   history_provider))
    # PoolTable rides in the same call -- same base path, so one configure
    # still means N tags in, N qualities out. It is NOT historized regardless
    # of `historize`: the historian stores scalars, and every number in this
    # dataset is already one of the 75 historized tags it was assembled from.
    for name in tagpaths.DATASET_TAGS:
        gateway_leaves.append(leaf(name, tagpaths.DATATYPE_DATASET, False, ""))
    _configure(target, base, gateway_leaves, result)

    # Diagnostics: created, but NOT historized. See tagpaths.historized_paths.
    diagnostic_leaves = []
    for name in tagpaths.DIAGNOSTIC_TAGS:
        diagnostic_leaves.append(
            leaf(name, tagpaths.DIAGNOSTIC_TYPES[name], False, ""))
    _configure(target, base + "/" + tagpaths.DIAGNOSTICS_FOLDER,
               diagnostic_leaves, result)

    audit(target, result)
    return result
