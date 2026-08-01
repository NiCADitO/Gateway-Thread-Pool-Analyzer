"""The pool catalog, as data.

Every entry is a PoolSpec. The sampler walks this list in order and assigns
each thread to the first spec that matches, so `OTHER` must stay last.

**Adding a pool means appending one PoolSpec here and one UDT instance. It
never means editing sampler.py.**

Provenance: every prefix below was taken from a real thread dump off a live
gateway, not from documentation and not from guesswork. Three were captured:

    tests/fixtures/threads_81_11.tsv   8.1.11 / OpenJDK 11   117 threads
    tests/fixtures/threads_81_48.tsv   8.1.48 / OpenJDK 17   105 threads
    tests/fixtures/threads_83_8.tsv    8.3.8  / OpenJDK 17   101 threads

See `docs/development.md` for how to capture a fresh one. The one name that
came from the forum post -- 'webserver' -- is also the only one that turned
out to be exactly right, and it is the only prefix stable across all three.

Capturing three gateways rather than one paid for itself immediately:

- 8.1.48 renamed the OPC-UA pool from `milo-*` to `opc-ua-*` and replaced
  `gateway-shared-exec-engine-*` with `shared-worker-*`.
- 8.3.8 added eight more pools that neither 8.1 gateway has at all --
  `single-executor-*`, `shared-scheduled-executor-*`, `Scheduler-<hash>-*`,
  `managed-tag-provider-*`, `Cleaner-*` and the auth-token schedulers.

A catalog built from any one gateway reports permanently empty buckets on the
others, and an empty bucket on a trend is indistinguishable from a healthy
idle one. Every spelling is kept for that reason -- do not "clean up" the one
your gateway does not use.

Two prefixes are deliberately looser than the names that prompted them:

- `designer-auth-token-` and `client-auth-token-` stop before the role,
  because 8.3.8 has both `-scheduler-N` and `-worker-N` and the workers only
  appear once a Designer connects. The first capture off a freshly-booted
  gateway did not have them.
- `gateway-log` covers `gateway-log-monitoring-*` and `gateway-log-maintenance`,
  the latter being periodic -- absent from one capture off 8.1.11 and present
  in another ten minutes later.

Both are the same lesson: a catalog built from a single instant of a single
gateway misses threads that are not always up.

Two deliberate omissions, both load-bearing:

1. **There is no bare 'gateway-' catch-all.** It would match
   gateway-storeforward, gateway-shared-exec-engine and every other subsystem
   below, so it could only ever sit last -- at which point it silently
   swallows gateway threads this catalog has never seen. Letting those fall
   into OTHER instead is what makes `Diagnostics/UnmatchedNames` a working
   discovery mechanism rather than a permanently empty tag. A new Ignition
   version that adds a thread pool should show up as a rising OTHER count.

2. **VM-internal threads are not in the JVM bucket by accident of naming.**
   `GC Thread#0`, `G1 Refine#0`, `VM Thread` and `VM Periodic Task Thread`
   appear in a `kill -3` dump but carry no java.lang.Thread.State line and are
   NOT reported by ThreadMXBean.getAllThreadIds(). They are listed here anyway
   so that a dump-derived fixture and a live sample bucket identically, but on
   a real gateway those matchers will never fire. Confirmed on 8.1.11: the
   dump held 130 names, ThreadMXBean's view is the 117 with a state line.

Jython 2.7: no f-strings, no comprehensions.
"""
from thread_monitor import matchers


class PoolSpec(object):
    """One bucket in the catalog.

    key    tag-path segment and dict key. Lowercase, no spaces -- it becomes
           [default]GatewayHealth/Threads/Pools/<key>/Count.
    label  legend text on the Power Chart.
    matcher  a matchers.* object.
    why    what this pool actually does. Written for someone looking at a
           spiking trend who does not already know the subsystem.
    """

    def __init__(self, key, label, matcher, why):
        self.key = key
        self.label = label
        self.matcher = matcher
        self.why = why

    def matches(self, name):
        return self.matcher.matches(name)

    def __repr__(self):
        return "PoolSpec(%s)" % (self.key,)


OTHER_KEY = "other"


