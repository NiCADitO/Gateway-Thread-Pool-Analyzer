# Development

## Running the tests

```bash
python -m pytest
```

No gateway, no containers, no database. That is deliberate — see constraint #1
in [CLAUDE.md](../CLAUDE.md). If a test starts needing a gateway, the logic it
tests probably belongs in `thread_monitor/` rather than `ignition_adapter/`.

---

## Capturing a thread dump off a live gateway

```bash
python scripts/discover_threads.py <container>                       # summarise
python scripts/discover_threads.py <container> --out tests/fixtures/threads_83.tsv
```

Without `--out` it prints how the *current* catalog buckets that gateway's
threads, including anything unmatched. That is the fast way to find a pool the
catalog has never seen.

### How it works, and why not the obvious way

The Ignition container ships a **JRE, not a JDK** — there is no `jstack` and no
`jcmd` to attach with:

```
$ docker exec 81-GW1-1 ls lib/runtime/jre-nix/bin
java  jfr  jjs  jrunscript  keytool  pack200  rmid  rmiregistry  unpack200
```

So instead: `kill -3` on the gateway JVM makes HotSpot print a full thread dump
to its own stdout, and the container symlinks `logs/wrapper.log` to
`/dev/stdout`, so it lands in `docker logs`. Nothing to install, nothing to
expose, no gateway credentials.

`kill -3` is a diagnostic signal, not a termination signal — the JVM prints and
carries on. Both lab gateways stayed healthy across repeated captures.

**The JVM is not pid 1.** Pid 1 is the service wrapper; the JVM is its child.
`discover_threads.py` finds it by scanning for `/bin/java`.

### Why the fixtures exclude some threads

A dump lists VM-internal threads — `GC Thread#0`, `G1 Refine#0`, `VM Thread`,
`VM Periodic Task Thread` — that carry no `java.lang.Thread.State:` line.
`ThreadMXBean.getAllThreadIds()` does **not** report them.

On 8.1.11 the dump held 130 names; ThreadMXBean's view is the 117 with a state
line, which matches the JVM's own SMR thread-list length exactly. Fixtures keep
only those 117. Letting the other 13 in would make the fixture describe a
thread set the live sampler can never see, and every count derived from it
would be quietly wrong.

`tests/test_sampler.py::test_vm_internal_threads_are_absent_from_the_fixture`
guards this.

### Capture more than once

`gateway-log-maintenance` was absent from the first capture off 8.1.11 and
present in a second one ten minutes later. Periodic threads are not visible in
a single instant. Run the script a few times before concluding the catalog is
complete.

---

## Verifying against a real gateway

```bash
python scripts/verify_on_jython.py 81-GW1-1
```

The CPython suite proves the logic. It cannot prove the two things most likely
to break on a gateway and invisible from CPython: that every module actually
imports under **Jython 2.7**, and that `java.lang.management` behaves the way
`stubs.py` says. This runs `src/` inside the container with the gateway's own
interpreter (`lib/core/common/jython-ia-2.7.2.0.jar`) against a real
`ThreadMXBean`, and asserts the contract.

**It does not prove scope.** It starts a new JVM inside the container, so it
reports that little JVM's half-dozen threads, not the gateway's. Whether the
code runs in *Gateway* scope is a deployment question — answered by the
Gateway Event Script at M4.

Two flags it needs, both non-obvious:

- `-Dpython.import.site=false` — outside the gateway's own configured
  `python.home` the bundled jar has no `site` module on `sys.path` and dies
  before running a line of our code.
- The JVM is invoked with `-cp <jar> org.python.util.jython`; there is no
  `jython` launcher script in the image.

### What it found

Both confirmed empirically on 8.1.11 **and** 8.3.8:

| Check | Result |
|---|---|
| Every module imports under Jython 2.7.2 | pass |
| `findDeadlockedThreads()` with no deadlock | returns `None`, **not** `[]` — as documented |
| `deadlocked_count` maps that to | `0`, not `None` |
| `getThreadInfo(ids, 0)` | works, no stack traces |
| Sample cost | 4–5 ms warm (39 ms first call, JIT) |

And one thing worth knowing:

- **`Thread.State` crosses the boundary as `unicode`, not `str`.** Harmless —
  under Python 2 `u'RUNNABLE'` compares and hashes equal to `'RUNNABLE'`, so
  `snapshot.PoolCount`'s dict lookups behave identically. But it means
  `isinstance(state, str)` is **False** on a gateway and **True** on CPython 3.
  A test asserting that would pass here and fail there;
  `test_states_cross_the_boundary_as_text_not_as_a_java_enum` deliberately
  does not.

