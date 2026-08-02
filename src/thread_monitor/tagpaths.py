"""Every tag path this project writes, in one place.

Nothing else in the repo may build a tag path by string concatenation. The
tag JSON in `ignition-project/tags/`, the provisioning code, the writer and
the tests all derive their paths from here, so a rename is one edit and
`tests/test_tagpaths.py` proves the committed tag JSON still agrees.

Layout:

    [default]GatewayHealth/Threads/
        Pools/<key>/{Count,Runnable,Blocked,Waiting,TimedWaiting}
        TotalCount  PeakCount  DaemonCount  DeadlockedCount  BlockedTotal
        PoolTable                       <- DataSet, feeds the Perspective table
        Diagnostics/{SampleDurationMs, LastSampleTime, LastError,
                     ApiRoute, UnmatchedNames}

Jython 2.7: no f-strings, no comprehensions.
"""
from thread_monitor import taxonomy

PROVIDER = "[default]"
ROOT = "GatewayHealth/Threads"
POOLS_FOLDER = "Pools"
DIAGNOSTICS_FOLDER = "Diagnostics"

UDT_NAME = "ThreadPool"

# UDT member name -> the java.lang.Thread.State it counts.
#
# NEW and TERMINATED are deliberately not broken out. Both are vanishingly
# rare in a steady-state gateway and giving each its own historized tag across
# every pool would cost ~28 tags to trend two flat lines. They are still
# included in Count, so if they ever do occur, Count exceeds the sum of the
# four state members -- visible, rather than silently dropped.
STATE_MEMBERS = [
    ("Runnable", "RUNNABLE"),
    ("Blocked", "BLOCKED"),
    ("Waiting", "WAITING"),
    ("TimedWaiting", "TIMED_WAITING"),
]

COUNT_MEMBER = "Count"


def _member_names():
    names = [COUNT_MEMBER]
    for member, _state in STATE_MEMBERS:
        names.append(member)
    return names


UDT_MEMBERS = _member_names()


def base():
    """'[default]GatewayHealth/Threads'"""
    return PROVIDER + ROOT


def pools_folder():
    return base() + "/" + POOLS_FOLDER


def pool_folder(key):
    return pools_folder() + "/" + key


def pool_member(key, member):
    """e.g. '[default]GatewayHealth/Threads/Pools/webserver/Blocked'"""
    return pool_folder(key) + "/" + member


def gateway_tag(name):
    """e.g. '[default]GatewayHealth/Threads/TotalCount'"""
    return base() + "/" + name


def diagnostic_tag(name):
    """e.g. '[default]GatewayHealth/Threads/Diagnostics/LastError'"""
    return base() + "/" + DIAGNOSTICS_FOLDER + "/" + name


TOTAL_COUNT = "TotalCount"
PEAK_COUNT = "PeakCount"
DAEMON_COUNT = "DaemonCount"
DEADLOCKED_COUNT = "DeadlockedCount"

# Blocked threads across every pool, as one number.
#
# The per-pool Blocked tags say WHICH pool; this says WHETHER. That turns out
# to be the more useful question most of the time: it is the one value worth
# putting on a status tile and the one worth alarming on, and neither is
# possible against fourteen separate tags without an expression that has to be
# rewritten every time a PoolSpec is added.
#
# Derived, not measured -- it is the sum of the fourteen Pools/*/Blocked values
# from the same sample, so it can never disagree with them.
BLOCKED_TOTAL = "BlockedTotal"

GATEWAY_TAGS = [TOTAL_COUNT, PEAK_COUNT, DAEMON_COUNT, DEADLOCKED_COUNT,
                BLOCKED_TOTAL]

# The whole snapshot as one DataSet, so the Perspective table can show current
# state per pool without sixty separate bindings.
#
# It is a tag rather than a Perspective named query or a script transform for
# one reason: `props.data` bound to a tag is a shape this project has already
# proven works (it is the same binding the stat tiles use for props.text). A
# per-cell `props.data[0].count` binding was tried first and rendered zero rows
# with no error anywhere -- see build_view.build_table.
#
# NOT historized, and not because it was forgotten. The historian stores scalar
# values per tag; the trendable numbers in here are already the 75 historized
# scalars this dataset is assembled from. Historizing it would duplicate all of
# them into a blob nothing can chart.
POOL_TABLE = "PoolTable"

