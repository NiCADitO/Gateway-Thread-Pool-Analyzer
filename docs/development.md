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

## Adding a pool bucket

1. Capture a dump: `python scripts/discover_threads.py <container>`.
2. Read the **Unmatched** list at the bottom of the output.
3. Append a `PoolSpec` to `POOL_SPECS` in
   [taxonomy.py](../src/thread_monitor/taxonomy.py) — before `other`, which
   must stay last. Write the `why`: it is read off a spiking chart by someone
   who does not know the subsystem.
4. `python -m pytest`. The invariant tests run against every fixture
   automatically.
5. Add a UDT instance for the new bucket (M3).

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
| Project library layout `ignition/script-python/<pkg>/<mod>/code.py` | 8.1.11 only — **unconfirmed on 8.3** |
| Gateway rescans project resources on a timer, up to several minutes | 8.1.11 only |

The last two rows are the open ones. They gate M6.

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
