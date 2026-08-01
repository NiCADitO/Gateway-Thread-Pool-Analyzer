# CLAUDE.md

Conventions and hard constraints for this repo. Read before touching anything.

## What this is

A thread-pool monitor for Ignition gateways. It samples the gateway JVM's live
threads on a timer, buckets them by pool, counts them by `java.lang.Thread.State`,
and writes the result to historized tags so gateway health can be trended.

It exists because a gateway gives you no historical view of its own thread
pools: the Status page shows a live list, and the moment a pool saturates
there is no record of when it started or whether it is getting worse.

Targets Ignition **8.1 and 8.3**.

---

## Hard constraints

Violating any of these means rework, not a code review comment.

### 1. `src/thread_monitor/` never touches Ignition or Java

Nothing under `src/thread_monitor/` may `import system`, `import java.*`, or
reference `system.` at all. It takes a list of `(thread_name, state_name)`
string pairs and returns data structures.

That boundary is the only reason the whole catalog can be tested against real
captured thread dumps with no gateway running. `tests/test_architecture_boundary.py`
enforces it. Do not weaken it or add exemptions.

The pressure to break it is real and specific: comparing against the actual
`java.lang.Thread.State` enum looks tidier than comparing strings. It is not
worth the test suite.

`src/ignition_adapter/` is the only place that touches `system.*` or `java.*`.
If it grows logic, that logic belongs in `thread_monitor/`.

### 2. `src/` is Jython 2.7, not Python 3

Everything under `src/` runs on Jython 2.7 inside a gateway. Do not use:

- f-strings (use `%` formatting)
- type hints or variable annotations
- list, dict, or set comprehensions -- write the loop out
- `pathlib`, `dataclasses`, `typing`, `enum`, `asyncio`
- walrus operator, keyword-only arguments, `nonlocal`, argument-less `super()`

`tests/` and `scripts/` run on CPython 3 and may use modern syntax freely. The
boundary is `src/` versus everything else. Enforced by
`tests/test_jython_compatibility.py`.

### 3. Bare `except:` in `ignition_adapter/`, always

On Jython, a failure from `system.*` or `java.lang.management.*` arrives as a
`java.lang.Exception`, which does **not** subclass Python's `Exception`. So
`except Exception:` there catches nothing and the error escapes.

This repo is more exposed to it than most: `jvm.py`'s whole design is to try an
API route, catch it failing, and fall through to the next. With
`except Exception` the failure escapes, the gateway timer dies, and every
CPython test still passes because the test doubles raise real Python exceptions.

### 4. Never guess a `system.*` or Java signature

`src/ignition_adapter/stubs.py` documents the exact signature and return type
of every Ignition scripting function and every `java.lang.management` call this
project relies on. If what you need is not there, add a
`TODO(human): confirm signature for x.y`, stub it, and move on.

A wrong `ThreadMXBean` call compiles fine and fails on a production gateway,
which is the worst available place to discover it.

### 5. Never invent an Ignition file format

Do not generate or hand-edit tag export JSON, `resource.json` manifests,
Perspective `view.json`, or `project.json` from scratch. Build one by hand in
the Designer, export it, commit it, and have the code *edit that known-good
shape*. A synthesized file that fails to import is not diagnosable from the
file itself.

### 6. Never add a prefix to the taxonomy without evidence

Every matcher in `src/thread_monitor/taxonomy.py` must come from a real thread
dump committed under `tests/fixtures/`. Run
`python scripts/discover_threads.py <container>` and read the output.

A guessed prefix that never matches produces a bucket that trends a flat zero,
and a flat zero on a chart reads as "this subsystem is healthy" rather than
"this measurement is broken."

---

## Architecture

```
src/
  thread_monitor/         pure Python, no Ignition, fully unit-testable
    matchers.py           Prefix/Exact/Contains/AnyOf name matchers
    taxonomy.py           POOL_SPECS -- the pool catalog, as data
    sampler.py            count(samples) -> Snapshot. The whole algorithm.
    snapshot.py           value objects + flatten_for_write + format_report
    tagpaths.py           every tag path, in one place
  ignition_adapter/       the ONLY place that references system.* or java.*
    stubs.py              signature inventory + CPython test doubles
    jvm.py                ThreadMXBean access, probed per version
    tags.py               writeBlocking / configure wrappers
    entry.py              what the timer script and script console call
ignition-project/         Designer-exported artifacts (read-mostly)
scripts/                  CPython host tooling: capture, build, deploy
tests/                    CPython 3
  fixtures/*.tsv          real thread dumps off real gateways
```

**Adding a pool means appending one `PoolSpec` and one UDT instance. It never
means editing `sampler.py`.**

---

## Testing

`python -m pytest`. No gateway, no database, no containers required -- that is
the point of the boundary in constraint #1.

Fixtures are real captures, one per gateway version. Any new `.tsv` dropped in
`tests/fixtures/` is picked up automatically by the parametrized invariant
tests, so capturing a new gateway is a one-file change.

**Cross-version rules that cost real debugging to learn:**

- "No PoolSpec is dead" is asserted against the **union** of all fixtures, not
  per-fixture. A gateway with no datasources legitimately has no history or
  store-and-forward threads. Asserting per-fixture pushes someone to delete
  those specs, which then reads as "no store-and-forward problems" on a
  gateway that has plenty.
- Thread names **do** drift across versions. 8.1.48 renamed the OPC-UA pool
  from `milo-*` to `opc-ua-*` and replaced `gateway-shared-exec-engine-*` with
  `shared-worker-*`. Both spellings are kept. Do not "clean up" the one your
  gateway does not use.
- Some threads are **periodic**. `gateway-log-maintenance` was absent from the
  first capture off 8.1.11 and present in the second ten minutes later. A
  catalog built from a single instant misses threads that are not always up.

---

## Scope guards

Do not add any of these without asking first:

- publishing samples to MQTT/Sparkplug, or any UNS integration
- a Prometheus endpoint, or any external scrape target
- heap, GC, or CPU metrics -- this project counts threads
- stack-trace capture, or anything using `getThreadInfo` with a depth > 0
- per-thread detail tags, or anything whose tag count scales with thread count
- writing to any tag outside `[default]GatewayHealth/Threads/`

---

## Commits

Small and single-purpose. Prefix with the area: `taxonomy:`, `sampler:`,
`tags:`, `adapter:`, `scripts:`, `fixtures:`, `docs:`.
