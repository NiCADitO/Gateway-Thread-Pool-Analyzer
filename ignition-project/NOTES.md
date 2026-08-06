# Gateway resource layout — probed, not assumed

Facts established by inspecting live gateways. Anything not verified says so.
CLAUDE.md #5 forbids inventing an Ignition file format; this file records what
was actually observed so nobody has to re-derive it.

Gateways used:

| Container | Version | JVM |
|---|---|---|
| `81-GW1-1` | 8.1.11 | OpenJDK 11.0.11 |
| `81-GW2-1` | 8.3.8 | OpenJDK 17.0.19 |

---

## Project library (script-python) — CONFIRMED on 8.1.11

```
data/projects/<project>/ignition/script-python/<pkg>/<module>/code.py
data/projects/<project>/ignition/script-python/<pkg>/<module>/resource.json
```

- Only the **leaf** carries `resource.json`; intermediate package folders need none.
- `scope` is `"A"` for library scripts (views use `"G"`).
- `lastModificationSignature` can be 64 zeros — the gateway recomputes it and
  logs the resource as externally modified.
- `__init__.py` must **not** be pushed. Ignition ignores a script named
  `__init__` outright.
- `src/thread_monitor/sampler.py` → importable as `from thread_monitor import
  sampler`, identical to the import the tests use.

Verified end to end: 9 resources pushed, gateway logged
`Setting LastModification to "external"` for each and then
`Restarting gateway scripts...` **5 seconds** later. (The scanner observed up
to several minutes on the same gateway, so 5s is a floor, not a guarantee —
`wait_for_ingest()` still earns its place.)

**UNCONFIRMED on 8.3.** `81-GW2-1` has no projects at all; its
`data/projects/` contains only a `.resources` directory. Nothing can be said
about 8.3's layout until a project exists there.

---

## Gateway timer scripts — plain Python on 8.3 (earlier conclusion was wrong)

I originally concluded these could not be synthesized, reasoning from the
sibling resources on 8.1.11 (`ignition/global-props/`,
`com.inductiveautomation.vision/client-tags/`) which store `resource.json`
plus a **binary `data.bin`**. That inference was wrong.

Once a timer script existed on 8.3.8, it turned out to be **readable Python**:

```
ignition/timer/<name>/handleTimerEvent.py
ignition/timer/<name>/resource.json
```

```json
{
  "scope": "G",
  "version": 1,
  "restricted": false,
  "overridable": true,
  "files": ["handleTimerEvent.py"],
  "attributes": {
    "sharedThread": true,
    "delay": 10000,
    "fixedDelay": true,
    "enabled": true,
    "lastModification": {"actor": "ad", "timestamp": "..."},
    "lastModificationSignature": "..."
  }
}
```

The body is a function, tab-indented, not a bare statement list:

```python
def handleTimerEvent():
	from ignition_adapter import entry
	entry.sample_and_write()
```

So timer scripts **can** be generated on 8.3. `delay` was changed from 1000 to
10000 by editing this file directly and the gateway honoured it after a
restart — confirming both that the format is right and that the file is the
source of truth at boot.

Fixed **Delay**, not Fixed Rate: the next sample cannot start until the last
finished, so a slow sample degrades resolution instead of stacking up.
`entry.sample_and_write()` also carries its own reentrancy guard.

### It does NOT transfer to 8.1, and the failure is silent

Tested by generating the same resource on 8.1.11. The gateway **accepted** it:

```
I [g.IgnitionProjectManager]: Setting LastModification to "external" on
  Gateway_Thread_Pool_Analyzer_and_Historizer/threadMonitor [ignition/timer]
I [Project]: Restarting gateway scripts... project=...
```

It even recomputed `lastModificationSignature` from zeros to a real hash — so
the resource was genuinely read and processed. And it **never executed**, not
after the rescan and not after a full gateway restart. No error, anywhere.

