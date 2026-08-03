# gateway-thread-monitor

Trend an Ignition gateway's JVM thread pools over time, so you can see thread
pressure days or weeks after it happened.

Ignition's Status page shows a live thread list: this instant, and nothing
before it. It cannot tell you what the webserver pool looked like an hour ago,
whether store-and-forward has been climbing all week, or what else was running
the last time a pool blocked. By the time a user complains, the evidence is
gone.

This project samples the gateway JVM's threads on a timer, sorts them into 14
pools, counts each pool by `java.lang.Thread.State`, and writes the result to
historized Ignition tags. "The gateway feels slow" becomes a trend you can
point at.

Runs on Ignition **8.1 and 8.3**. At runtime it reads the JVM and writes only
its own tags. It changes nothing else on the gateway.

## What it looks like

Point the discovery script at a gateway container to see how it buckets that
gateway's live threads. This writes nothing:

```bash
python scripts/discover_threads.py 81-GW1-1
```

```
Threads: 119 total

POOL            COUNT  RUNNABLE  BLOCKED  WAITING TIMED_WAITING
---------------------------------------------------------------
webserver          14         6        0        0             8
executor           18         0        0       12             6
scheduler           9         0        0        2             7
tags                7         0        0        5             2
history             9         0        0        0             9
database            5         0        0        1             4
opcua              13         5        0        2             6
perspective         7         0        0        0             7
alarming            0         0        0        0             0
drivers             0         0        0        0             0
scripting           3         0        0        2             1
platform           25         9        0        5            11
jvm                 9         7        0        1             1
other               0         0        0        0             0
```

Deployed, those same numbers land in tags every 10 seconds and become a Power
Chart.

## Install and run

You need Python 3 on the machine you run the scripts from, and Docker access
to the gateway container. The gateway itself needs no extra modules.

Run the test suite. It needs no gateway, no containers, and no database:

```bash
python -m pytest
```

Deploy to an 8.1 gateway, creating all 81 tags:

```bash
python scripts/build_project_library.py --deploy --provision \
  --history-provider PostgresDBConnection \
  --container 81-GW1-1 --project Gateway_Thread_Pool_Analyzer_and_Historizer
```

8.3 needs two extra flags, because it does not watch its project directory and
it can accept a generated timer script:

```bash
python scripts/build_project_library.py --deploy --restart --with-timer --provision \
  --history-provider PostgreSQLHistorian \
  --container 81-GW2-1 --project Gateway_Thread_Pool_Analyzer_and_Historizer
```

On 8.1 you must also create the gateway timer event script once, by hand in
the Designer. 8.1 stores event scripts in a compressed binary file that cannot
be generated safely.

Confirm it is working:

```bash
docker logs 81-GW1-1 2>&1 | grep GatewayThreadMonitor
```

A healthy gateway logs this every five minutes:

```
I [GatewayThreadMonitor]: sample 30: 81 tags written (115 threads, 4ms)
```

[docs/operations.md](docs/operations.md) covers the Designer step, the
Perspective dashboard, gateways without a historian, and how to prove history
rows are really landing.

## The pool catalog

Every thread lands in exactly one bucket. First match wins, and `other` catches
whatever the catalog does not recognise, so the per-pool counts always sum to
the total. On all five captured dumps, `other` is zero.