DATASET_TAGS = [POOL_TABLE]

SAMPLE_DURATION_MS = "SampleDurationMs"
LAST_SAMPLE_TIME = "LastSampleTime"
LAST_ERROR = "LastError"
API_ROUTE = "ApiRoute"
UNMATCHED_NAMES = "UnmatchedNames"

DIAGNOSTIC_TAGS = [SAMPLE_DURATION_MS, LAST_SAMPLE_TIME, LAST_ERROR,
                   API_ROUTE, UNMATCHED_NAMES]


def scalar_paths():
    """Every path whose value is a plain number, string or timestamp.

    This is exactly what snapshot.flatten_for_write() produces, in the same
    order, and test_tagpaths proves the two never drift apart.

    Split out from all_paths() because the DataSet tag cannot be built in this
    package: turning rows into a Dataset needs system.dataset.toDataSet, and
    nothing under thread_monitor/ is allowed to touch system.* (CLAUDE.md #1).
    The pure core produces the rows; the adapter converts and appends them.
    """
    paths = []
    for key in taxonomy.spec_keys():
        for member in UDT_MEMBERS:
            paths.append(pool_member(key, member))
    for name in GATEWAY_TAGS:
        paths.append(gateway_tag(name))
    for name in DIAGNOSTIC_TAGS:
        paths.append(diagnostic_tag(name))
    return paths


def dataset_paths():
    paths = []
    for name in DATASET_TAGS:
        paths.append(gateway_tag(name))
    return paths


def all_paths():
    """Every tag path this project expects to exist.

    Used by provisioning to know what to create, and by audit() to prove the
    provider really holds them. Built by concatenation rather than by a second
    copy of the loops, so a path can never appear in one and not the other.
    """
    return scalar_paths() + dataset_paths()


# Ignition dataType names. Int4 is the default but is stated anyway, because
# a tag that silently comes out the wrong type is not diagnosable from the
# trend. See ignition_adapter/stubs.py for the full legal list.
DATATYPE_INT = "Int4"
DATATYPE_STRING = "String"
DATATYPE_DATETIME = "DateTime"

# Verified by reflection on BOTH gateways: the DataType enum in
# com.inductiveautomation.ignition.common.sqltags.model.types has 22 constants
# and `DataSet` is one of them, spelled exactly like this. Worth checking
# rather than assuming -- a typo in tagType silently becomes `Unknown` instead
# of erroring, per stubs.py.
DATATYPE_DATASET = "DataSet"

# Diagnostic tags are NOT all integers, unlike everything else.
DIAGNOSTIC_TYPES = {
    SAMPLE_DURATION_MS: DATATYPE_INT,
    LAST_SAMPLE_TIME: DATATYPE_DATETIME,
    LAST_ERROR: DATATYPE_STRING,
    API_ROUTE: DATATYPE_STRING,
    UNMATCHED_NAMES: DATATYPE_STRING,
}


def datatype_for(path):
    """The Ignition dataType this path should be created with."""
    for name in DIAGNOSTIC_TAGS:
        if path == diagnostic_tag(name):
            return DIAGNOSTIC_TYPES[name]
    for name in DATASET_TAGS:
        if path == gateway_tag(name):
            return DATATYPE_DATASET
    return DATATYPE_INT


def historized_paths():
    """The paths that get tag history enabled. Deliberately NOT all of them.

    The 70 pool members plus the 5 gateway counters -- 75 of the 81 -- and
    neither the 5 Diagnostics tags nor the PoolTable DataSet.

    Excluding Diagnostics is not tidiness, it is the difference between
    on-change historization working and not. Measured on the live 8.1
    gateway: the real metrics change about 2% of samples, so on-change stores
    ~11,100 rows/day. But LastSampleTime is a timestamp, so it changes on
    EVERY sample by definition, and SampleDurationMs jitters 4-5ms so it
    changes on roughly half. Historizing those two alone would add ~12,960
    rows/day -- 54% of all rows, carrying nothing trendable -- and would
    degrade on-change back into fixed-periodic for no benefit.

    They remain live tags. They are readable in the tag browser and in a
    Perspective binding; they just are not written to the historian.
    """
    paths = []
    for key in taxonomy.spec_keys():
        for member in UDT_MEMBERS:
            paths.append(pool_member(key, member))
    for name in GATEWAY_TAGS:
        paths.append(gateway_tag(name))
    return paths
