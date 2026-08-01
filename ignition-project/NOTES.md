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

## Gateway event scripts — CANNOT be synthesized

`event-scripts` is a real resource type (found in the gateway jars). But the
payload is **not** plain Python on disk.

Evidence: the sibling resources that do exist on 8.1.11 —
`ignition/global-props/` and `com.inductiveautomation.vision/client-tags/` —
each store `resource.json` **plus a binary `data.bin`**. Perspective views, by
contrast, use readable JSON. Gateway event scripts follow the `data.bin` shape.

So the timer script must be created **once in the Designer**. A synthesized
binary resource would fail opaquely, which is exactly the failure mode
CLAUDE.md #5 exists to prevent.

This is a one-time cost: the script body is two lines and never changes,
because all the real code lives in the project library, which *is* pushable.

```python
# Gateway Events > Timer > threadMonitor
#   Delay: 10000 ms   Mode: Fixed Delay   Threading: Shared   Enabled: yes
from ignition_adapter import entry
entry.sample_and_write()
```

Fixed **Delay**, not Fixed Rate: the next sample cannot start until the last
finished, so a slow sample degrades resolution instead of stacking up.
`entry.sample_and_write()` also carries its own reentrancy guard.

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
