"""What the gateway timer and the script console call. No logic of its own.

Three entry points:

    dump()              read and print. Writes nothing. Run this first.
    sample_and_write()  what the gateway timer calls, every 10 seconds.
    diagnose()          dump() plus the tag paths, for provisioning problems.

Jython 2.7: no f-strings, no comprehensions.
"""
import sys

from ignition_adapter import config
from ignition_adapter import jvm
from ignition_adapter import provisioning
from ignition_adapter import tags
from thread_monitor import snapshot
from thread_monitor import tagpaths

try:
    import system
except ImportError:
    system = None


# Reentrancy guard. The gateway timer is configured Fixed Delay, so the next
# sample cannot start until this one returns and this should never trip -- but
# "should never" plus a shared JVM plus someone switching the timer to Fixed
# Rate in the Designer is exactly how two samples end up interleaving their
# writes. Cheap insurance for a wrong number that would be very hard to spot.
_sampling = [False]

# Last result, readable from the script console after the timer has been
# running. Saves provisioning a tag just to answer "is it working".
_last = [None]

# Samples taken since this module was loaded. Drives the heartbeat below.
_count = [0]

# Whether the one-shot auto-provision has been attempted since this module
# was loaded. Latches on the ATTEMPT, not on success -- see _maybe_provision.
_provision_attempted = [False]

# Last thing logged, so a persistent fault is not re-logged every sample.
# An unprovisioned tag tree fails identically forever; at a 10s timer that is
# 8,640 identical warnings a day, which buries the one line that changes.
_last_logged = [None]

LOGGER_NAME = "GatewayThreadMonitor"

# Log a summary every N samples. At a 10s timer that is once every 5 minutes:
# frequent enough to prove liveness in `docker logs`, rare enough not to be
# the noisiest thing in the gateway log.
HEARTBEAT_EVERY = 30


def _logger():
    """The gateway logger, or None off-gateway."""
    if system is None:
        return None
    try:
        return system.util.getLogger(LOGGER_NAME)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        return None


def _log(level, message):
    """Log if we can, and never let logging itself break a sample.

    A timer script has nowhere to return a value TO -- the gateway discards
    it -- so before any tags exist this log is the only evidence the thing is
    running at all. It stays useful afterwards for the same reason: when the
    tags stop updating, the question is whether the timer died or the writes
    failed, and those look identical from the tag browser.
    """
    logger = _logger()
    if logger is None:
        return
    try:
        if level == "warn":
            logger.warn(message)
        else:
            logger.info(message)
    except:  # noqa: E722 -- bare: see CLAUDE.md #3.
        pass


def _maybe_provision():
    """Provision once per module load, if config asks for it.

    LATCHES ON THE ATTEMPT, not on success. That is the whole safety
    property: a gateway where provisioning permanently fails must not rewrite
    tag configuration every 10 seconds forever. One attempt, one log line,
    then never again until the module is reloaded.

    A retry that succeeds on the second try is not worth an unbounded stream
    of config writes aimed at the gateway being measured. If the first attempt
    fails, the log says why and a human redeploys.
    """
    if _provision_attempted[0]:
        return
    if not config.PROVISION_ON_START:
        return

    _provision_attempted[0] = True
    result = provisioning.provision(config.HISTORY_PROVIDER)
    if result.ok():
        _log("info", "auto-provision: " + result.summary())
    else:
        _log("warn", "auto-provision: " + result.summary())


def _fault_key(result):
    """What KIND of state this sample is in, ignoring the numbers.

    Thread counts change every sample, so keying the log on the full summary
    would defeat the deduplication entirely. Keying on the fault instead means
    'still broken the same way' stays quiet while 'broken differently' or
    'recovered' speaks up immediately.
    """
    if result.error:
        return "error:" + result.error
    if result.bad_paths:
        return "rejected:%d" % (len(result.bad_paths),)
    if result.dataset_error:
        return "dataset:" + result.dataset_error
    return "ok"


