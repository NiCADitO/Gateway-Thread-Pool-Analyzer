# Operations

How to put this on a gateway, and how to tell whether it is working.

---

## Deploying

### Ignition 8.1

```bash
python scripts/build_project_library.py --deploy --provision \
  --history-provider PostgresDBConnection \
  --container 81-GW1-1 --project Gateway_Thread_Pool_Analyzer_and_Historizer
```

8.1 watches its project directory and reloads within seconds (observed 5–180 s;
the script waits and tells you when it lands).

**One-time Designer step on 8.1.** Create the gateway timer event script by
hand — 8.1 stores event scripts as a single gzip-compressed
`ignition/event-scripts/data.bin`, which cannot be generated. Gateway Events →
Timer → `threadMonitor`, **10000 ms**, **Fixed Delay**, shared thread:

```
from ignition_adapter import entry
entry.sample_and_write()
```

That body never changes — all the real code lives in the project library,
which *is* pushable. So this is once per gateway, ever.

### Ignition 8.3

```bash
python scripts/build_project_library.py --deploy --restart --with-timer --provision \
  --history-provider <YourHistorianName> \
  --container 81-GW2-1 --project Gateway_Thread_Pool_Analyzer_and_Historizer
```

Two differences, both load-bearing:

- **`--restart` is mandatory.** 8.3 does *not* watch the project directory.
  Files land on disk, the push reports success, and the gateway keeps running
  the previous code until it reloads at boot. Verified: an external edit went
  unnoticed for 5 minutes and loaded instantly on restart.
- **`--with-timer` works here.** 8.3 stores timer scripts as plain
  `handleTimerEvent.py` + `resource.json`, so no Designer step at all.
  It does **not** work on 8.1 — there the resource is accepted, logged, its
  signature recomputed, and then never executed.

**Where the provider name lives on 8.3.** It is the *historian provider*, not
the datasource, and the two are usually spelled differently. 8.3 keeps it as a
file, so you can read it rather than click through the web UI:

```bash
docker exec 81-GW2-1 sh -c 'ls /usr/local/bin/ignition/data/config/resources/core/com.inductiveautomation.historian/historian-provider/'
```

In this lab that returns `PostgreSQLHistorian`, whose `config.json` names the
datasource `PostgreSQL`. Passing the datasource name instead would create 64
tags with a provider that does not exist — and they would look historized.

### A gateway with no historian

```bash
... --provision --history-provider NONE
```

Creates all 69 tags with live values and no history. The log says
`(NO HISTORY -- live values only)` so it cannot be mistaken for a gateway that
is trending. A *blank* provider is still refused — blank is what an unset
config looks like, and treating it as "no history wanted" is how you get 64
tags that look historized and store nothing.

---

## Is it working?

Three independent signals, in increasing order of how much they prove.

### 1. The gateway log

```bash
docker logs <container> 2>&1 | grep GatewayThreadMonitor
```

Healthy looks like this, once every 5 minutes:

```
I [GatewayThreadMonitor]: sample 30: 69 tags written (105 threads, 4ms)
```

Not-yet-provisioned looks like this:

```
W [GatewayThreadMonitor]: 69 of 69 writes rejected, first:
  [default]GatewayHealth/Threads/Pools/webserver/Count (Bad_NotFound)
```

Logging is deduplicated on the **fault**, not the message, so a persistent
problem is stated once and then only on the heartbeat. A change of state —
including recovery — is logged immediately. Silence for more than ~5 minutes
means the timer is dead, not that everything is fine.

### 2. The tags move

Tag browser → `[default]GatewayHealth/Threads/`. Watch `Pools/webserver/Count`
over ~30 s. Also check:

- `Diagnostics/SampleDurationMs` stays single-digit
- `Diagnostics/LastError` stays empty
- `Diagnostics/UnmatchedNames` is empty — anything in it is a thread pool the
  catalog has never seen, and is a prompt to add a `PoolSpec`
- the sum of all `Pools/*/Count` equals `TotalCount`

### 3. History rows are landing

This is the one that actually matters, because every other signal can look
healthy while the historian stores nothing.