---

## Adding a pool bucket

1. Capture a dump: `python scripts/discover_threads.py <container>`.
2. Read the **Unmatched** list at the bottom of the output.
3. Append a `PoolSpec` to `POOL_SPECS` in
   [taxonomy.py](../src/thread_monitor/taxonomy.py) — before `other`, which
   must stay last. Write the `why`: it is read off a spiking chart by someone
   who does not know the subsystem.
4. `python -m pytest`. The invariant tests run against every fixture
   automatically.
5. Re-run provisioning. The five new tags are created from the catalog; there
   is no Designer step and no UDT to edit.

Never add a prefix you have not seen in a real dump. A guessed prefix produces
a bucket that trends a flat zero, and a flat zero reads as "healthy".

---

## Adding a gateway to the corpus

Drop a new `.tsv` in `tests/fixtures/`. The parametrized invariant tests pick
it up with no other change.

Then check two things:

- `other` should be 0. If not, add specs for what turns up.
- The union test `test_no_spec_is_dead_across_the_whole_corpus` should still
  pass. If a bucket is empty *only* on the new gateway, that is usually
  configuration (no datasources → no history threads), not version drift.
  Leave the spec alone.

---

## Known gateway facts

Recorded here so they are not re-derived. Anything unverified says so.

| Fact | Verified on |
|---|---|
| Container JRE has no `jstack`/`jcmd`; `kill -3` + `docker logs` works | 8.1.11, 8.1.48, 8.3.8 |
| `wrapper.log` is a symlink to `/dev/stdout` | 8.1.11, 8.1.48, 8.3.8 |
| JVM is a child of pid 1 (the wrapper), not pid 1 | 8.1.11, 8.1.48, 8.3.8 |
| `ThreadMXBean` omits GC/VM-internal threads (130 → 117) | 8.1.11 |
| 13 VM-internal threads excluded on every gateway captured | all three |
| OpenJDK 11 on 8.1.11; OpenJDK 17 on 8.1.48 and 8.3.8 | all three |
| OPC-UA pool renamed `milo-*` → `opc-ua-*` | 8.1.11 vs 8.1.48 |
| `gateway-shared-exec-engine-*` → `shared-worker-*` | 8.1.11 vs 8.1.48 |
| 8.3 adds `single-executor-*`, `shared-scheduled-executor-*`, `Scheduler-<hash>-*`, `managed-tag-provider-*`, `Cleaner-*`, auth-token pools | 8.3.8 |
| `designer-auth-token-worker-*` appears only once a Designer connects | 8.3.8 |
| `Scheduler-<hash>-N` carries a per-boot hash — only the prefix is stable | 8.3.8 |
| Project library layout `ignition/script-python/<pkg>/<mod>/code.py` | 8.1.11 **and** 8.3.8 |
| 8.1 rescans project resources on a timer (5–180 s observed) | 8.1.11 |
| **8.3 does NOT watch the project directory** — loads from disk at boot only | 8.3.8 |
| Timer event scripts: binary `ignition/event-scripts/data.bin` (gzip) | 8.1.11 |
| Timer event scripts: plain `ignition/timer/<name>/handleTimerEvent.py` | 8.3.8 |
| Tag config: `config.idb` on 8.1, files under `data/config/resources` on 8.3 | both |
| Historian provider config lives in `core/com.inductiveautomation.historian/historian-provider/<name>/` | 8.3.8 |
| `system.tag.configure` / `exists` / `writeBlocking` identical across versions | both |
| 8.3 REMOVED `system.tag.read/readAll/write/writeAll` | 8.3.8 |
| Jython 2.7.2 on 8.1.11, **2.7.4** on 8.3.8 | both |

All rows are now closed. The version differences that bite are the two in
**bold**: an external push to 8.3 is ignored while reporting success, and a
generated timer resource on 8.1 is ingested and then never executed.

### Thread counts on an idle gateway

Useful as a baseline when reading a trend. All three gateways were idle with
no significant load:

| | 8.1.11 | 8.1.48 | 8.3.8 |
|---|---|---|---|
| total | 117 | 105 | 101 |
| webserver | 15 | 16 | 10 |
| executor | 17 | 19 | 16 |
| opcua | 13 | 12 | 19 |
| platform | 26 | 30 | 21 |

8.1.11 is the only one of the three with datasources configured, which is why
it is the only one with non-zero `history`.