| Bucket | Matches | Why you would watch it |
|---|---|---|
| `webserver` | `webserver-*` | Jetty request handlers. A pinned webserver pool is what "the gateway feels slow" actually looks like. The single most useful trend here. |
| `executor` | `gateway-shared-exec-engine-*`, `shared-worker-*`, `platform-executor-*`, `ForkJoinPool*` | The general work pool. A backlog means something else is blocking. |
| `scheduler` | `platform-scheduled-executor-*`, `cron4j::*`, `shared-scheduler-*`, `Timer-*` | Scheduled reports, polling tags, fixed-rate work. Growth means a task is overrunning its own interval. |
| `tags` | `tag-provider*`, `standard-tag-provider-*`, `config-tag-provider*`, `tag-group-manager*`, `gateway.tags.*` | Tag providers and group execution. Blocked threads here stall tag evaluation gateway-wide. |
| `history` | `tags-history-*`, `gateway-storeforward-*`, `*sf-engine[*`, `data-collector-*` | Early warning for a historian that cannot keep up with its own ingest. |
| `database` | `gateway-db-connection-validator-*`, `mysql-cj-*`, `*-JDBC-Cleaner`, `HSQLDB Timer*`, `Connection evictor` | Connection pool validation and eviction. Churn means connections are dropping. |
| `opcua` | `milo-*`, `opc-ua-*` | The OPC-UA stack itself. Spikes track device connection churn. |
| `perspective` | `perspective-*`, `perspective.*` | Session workers. Read next to `webserver` to tell "many users" apart from "one slow request". |
| `alarming` (see note) | `alarm-notification-*`, `gateway-alarm-*`, `sip-*`, `pop3-poll*` | One thread per alarm pipeline. A blocked thread here means alarms are not going out. |
| `drivers` (see note) | `drivers-*`, `drivers.*`, `bacnet-*` | Field device drivers and their request cycles. A backed-up request cycle is a device that stopped answering. |
| `scripting` | `gateway-script*`, `gateway-scheduled-scripts-*`, `gateway-tags-eventscripts*`, `script-invoke-async*` | Everything that runs user script, including gateway timer scripts. Each project gets its own `gateway-script-shared-timer-[<project>]` thread. |
| `platform` | logging, file and certificate watchers, service wrapper, gateway network, auth and OAuth, Jetty housekeeping | Constant-count background threads. Deliberately kept out of `webserver`. |
| `jvm` | JIT compiler, reference handling, cleaners | `GC Thread#*` and `VM Thread` never appear here in practice. See below. |
| `other` | everything else | Expected to be zero. A rising count means a new pool appeared. Read `Diagnostics/UnmatchedNames`. |

