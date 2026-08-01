"""Every tag path this project writes, in one place.

Nothing else in the repo may build a tag path by string concatenation. The
tag JSON in `ignition-project/tags/`, the provisioning code, the writer and
the tests all derive their paths from here, so a rename is one edit and
`tests/test_tagpaths.py` proves the committed tag JSON still agrees.

Layout:

    [default]GatewayHealth/Threads/
        Pools/<key>/{Count,Runnable,Blocked,Waiting,TimedWaiting}
        TotalCount  PeakCount  DaemonCount  DeadlockedCount
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
# every pool would cost ~24 tags to trend two flat lines. They are still
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

GATEWAY_TAGS = [TOTAL_COUNT, PEAK_COUNT, DAEMON_COUNT, DEADLOCKED_COUNT]

SAMPLE_DURATION_MS = "SampleDurationMs"
LAST_SAMPLE_TIME = "LastSampleTime"
LAST_ERROR = "LastError"
API_ROUTE = "ApiRoute"
UNMATCHED_NAMES = "UnmatchedNames"

DIAGNOSTIC_TAGS = [SAMPLE_DURATION_MS, LAST_SAMPLE_TIME, LAST_ERROR,
                   API_ROUTE, UNMATCHED_NAMES]


def all_paths():
    """Every tag path this project expects to exist, in write order.

    Used by provisioning to know what to create and by the tests to prove the
    committed tag JSON covers exactly this set.
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