```bash
docker exec postgresct psql -U ignition -d test -c "SELECT te.tagpath, count(*) AS rows, min(d.intvalue) AS lo, max(d.intvalue) AS hi, to_timestamp(max(d.t_stamp)/1000) AS last FROM sqlth_1_data d JOIN sqlth_te te ON d.tagid=te.id WHERE te.tagpath ILIKE 'gatewayhealth%' GROUP BY 1 ORDER BY 2 DESC LIMIT 15;"
```

Expect **64** distinct series — not 69. The five `Diagnostics` tags are
deliberately not historized: `LastSampleTime` is a timestamp so it changes
every single sample by definition, and historizing it plus `SampleDurationMs`
would add ~12,960 rows/day against ~11,109 for all 64 real metrics combined —
54% of rows carrying nothing trendable, and it would degrade on-change back
into fixed-periodic.

**Find your partition table first — it is not the same on both gateways.**

The table name is `sqlt_data_<drvid>_<YYYY>_<MM>` with monthly partitioning, or
`sqlth_<drvid>_data` when partitioning is *disabled*. `drvid` identifies the
**gateway**, not the database:

```bash
docker exec postgresct psql -U ignition -d test -c "SELECT id, name, provider FROM sqlth_drv ORDER BY id;"
docker exec postgresct psql -U ignition -d test -c "SELECT pname, to_timestamp(start_time/1000) FROM sqlth_partitions ORDER BY start_time DESC LIMIT 4;"
```

In this lab both gateways point at the *same* `test` database, so the historian
holds both and separates them by driver:

| Gateway | drvid | Partitioning | Table |
|---|---|---|---|
| 8.1.11 `gw1` | 1 | disabled | `sqlth_1_data` |
| 8.3.8 `gw2` | 3 | monthly | `sqlt_data_3_2026_08` |

That is worth knowing before you conclude a gateway "isn't historizing":
querying `sqlth_1_data` for gw2's data returns zero rows and looks exactly like
a broken pipeline. Two gateways sharing one historian is normal and fine — the
tag paths are identical, so it is only `drvid` that tells them apart.

---

## The Power Chart

A Designer step, deliberately: `view.json` is one of the formats CLAUDE.md #5
forbids synthesizing, because a broken one is not diagnosable from the file.

Perspective → new view → **Power Chart**. In the chart's tag browser add:

```
[default]GatewayHealth/Threads/Pools/webserver/Count
[default]GatewayHealth/Threads/Pools/executor/Count
[default]GatewayHealth/Threads/Pools/history/Count
[default]GatewayHealth/Threads/TotalCount
```

Then, on a **second axis**, the one that earns the whole project:

```
[default]GatewayHealth/Threads/Pools/*/Blocked
```

`Blocked` is flat zero on a healthy gateway. It going non-zero is the event
worth alarming on, and it is invisible in a thread *count* — a saturated pool
and an idle one have the same size.

Set the chart's **render mode to stepped/discrete**, not interpolated. These
are step functions, and history is stored with `historicalDeadbandStyle:
Discrete` for the same reason: interpolation would draw a straight ramp
between two on-change points up to five minutes apart, inventing values that
never existed and turning a spike into a gentle slope.

### Reading it

| What you see | What it means |
|---|---|
| `webserver/Count` climbing and staying up | Jetty is holding threads — slow requests, or more concurrent users. Check `perspective/Count` to tell those apart. |
| any `*/Blocked` above zero | Lock contention. The pool name says which subsystem. |
| `history/Count` trending up over days | Store-and-forward is not keeping up with its own ingest — usually a slow or unreachable historian database. |
| `TotalCount` up but no single pool up | Look at `other/Count` and `Diagnostics/UnmatchedNames`; a new pool may have appeared. |
| a series flat-lining while others move | Suspect the measurement, not the gateway. A flat zero usually means a `PoolSpec` prefix stopped matching after an upgrade. |

That last row is why the catalog carries every historical spelling of a pool
name rather than just the one your gateway uses.

---

## Re-provisioning

Safe to re-run any time. It is idempotent: leaves are overwritten with the
complete desired configuration, folders use collision policy `Ignore` (what
`Overwrite` does to a folder's *children* is undocumented, and the blast
radius would be all twelve pools plus their history).

Nothing is ever deleted. Removing a `PoolSpec` leaves its tags behind as
orphans rather than risking a recursive delete — clean those up by hand if you
care.

After adding a `PoolSpec`, just redeploy with `--provision`. The five new tags
come from the catalog; there is no Designer step and no UDT to edit.