So `ignition/timer/` is an **8.3 change**; 8.1 keeps gateway event scripts in
the older binary `event-scripts` type. My original inference was right for 8.1
and wrong for 8.3, and each version had to be tested to find that out.

**On 8.1 the timer must be created in the Designer.** A generated one there is
inert while looking installed, which is worse than no timer at all. The
inert resource was removed from GW1 rather than left as a trap.

This is a one-time cost per gateway: the body is three lines and never
changes, because all the real code lives in the project library — which *is*
pushable on 8.1.

---

## 8.3 does NOT watch the project directory — CONFIRMED

This is the single most important operational difference between the two
versions, and it silently makes a deploy look like it worked.

| | 8.1.11 | 8.3.8 |
|---|---|---|
| External file push | logs `Restarting gateway scripts...` within **5s** | **never** picked up |
| Waited | — | 240s, then a further 60s after editing a file the Designer itself wrote |
| On gateway restart | n/a | loads from disk **immediately** |

8.3 reloads on notification (a Designer save logs
`Restarting gateway scripts... project=..., collection=...`), not from a
filesystem watch. Its project store is content-addressed —
`data/projects/.resources/` holds hash-named entries plus a `.meta` — and 8.3
also migrated its whole gateway config out of `config.idb` into
`data/config/resources/` (see the migration log in that directory).

**Consequence:** `scripts/build_project_library.py` needs `--restart` on 8.3.
Without it the push lands on disk, the script reports success, and the gateway
goes on running the previous code — indistinguishable from the change not
working, which is the same trap `wait_for_ingest()` exists to catch on 8.1.

---

## Live verification of the whole chain — 8.3.8

With the library deployed and the timer running, the gateway logged:

```
W [GatewayThreadMonitor]: 82 of 82 writes rejected,
  first: [default]GatewayHealth/Threads/Pools/webserver/Count (Bad_NotFound)
  (110 threads, 31ms)
```

Four things confirmed at once, all previously assumptions:

1. **Gateway scope is real.** 110 threads is the gateway's own count. The
   standalone Jython harness sees 6, so the numbers themselves distinguish the
   two — a report from the wrong JVM would have looked plausible.
2. **The 8.3 restart deploy path works.**
3. **`writeBlocking` does not raise for a nonexistent tag.** It returns
   `Bad_NotFound` per path while the call itself succeeds — exactly as
   `stubs.py` documents. `tags.py` inspects every QualityCode for this reason;
   without that, a completely unprovisioned tag tree reports as a clean write.
4. **Sample cost on a real gateway: 31ms first, then 13ms.** Against 4-5ms on
   the 6-thread test JVM. Comfortable at a 10s timer.

---

## Tags — pending

`system.tag.configure(basePath, tags, collisionPolicy)` takes plain dicts and
returns a `QualityCode` per tag, so unlike a binary resource it fails *loudly*
and diagnosably. That makes programmatic provisioning viable here.

Still to confirm on a live gateway:

- exact collision-policy letters accepted on 8.1 and 8.3
- whether a UDT definition can be created by `configure` or must be built in
  the Designer and exported first
- whether history settings survive a `configure` on an existing UDT instance

Until those are answered, `TODO(human)` markers stay in the provisioning code.

---

## Running code in Gateway scope

The only routes that execute in the **gateway's** JVM:

| Route | Usable here |
|---|---|
| Gateway Event Script (timer) | **yes** — needs the one Designer step above |
| Perspective session | yes, but wiring a view means synthesizing `view.json` |
| WebDev endpoint | module not installed |
| Designer Script Console | **no** — runs in the *Designer's* JVM |

That last row matters and is easy to get wrong: a thread report from the
Script Console looks entirely plausible and is the wrong JVM's threads.

`scripts/verify_on_jython.py` sidesteps all of this for *code* verification —
it runs `src/` under the gateway's own `jython-ia-2.7.2.0.jar` against a real
`ThreadMXBean`. It proves the code and the Java API contract; it does not and
cannot prove scope.
