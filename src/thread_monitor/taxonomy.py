"""The pool catalog, as data.

Every entry is a PoolSpec. The sampler walks this list in order and assigns
each thread to the first spec that matches, so `OTHER` must stay last.

**Adding a pool means appending one PoolSpec here and one UDT instance. It
never means editing sampler.py.**

Provenance: every prefix below came from a real thread dump off a live
gateway, or from a string constant compiled into the gateway's own jars.
Nothing here is from documentation and nothing is from guesswork. Five dumps
were captured:

    tests/fixtures/threads_81_11.tsv            8.1.11 / JDK 11   117 threads
    tests/fixtures/threads_81_11_timer.tsv      8.1.11 / JDK 11   119 threads
    tests/fixtures/threads_81_48.tsv            8.1.48 / JDK 17   105 threads
    tests/fixtures/threads_83_8.tsv             8.3.8  / JDK 17   101 threads
    tests/fixtures/threads_83_8_historian.tsv   8.3.8  / JDK 17   129 threads

TWO SOURCES, AND WHY THE SECOND ONE EXISTS

A dump only ever shows an instant of one gateway with one configuration. It
cannot show a pool belonging to a module you have not licensed, a device you
have not connected, or an alarm pipeline you have not built -- and on a
customer gateway all three of those exist and would land in `other`.

So the second source is static: every string constant compiled into a class
that calls
    com.inductiveautomation.ignition.common.execution.TPC.newThreadFactory
which is where Ignition centralises thread naming. Those literals ARE the
thread names, read out of the bytecode's constant pool.

Finding that entry point took three wrong turns worth recording, because each
one produced a confident-looking wrong answer:

  1. Filtering a million constants by what a thread name LOOKS like returned
     ~6,000 candidates and 67% recall on names already known to be real.
     Shape is weak evidence; provenance is strong evidence.
  2. Filtering to classes that call Thread.setName returned ZERO unbucketed
     candidates on 8.1 -- a clean result that was measuring nothing, because
     Ignition does not call setName at the call site.
  3. The scan reported "0 classes name threads" across 128,877 classes,
     because under Jython a java.lang.String does NOT compare equal to a
     Python str. `String("x") == "x"` is False. Every marker test was
     silently false. See ignition_adapter/stubs.py -- the same seam that
     test_states_cross_the_boundary_as_text_not_as_a_java_enum guards.

THE LIMIT OF THE STATIC SCAN, stated plainly: it recovers only 9 of 17
prefixes we have watched live. `webserver-`, `tags-history-`,
`platform-executor-`, `shared-worker-`, `single-executor-` and
`data-collector-` are all absent from it, because those names are assembled
at runtime rather than stored as one literal. **Its silence about a pool is
therefore not evidence that the pool does not exist.** It adds names; it
never subtracts them, and `Diagnostics/UnmatchedNames` remains the mechanism
that catches whatever both methods missed.

Candidates it surfaced that are deliberately NOT here: HTTP header names,
Dropwizard metric names (`gateway-network.pendingUploads.%s`), config keys
(`device-name`, `session-project`), backup filenames and Designer-only
threads. Being a literal in a thread-naming class makes a string a candidate,
not a thread name -- each one was read before it was accepted or rejected.

See `docs/development.md` for how to capture a fresh one. The one name that
came from the forum post -- 'webserver' -- is also the only one that turned
out to be exactly right, and it is the only prefix stable across all three.

Capturing several gateways rather than one paid for itself immediately:

- 8.1.48 renamed the OPC-UA pool from `milo-*` to `opc-ua-*` and replaced
  `gateway-shared-exec-engine-*` with `shared-worker-*`.
- 8.3.8 added eight more pools that neither 8.1 gateway has at all --
  `single-executor-*`, `shared-scheduled-executor-*`, `Scheduler-<hash>-*`,
  `managed-tag-provider-*`, `Cleaner-*` and the auth-token schedulers.
- 8.3.8 renamed store-and-forward from `gateway-storeforward-pipeline[...]`
  to `sf-engine[...]`, the tag provider from `tag-provider-*` to
  `standard-tag-provider-*`, and gateway network from `gateway-gn-*` to
  `gateway-network*`. Three more instances of the same trap, all of which
  would have read as a healthy flat zero.
- Both versions give each project its own
  `gateway-script-shared-timer-[<project>]-N` thread, which is what actually
  runs a gateway timer script. An earlier note in the `scripting` spec
  asserted the opposite; it was wrong and is corrected there.

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


# How strongly a spec is evidenced. This is a real distinction, not a label:
# a bucket built from a live dump is known to fire, and one built from a
# compiled string constant is only known to be SPELLED right.
#
# DUMP      the name was watched on a running gateway.
# CONSTANT  the name is a literal in a class that calls
#           com.inductiveautomation.ignition.common.execution.TPC
#           .newThreadFactory, but neither lab gateway runs that subsystem, so
#           it has never been seen to fire.
#
# Kept in the data because the tests treat the two differently: a DUMP spec
# that claims nothing anywhere is a typo and fails the build, while a CONSTANT
# spec claiming nothing is the expected state on a gateway without that module.
EVIDENCE_DUMP = "dump"
EVIDENCE_CONSTANT = "constant"


class PoolSpec(object):
    """One bucket in the catalog.

    key    tag-path segment and dict key. Lowercase, no spaces -- it becomes
           [default]GatewayHealth/Threads/Pools/<key>/Count.
    label  legend text on the Power Chart.
    matcher  a matchers.* object.
    why    what this pool actually does. Written for someone looking at a
           spiking trend who does not already know the subsystem.
    evidence  EVIDENCE_DUMP or EVIDENCE_CONSTANT. A CONSTANT spec MUST name
           the class its literals came from in `why`, so the claim can be
           re-checked against the jars rather than taken on trust.
    """

    def __init__(self, key, label, matcher, why, evidence=EVIDENCE_DUMP):
        self.key = key
        self.label = label
        self.matcher = matcher
        self.why = why
        self.evidence = evidence

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
            # 8.3.8, both watched live: standard-tag-provider-default-1.
            # `tag-provider` above does NOT match it -- the name is prefixed
            # with the provider KIND, so a bucket built on 8.1 spellings
            # alone trends a flat zero here on 8.3.
            matchers.prefix("standard-tag-provider-"),
            matchers.prefix("config-tag-provider"),
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
            # 8.3.8 renamed store-and-forward. Watched live as
            # sf-engine[postgresql]-scan and -forward-executor-N; the literals
            # sf-engine[%s]-scan, sf-engine[%s]-maintenance and
            # sf-engine[%s]-forward-executor-%s are all in
            # gateway.storeforward.engine.PipelineEngine. `contains` because
            # the datasource name is interpolated in the middle.
            matchers.contains("sf-engine["),
            # 8.3.8 Historian module. Watched live as data-collector-worker-1
            # and data-collector-scheduler-1; literals are in
            # historian-gateway-api-1.3.8.jar.
            matchers.prefix("data-collector-"),
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
            # Watched live on 8.3.8 as PostgreSQL-JDBC-Cleaner, whose literal
            # lives in the PostgreSQL JDBC driver jar itself. `contains`
            # because the vendor name leads, so every JDBC driver that follows
            # this convention is covered rather than just the one this lab
            # happens to run.
            matchers.contains("-JDBC-Cleaner"),
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
        matchers.any_of(
            matchers.prefix("perspective-"),
            # perspective.gateway.GatewayHook$PerspectiveGatewayContext names
            # one with a DOT, not a dash: "perspective.property-changes".
            matchers.prefix("perspective."),
        ),
        "Perspective session workers and scheduler. Scales with concurrent "
        "sessions, so this trend read next to webserver separates 'lots of "
        "users' from 'one slow request'.",
    ),

    # --- the two buckets the lab gateways cannot demonstrate ---------------
    #
    # Neither of these fires on either lab gateway: there are no alarm
    # pipelines configured and no field devices connected. They are here
    # because a real gateway almost always has both, and without them every
    # alarm and driver thread lands in `other` -- where a genuine problem
    # looks like an unidentified blob rather than a named subsystem.
    #
    # Marked EVIDENCE_CONSTANT so the "no dead spec" test does not fail the
    # build for a bucket that is legitimately empty here. See that test.

    PoolSpec(
        "alarming",
        "Alarming and notification",
        matchers.any_of(
            # alarming.pipelines.SingleThreadAlarmPipeline:
            #   "alarm-notification-pipeline[%s]", "gateway-alarm-pipelines-misc"
            # alarming.pipelines.blocks.DelayBlock:
            #   "alarm-notification-pipeline-delay-schedule"
            matchers.prefix("alarm-notification-"),
            matchers.prefix("gateway-alarm-"),
            # alarming.notification.email.EmailNotificationProfile:
            #   "alarm-notification-email-profile[%s]", "pop3-poll"
            matchers.prefix("pop3-poll"),
            # alarming.notification.sip.call.CallManager:
            #   "sip-callqueue-transfer", "sip-registration-job"
            matchers.prefix("sip-"),
        ),
        "Alarm pipelines and notification profiles -- email, SMS and SIP "
        "voice. Every alarm pipeline gets its own thread "
        "(SingleThreadAlarmPipeline), so this count tracks configured "
        "pipelines rather than load, and a pipeline BLOCKED here means alarms "
        "are not going out. Names taken from the literals in "
        "SingleThreadAlarmPipeline, DelayBlock, EmailNotificationProfile and "
        "CallManager; not seen live because this lab has no pipelines.",
        EVIDENCE_CONSTANT,
    ),

    PoolSpec(
        "drivers",
        "Device drivers",
        matchers.any_of(
            # xopc.driver.api.AbstractDriver:      "drivers-%s-%s", "drivers.%s"
            # xopc.driver.api.BasicRequestCycle:   "drivers-request-cycle-%s"
            # xopc.driver.api.AbstractSocketDriver:
            #     "drivers-%s[%s]-asyncsocketiosession"
            # xopc.drivers.common.AbstractSocketListener:
            #     "drivers-tcpudp-socket-listener"
            matchers.prefix("drivers-"),
            matchers.prefix("drivers."),
            # drivers.bacnet.LocalDeviceManager
            matchers.prefix("bacnet-"),
            matchers.prefix("driver-bacnet"),
        ),
        "Field device drivers -- Modbus, Allen-Bradley, Siemens, DNP3, BACnet "
        "and the rest -- and their request cycles and socket listeners. "
        "Distinct from `opcua`, which is the OPC-UA stack itself. A driver "
        "request cycle backing up is a device that stopped answering, and it "
        "shows here before it shows anywhere a user would notice. Names taken "
        "from the literals in the xopc driver API; not seen live because this "
        "lab has no devices connected.",
        EVIDENCE_CONSTANT,
    ),

    PoolSpec(
        "scripting",
        "Gateway scripting",
        matchers.any_of(
            # Covers gateway-scripts-pylib-watcher/-notifier AND
            # gateway-script-shared-timer-[<project>]-N. Note the missing 's':
            # the timer threads are `gateway-script-`, the library watchers
            # `gateway-scripts-`.
            matchers.prefix("gateway-script"),
            # common.script.ScheduledScriptManager, literal
            # "gateway-scheduled-scripts-[%s]".
            matchers.prefix("gateway-scheduled-scripts-"),
            # gateway.script.GatewaySystemUtilities -- this is
            # system.util.invokeAsynchronous.
            matchers.prefix("script-invoke-async"),
            # gateway.tags.scripting.TagScriptManagerImpl on 8.3.8,
            # gateway.tags.TagProviderImpl on 8.1.11. Tag CHANGE scripts:
            # bucketed by what the thread does (runs user script) rather than
            # by the subsystem that owns it, because that is the question
            # someone watching this trend is asking.
            matchers.prefix("gateway-tags-eventscripts"),
        ),
        "Everything that runs user script: project library watching and "
        "reload notification, gateway timer scripts, scheduled scripts, "
        "tag event scripts and system.util.invokeAsynchronous. "
        "CORRECTION: an earlier version of this note claimed gateway timer "
        "scripts run on the scheduler pool. They do not -- both 8.1.11 and "
        "8.3.8 give each project its own "
        "gateway-script-shared-timer-[<project>]-N thread, watched live on "
        "both. If this pool is pinned, a script is running long.",
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
            # Broadened from -scheduler-: OIDCProviderManager also names an
            # "internal-remembered-subjects-worker".
            matchers.prefix("internal-remembered-subjects-"),
            matchers.exact("File Reaper"),
            # 8.3.8 gateway-network. Replaces gateway-gn-, which is absent
            # there -- the same version-rename trap as milo- / opc-ua-, found
            # the same way. GatewayNetworkManagerImpl also names
            # "cert-chain-store".
            matchers.prefix("gateway-network"),
            matchers.prefix("cert-chain-store"),
            # gateway.http.HttpClientManagerImpl. Lowercase, unlike the
            # "HttpClient-" threads the JDK names.
            matchers.prefix("http-client"),
            matchers.prefix("idle-connection-evictor"),
            # IgnitionGateway's own singletons.
            matchers.prefix("gateway-eventbus"),
            matchers.prefix("system-init"),
            matchers.prefix("auth-tokens"),
            matchers.prefix("idp-relay"),
            # auth.oauth2.* -- oauth2-client, oauth2-relay,
            # oauth2-token-lifecycle, managed-oauth2-client.
            matchers.prefix("oauth2-"),
            matchers.prefix("managed-oauth2-"),
            # AutomaticThreadDumpManager. Worth naming rather than leaving in
            # `other`: if it is running, the gateway is dumping its own
            # threads because something already went wrong.
            matchers.prefix("automatic-thread-dump-manager"),
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