**Note on `alarming` and `drivers`.** Neither has ever been seen to fire.
Neither lab gateway has an alarm pipeline configured or a field device
connected. Their names come from string constants compiled into the classes
that call `TPC.newThreadFactory`, not from a thread dump. The catalog marks
them `EVIDENCE_CONSTANT`, which exempts them from the "no dead bucket" test.
A second test asserts they stay silent, so the moment either matches a real
thread, the suite tells you to promote it. See
[how the constants were found](docs/development.md#finding-pools-that-are-not-running-at-all).

Adding a bucket means adding one `PoolSpec` to
[taxonomy.py](src/thread_monitor/taxonomy.py) and nothing else. Provisioning
walks the catalog, so its five tags come with it. You never touch the sampler.

## What the real thread dumps found

The catalog comes from five thread dumps taken off live gateways running three
Ignition versions, not from documentation. That decision paid for itself
immediately.

**Thread names drift hard between versions.** 8.1.48 renamed the OPC-UA pool
from `milo-*` to `opc-ua-*`, and replaced `gateway-shared-exec-engine-*` with
`shared-worker-*`. 8.3.8 renamed store-and-forward to `sf-engine[...]`, the tag
provider to `standard-tag-provider-*`, and gateway network to
`gateway-network*`. Of every prefix in the catalog, only `webserver-` is stable
across all three versions. A catalog built from one gateway trends a flat zero
on the others, and a flat zero reads as "healthy", not as "broken measurement".

**`ThreadMXBean` does not see VM-internal threads.** A `kill -3` dump on 8.1.11
listed 130 threads; `getAllThreadIds()` reports 117. The 13 missing ones
(`GC Thread#*`, `G1 *`, `VM Thread`, `VM Periodic Task Thread`) carry no
`java.lang.Thread.State` line. The fixtures exclude them, because otherwise
every expected count would be quietly wrong.

**Some threads are periodic.** `gateway-log-maintenance` was absent from the
first 8.1.11 capture and present ten minutes later. Capture more than once.

**Some threads only appear under load.** `designer-auth-token-worker-*` shows
up on 8.3 only once a Designer connects, so its prefix stops before the role.

## Tag layout

```
[default]GatewayHealth/Threads/
  Pools/<bucket>/{Count, Runnable, Blocked, Waiting, TimedWaiting}
  TotalCount  PeakCount  DaemonCount  DeadlockedCount  BlockedTotal
  PoolTable                     (DataSet, feeds the Perspective table)
  Diagnostics/{SampleDurationMs, LastSampleTime, LastError,
               ApiRoute, UnmatchedNames}
```

That is 81 flat memory tags, created by `system.tag.configure`. 75 of them are
historized. The 5 `Diagnostics` tags and the `PoolTable` dataset are
deliberately not: `LastSampleTime` changes on every single sample by
definition, so historizing it would add more rows per day than all the real
metrics combined and degrade on-change logging into fixed-periodic.

There is no UDT. The plan originally called for one, and it was dropped. Its
only real benefit was configuring history in one place, which a `for` loop
already gives you. Keeping it would have meant depending on `_types_` semantics
and on history propagating from a UDT definition into its instances, and the
live-gateway probe could only mark both of those *inferred*, never verified.

`NEW` and `TERMINATED` are folded into `Count` rather than given their own
tags. Two flat lines across every pool are not worth 28 more historized tags.
If either state ever does occur, `Count` exceeds the sum of the four state
members, which is visible rather than silently dropped.

## Repository layout

```
src/thread_monitor/     pure Python core. No Ignition, no Java, fully testable
src/ignition_adapter/   the only place that touches system.* or java.*
scripts/                host tooling: capture, build, deploy
tests/fixtures/*.tsv    real thread dumps off real gateways
ignition-project/       Designer-exported artifacts
docs/                   operations and development guides
```

The boundary between the first two directories is the point. Nothing under
`src/thread_monitor/` may reference `system.` or `java.` at all, so the whole
counting algorithm is testable with no gateway. `src/` is Jython 2.7 because
that is what the gateway runs; `tests/` is CPython 3 and unconstrained. A test
enforces both rules by scanning the syntax tree.

See [CLAUDE.md](CLAUDE.md) for the hard constraints and
[docs/development.md](docs/development.md) for how captures and verification
work.

## Status

All milestones are complete and verified on live gateways. 137 tests pass, and
none of them need a gateway.

Verified on Ignition 8.1.11 (Java 11), 8.1.48 (Java 17) and 8.3.8 (Java 17).
A sample takes 3 to 7 ms in steady state, once every 10 seconds. The first
sample after a gateway restart is slower, around 30 to 60 ms, because the
module is still warming up.

## Origin

This started from [a forum post](https://forum.inductiveautomation.com/t/getting-threads-from-the-gateway/89447/13)
that counts `webserver` threads with `ManagementFactory.getThreadMXBean()`. The
idea is right. This project rebuilds around it in four ways:

- It uses `system.tag.writeBlocking` instead of the deprecated
  `system.tag.write`.
- It uses a catalog of pools instead of one hardcoded prefix.
- It runs in Gateway scope. The original used a Perspective component
  reference (`self.parent.parent`), which cannot resolve in a gateway timer at
  all. That is the most likely cause of the timer trouble the poster reported.
- Its `TIMED_WAITING` count means what the tag name says. In the posted
  snippet, the state check sits inside the `startswith("webserver")` branch, so
  the tag named `TIMED_WAITING` actually counts only webserver threads in that
  state. Here every state is counted per pool, and the totals reconcile.

One detail the post got right, which is easy to assume otherwise: its
`getThreadInfo(ids)` call is already the cheap form. The JavaDoc defines it as
equivalent to `getThreadInfo(ids, 0)`, which captures no stack traces. This
code uses the explicit two-argument form only so that the intent is visible at
the call site.

## Getting help and contributing

Open an issue on
[the GitHub repository](https://github.com/NiCADitO/Gateway-Thread-Pool-Analyzer/issues).

If you are adding a pool bucket, run `python scripts/discover_threads.py`
against your gateway first and commit the dump as a fixture. The test suite
picks up any new `.tsv` in `tests/fixtures/` automatically, so your gateway's
thread names become part of the corpus that keeps the catalog honest.