def _log_sample(result, summary):
    """Log a sample, without re-logging a persistent fault every 10 seconds.

    Rules, in order:
      - state changed (including recovery) -> log it now
      - first ever sample                  -> log it now, so a deploy is
                                              confirmed in seconds
      - otherwise                          -> only on the heartbeat

    An unprovisioned tag tree fails identically forever. Logging that on every
    sample is 8,640 lines a day that say nothing new, and it buries the line
    that does.
    """
    key = _fault_key(result)
    changed = key != _last_logged[0]
    heartbeat = _count[0] % HEARTBEAT_EVERY == 0

    if not (changed or _count[0] == 1 or heartbeat):
        return

    _last_logged[0] = key

    if not result.ok():
        _log("warn", summary)
    elif changed and _count[0] > 1:
        _log("info", "recovered: " + summary)
    else:
        _log("info", "sample %d: %s" % (_count[0], summary))


def dump():
    """Read the gateway's threads and print a report. Writes nothing.

    This is M2's deliverable and the first thing to run on a new gateway:

        from ignition_adapter import entry
        print entry.dump()

    Run it from a GATEWAY scope -- the Script Console in the Designer runs in
    the Designer's own JVM, so it reports the Designer's threads, which look
    entirely plausible and are the wrong answer. Use a Gateway Event Script,
    a Perspective session (which runs in the gateway), or a WebDev endpoint.
    """
    snap = jvm.read()
    _last[0] = snap
    return snapshot.format_report(snap)


def sample_and_write():
    """Take one sample and write it to tags. What the gateway timer calls.

    Never raises. A gateway timer script that throws is disabled by the
    gateway, and the trend simply stops -- which reads on a chart as "the
    problem went away" rather than "the measurement died". Failing quietly to
    Diagnostics/LastError is the lesser evil, and the reason LastError is a
    historized tag rather than just a log line.
    """
    if _sampling[0]:
        _log("warn", "skipped: previous sample still running")
        return "skipped: previous sample still running"
    _sampling[0] = True
    try:
        try:
            _maybe_provision()
            snap = jvm.read()
            _last[0] = snap
            result = tags.write_snapshot(snap)
            _count[0] = _count[0] + 1

            summary = "%s (%d threads, %dms)" % (
                result.summary(), snap.total_threads,
                snap.sample_duration_ms or -1)

            _log_sample(result, summary)
            return summary
        except:  # noqa: E722 -- bare: see CLAUDE.md #3.
            message = "sample failed: %s" % (sys.exc_info()[1],)
            _log("warn", message)
            return message
    finally:
        _sampling[0] = False


def last_report():
    """The most recent snapshot, formatted. None if nothing has run yet."""
    if _last[0] is None:
        return "no sample taken yet"
    return snapshot.format_report(_last[0])


def provision(history_provider):
    """Create the tags. Run this ONCE per gateway, then never again.

    From a Gateway-scope script:

        from ignition_adapter import entry
        print entry.provision("PostgresDBConnection")

    The argument is the tag history provider name from the gateway's Tag
    History Providers page. It is required -- see provisioning.provision.

    Deliberately NOT called automatically by the timer. Provisioning writes
    tag configuration, and a timer that provisions on failure turns a
    persistent misconfiguration into an unbounded stream of config writes
    against the very gateway being measured.

    Do not trust this function's own report alone. The real proof is the next
    timer sample: it logs `81 tags written` instead of `81 of 81 writes
    rejected`, and that signal comes from a code path that was already
    working before any of this existed.
    """
    result = provisioning.provision(history_provider)
    _log("info", "provision: " + result.summary())
    return result.summary()


def diagnose():
    """dump(), plus every tag path this expects to exist.

    For the case where the timer reports writes rejected: the tag tree does
    not match what the code wants, and this prints exactly what it wants.
    """
    lines = [dump(), "", "Expects %d tags:" % (len(tagpaths.all_paths()),)]
    for path in tagpaths.all_paths():
        lines.append("  " + path)
    return "\n".join(lines)