POOL_SPECS = [

    PoolSpec(
        "webserver",
        "Web server (Jetty)",
        matchers.prefix("webserver-"),
        "Jetty request handlers. Every Perspective page load, REST call, "
        "WebDev endpoint and Designer/Gateway web request runs on one of "
        "these. If this pool is pinned, the gateway feels slow to humans "
        "even when nothing else is wrong. The single most useful trend here.",
    ),

    PoolSpec(
        "executor",
        "Shared executors",
        matchers.any_of(
            matchers.prefix("gateway-shared-exec-engine-"),
            matchers.prefix("platform-executor-"),
            matchers.prefix("ForkJoinPool"),
            matchers.prefix("DefaultDispatcher-worker-"),
            # 8.1.48. Replaces gateway-shared-exec-engine-, which is absent
            # there -- so both must stay listed to span the 8.1 line.
            matchers.prefix("shared-worker-"),
            matchers.prefix("single-executor-"),  # 8.3.8
        ),
        "The gateway's general-purpose work pool. Almost every subsystem "
        "hands short tasks to it, so a backlog here means something else is "
        "blocking and this is where it shows up first.",
    ),

    PoolSpec(
        "scheduler",
        "Schedulers and timers",
        matchers.any_of(
            matchers.prefix("platform-scheduled-executor-"),
            matchers.prefix("cron4j::"),
            matchers.prefix("gateway-expr-pollingfunc-timer"),
            matchers.prefix("Timer-"),
            matchers.prefix("shared-scheduler-"),  # 8.1.48
            # 8.3.8. Note 'Scheduler-<hash>-N' is capitalised and carries a
            # per-boot hash, so only the prefix is stable.
            matchers.prefix("shared-scheduled-executor-"),
            matchers.prefix("Scheduler-"),
        ),
        "Fixed-rate work: gateway timer scripts, scheduled reports, polling "
        "expression tags. Growth here usually means a scheduled task is "
        "overrunning its own interval.",
    ),

    PoolSpec(
        "tags",
        "Tag system",
        matchers.any_of(
            matchers.prefix("tag-provider"),
            matchers.prefix("tag-group-manager"),
            matchers.prefix("gateway.tags."),
            matchers.prefix("managed-tag-provider-"),  # 8.3.8
        ),
        "Tag providers, tag group execution and the subscription model. "
        "Blocked threads here stall tag evaluation gateway-wide.",
    ),

    PoolSpec(
        "history",
        "Historian and store-and-forward",
        matchers.any_of(
            matchers.prefix("tags-history-"),
            matchers.prefix("gateway-storeforward-"),
        ),
        "Tag history writes and the store-and-forward pipelines. Sustained "
        "growth is the early warning for a historian that cannot keep up "
        "with its own ingest rate -- usually a slow or unreachable database.",
    ),

    PoolSpec(
        "database",
        "Database connections",
        matchers.any_of(
            matchers.prefix("gateway-db-connection-validator-"),
            matchers.prefix("mysql-cj-"),
            matchers.prefix("HSQLDB Timer"),
            matchers.exact("Connection evictor"),
        ),
        "Connection pool validation and eviction for the gateway's "
        "datasources, plus the internal HSQLDB config store. Small and "
        "stable in a healthy gateway; churn suggests connections dropping.",
    ),

    PoolSpec(
        "opcua",
        "OPC-UA",
        matchers.any_of(
            # 8.1.11 named these after the Milo library.
            matchers.prefix("milo-"),
            # 8.1.48 renamed them. Neither gateway has both, so dropping
            # either prefix silently empties this bucket on that version --
            # the single most valuable thing the two-gateway capture found.
            matchers.prefix("opc-ua-"),
        ),
        "The embedded OPC-UA stack -- Netty event loops, its shared pool and "
        "timers. Covers both the OPC-UA server and outbound device "
        "connections. Spikes track device connection churn.",
    ),

    PoolSpec(
        "perspective",
        "Perspective",
        matchers.prefix("perspective-"),
        "Perspective session workers and scheduler. Scales with concurrent "
        "sessions, so this trend read next to webserver separates 'lots of "
        "users' from 'one slow request'.",
    ),

    PoolSpec(
        "scripting",
        "Gateway scripting",
        matchers.prefix("gateway-scripts-"),
        "Project library watching and reload notification. Notably this is "
        "NOT where gateway timer scripts run -- those are on the scheduler "
        "pool. Steady at a couple of threads in normal operation.",
    ),

    PoolSpec(
        "platform",
        "Platform and infrastructure",
        matchers.any_of(
            matchers.prefix("gateway-performance-"),
            matchers.prefix("gateway-logging-"),
            # Covers gateway-log-monitoring-* and gateway-log-maintenance.
            # The latter is periodic: it was absent from the first capture off
            # 8.1.11 and present in the second, ten minutes later. Buckets
            # built from a single instant miss threads that are not always up.
            matchers.prefix("gateway-log"),
            matchers.prefix("gateway-gn-"),
            matchers.prefix("AsyncAppender-Worker-"),
            matchers.prefix("FileSystemWatchService"),
            matchers.prefix("certificate-store-watcher"),
            matchers.prefix("catapult-filemonitor"),
            matchers.prefix("HttpClient-"),
            matchers.prefix("Wrapper"),
            matchers.prefix("Wicket-"),
            matchers.prefix("Session-Scheduler-"),
            matchers.prefix("Session-HouseKeeper-"),
            matchers.prefix("Connector-Scheduler-"),
            # 8.1.48 TLS trust-manager refresh threads.
            matchers.prefix("client-trust-manager-"),
            matchers.prefix("server-trust-manager-"),
            # 8.3.8 identity and auth-token housekeeping. Kept here rather
            # than given a `security` bucket of their own: they are
            # constant-count and there is nothing to read in the trend.
            # Prefix stops before the role: 8.3.8 has both
            # designer-auth-token-scheduler-N and -worker-N, and the workers
            # only appear once a Designer connects -- they were absent from
            # the first capture off a freshly-booted gateway.
            matchers.prefix("client-auth-token-"),
            matchers.prefix("designer-auth-token-"),
            matchers.prefix("internal-remembered-subjects-scheduler-"),
            matchers.exact("File Reaper"),
        ),
        "Logging appenders, file and certificate watchers, the service "
        "wrapper, gateway network, and Jetty's own session/connector "
        "housekeeping. Kept out of the webserver bucket on purpose: these are "
        "constant-count background threads and mixing them in would put a "
        "fixed offset on the one trend that most needs to read true.",
    ),

    PoolSpec(
        "jvm",
        "JVM internals",
        matchers.any_of(
            matchers.prefix("GC Thread#"),
            matchers.prefix("G1 "),
            matchers.prefix("VM "),
            matchers.prefix("C1 CompilerThread"),
            matchers.prefix("C2 CompilerThread"),
            matchers.prefix("CompilerThread"),
            matchers.prefix("process reaper"),
            matchers.exact("VM Thread"),
            matchers.exact("Reference Handler"),
            matchers.exact("Finalizer"),
            matchers.exact("Signal Dispatcher"),
            matchers.exact("Common-Cleaner"),
            matchers.exact("DestroyJavaVM"),
            matchers.exact("Sweeper thread"),
            matchers.exact("Service Thread"),
            matchers.exact("Attach Listener"),
            matchers.exact("Notification Thread"),
            matchers.exact("Java2D Disposer"),
            # Java 17. Present on 8.1.48, absent on 8.1.11's Java 11, and --
            # unlike GC Thread# -- it IS ThreadMXBean-visible.
            matchers.exact("Monitor Deflation Thread"),
            matchers.prefix("Cleaner-"),  # 8.3.8 / Java 17
            # A gateway's main thread is WrapperSimpleAppMain (see platform);
            # a plain JVM's is 'main'. Present so running this code outside a
            # gateway -- scripts/verify_on_gateway.py does exactly that --
            # does not report a spurious unmatched thread.
            matchers.exact("main"),
        ),
        "JIT compiler, reference handling and cleaners. The GC and VM "
        "matchers here never fire on a live sample -- ThreadMXBean does not "
        "report VM-internal threads -- they exist so a kill -3 dump and a "
        "live sample bucket the same way.",
    ),

    # Must stay last. Everything reaches this.
    PoolSpec(
        OTHER_KEY,
        "Other",
        matchers.prefix(""),
        "Anything the catalog does not recognise. Expected to be near zero. "
        "A rising OTHER count means a new thread pool appeared -- read "
        "Diagnostics/UnmatchedNames, then add a PoolSpec above.",
    ),
]


def spec_keys():
    """Bucket keys in catalog order. The tag layout is built from this."""
    keys = []
    for spec in POOL_SPECS:
        keys.append(spec.key)
    return keys


def find(key):
    """The PoolSpec for `key`, or None."""
    for spec in POOL_SPECS:
        if spec.key == key:
            return spec
    return None
