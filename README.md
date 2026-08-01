# gateway-thread-monitor

Trend an Ignition gateway's thread pools over time.

The gateway's Status page shows you a live thread list. It does not show you
what the webserver pool looked like an hour ago, whether store-and-forward has
been climbing all week, or what else was happening the last time things went
blocked. This samples the gateway JVM's threads on a timer, buckets them by
pool, counts them by state, and writes the result to historized tags — so
"the gateway feels slow" becomes a trend you can point at.

Targets Ignition **8.1 and 8.3**. Reads only; the only thing it writes is its
own tags.

---

## Status

| Milestone | State | What it proves |
|---|---|---|
| **M0** Thread-name discovery | **done** | Real thread names captured off three live gateways — 8.1.11, 8.1.48 and 8.3.8. The catalog is evidence-based, not guessed. |
| **M1** Pure counting core | **done** | 49 tests, no gateway required. `other` is empty on all three real gateways. |
| **M2** Ignition adapter | **done** | 69 tests. Runs under the gateway's real Jython 2.7.2 against a real `ThreadMXBean` on both 8.1.11 and 8.3.8. Writes nothing. |
| M3 Tag provisioning | **needs one Designer step** | ~69 tags exist under `[default]GatewayHealth/Threads/`. |
| M4 Gateway timer | **needs one Designer step** | Values move on a 10 s fixed-delay timer in Gateway scope. |
| M5 History + trend | | Rows in `sqlt_data_*`, Power Chart renders. **The deliverable.** |
| M6 Deploy harness | | One-command deploy; verified on 8.3. |

---

## The pool catalog

Twelve buckets. Every thread lands in exactly one, first match wins, and
`other` catches the rest so the totals always reconcile.

| Bucket | Matches | Why you'd watch it |
|---|---|---|
| `webserver` | `webserver-*` | Jetty request handlers. Pinned here means the gateway feels slow to humans. **The most useful single trend.** |
| `executor` | `gateway-shared-exec-engine-*`, `shared-worker-*`, `platform-executor-*`, `ForkJoinPool*` | The general work pool. A backlog means something else is blocking. |
| `scheduler` | `platform-scheduled-executor-*`, `cron4j::*`, `shared-scheduler-*`, `Timer-*` | Gateway timer scripts, scheduled reports, polling tags. Growth means a task is overrunning its interval. |
| `tags` | `tag-provider*`, `tag-group-manager*`, `gateway.tags.*` | Tag providers and group execution. Blocked here stalls tag evaluation gateway-wide. |
| `history` | `tags-history-*`, `gateway-storeforward-*` | Early warning for a historian that can't keep up with its own ingest. |
| `database` | `gateway-db-connection-validator-*`, `mysql-cj-*`, `HSQLDB Timer*`, `Connection evictor` | Connection pool validation. Churn means connections dropping. |
| `opcua` | `milo-*`, `opc-ua-*` | The OPC-UA stack. Spikes track device connection churn. |
| `perspective` | `perspective-*` | Session workers. Read next to `webserver` to separate "many users" from "one slow request". |
| `scripting` | `gateway-scripts-*` | Project library watching. Note: gateway timer scripts run on `scheduler`, not here. |
| `platform` | logging, file/cert watchers, wrapper, gateway network, Jetty housekeeping | Constant-count background threads. Kept out of `webserver` on purpose. |
| `jvm` | JIT compiler, reference handling, cleaners | Note `GC Thread#*` and `VM Thread` are **not** here in practice — see below. |
| `other` | everything else | Expected near zero. Rising means a new pool appeared. |

Adding a bucket is one `PoolSpec` in [taxonomy.py](src/thread_monitor/taxonomy.py)
and nothing else. Provisioning walks the catalog, so the tags come with
it. It never means touching the sampler.

---

## What capturing three gateways found

The catalog was built from real dumps off 8.1.11, 8.1.48 and 8.3.8, not from
documentation. That immediately paid for itself:

- **Thread names drift hard across versions.** 8.1.48 renamed the OPC-UA pool
  from `milo-*` to `opc-ua-*` and replaced `gateway-shared-exec-engine-*` with
  `shared-worker-*`. 8.3.8 added eight pools neither 8.1 gateway has at all.
  Of every prefix in the catalog, `webserver-` is the only one stable across
  all three. A catalog built from one gateway trends a **flat zero** on the
  others — and a flat zero reads as "healthy", not "broken measurement".
- **Some threads only appear under load.** `designer-auth-token-worker-*`
  shows up on 8.3 only once a Designer connects, so the prefix stops before
  the role.
- **`ThreadMXBean` does not see VM-internal threads.** A `kill -3` dump on
  8.1.11 listed 130 threads; `getAllThreadIds()` reports 117. The 13 missing
  ones (`GC Thread#*`, `G1 *`, `VM Thread`, `VM Periodic Task Thread`) carry no
  `java.lang.Thread.State` line. Fixtures exclude them, or every expected count
  would be quietly wrong.
- **Some threads are periodic.** `gateway-log-maintenance` was absent from the
  first 8.1.11 capture and present ten minutes later.

---

## Tag layout

```
[default]GatewayHealth/Threads/
  Pools/<bucket>/{Count, Runnable, Blocked, Waiting, TimedWaiting}
  TotalCount   PeakCount   DaemonCount   DeadlockedCount
  Diagnostics/{SampleDurationMs, LastSampleTime, LastError,
               ApiRoute, UnmatchedNames}
```

69 flat memory tags, created by `system.tag.configure`. **No UDT** — the plan
originally called for one, and it was dropped: its only real benefit was
"history configured in one place", which a `for` loop already gives you.
Keeping it would have meant depending on `_types_` semantics and
definition-to-instance history propagation, both of which the live-gateway
probe could only mark *inferred*, never verified.

`NEW` and `TERMINATED` are folded into `Count` rather than given their own
tags: two flat lines across every pool isn't worth ~24 historized tags. If they
ever do occur, `Count` exceeds the sum of the four state members — visible,
rather than silently dropped.

---

## Usage

Capture a gateway's threads and see how the current catalog buckets them:

```bash
python scripts/discover_threads.py 81-GW1-1
```

Save a capture as a test fixture:

```bash
python scripts/discover_threads.py 81-GW1-1 --out tests/fixtures/threads_81_11.tsv
```

Run the tests — no gateway, no containers, no database:

```bash
python -m pytest
```

---

## Layout

```
src/thread_monitor/     pure Python core; no Ignition, no Java, fully testable
src/ignition_adapter/   the only place that touches system.* or java.*
scripts/                host tooling: capture, build, deploy
tests/fixtures/*.tsv    real thread dumps off real gateways
ignition-project/       Designer-exported artifacts (read-mostly)
```

See [CLAUDE.md](CLAUDE.md) for the hard constraints and
[docs/development.md](docs/development.md) for how captures work.

---

## Origin

Based on [a forum post](https://forum.inductiveautomation.com/t/getting-threads-from-the-gateway/89447/13)
that counts `webserver` threads via `ManagementFactory.getThreadMXBean()`. The
idea is right; this rebuilds around it:

- **`writeBlocking`** instead of the deprecated `system.tag.write`.
- **A catalog** instead of one hardcoded pool.
- **Gateway scope**, rather than a Perspective component reference
  (`self.parent.parent`) that cannot resolve in a gateway timer at all — the
  most likely cause of the timer trouble the poster reported.
- **A `TIMED_WAITING` count that means what its tag name says.** In the posted
  snippet the state check is nested inside the `startswith("webserver")`
  branch, so the tag called `TIMED_WAITING` actually only counts *webserver*
  threads in that state. Here every state is counted per pool and the totals
  reconcile.

One thing the post got right that's easy to assume otherwise: its
`getThreadInfo(ids)` call is *already* the cheap form. The JavaDoc defines it
as equivalent to `getThreadInfo(ids, 0)` — no stack traces. This code uses the
explicit two-argument form only because it says so at the call site.
