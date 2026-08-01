"""Run the real code under the gateway's own Jython 2.7 and real ThreadMXBean.

The CPython test suite proves the logic. It cannot prove the two things most
likely to break on a gateway and impossible to see from CPython:

  1. that every module actually parses and imports under Jython 2.7, and
  2. that `java.lang.management` behaves the way stubs.py says it does.

This closes both. It copies src/ into the gateway container and executes it
with the exact interpreter the gateway uses --
`lib/core/common/jython-ia-2.7.2.0.jar` -- against a genuine ThreadMXBean.

WHAT THIS DOES NOT PROVE: scope. It runs in a NEW JVM inside the container,
so it reports that little JVM's half-dozen threads, not the gateway's. Whether
the code is running in Gateway scope is a deployment question, answered by the
Gateway Event Script at M4, not by this.

Usage:
    python scripts/verify_on_jython.py 81-GW1-1
"""
import argparse
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")
REMOTE = "/tmp/gateway-thread-monitor-verify"
JYTHON_GLOB = "/usr/local/bin/ignition/lib/core/common/jython-ia-*.jar"

PROBE = '''
from ignition_adapter import entry, jvm
from thread_monitor import taxonomy

print("=== entry.dump() ===")
print(entry.dump())

snap = jvm.read()
bean = jvm.get_bean()
raw = bean.findDeadlockedThreads()
pairs = jvm.collect_pairs(bean, [])

print("")
print("=== contract checks ===")
print("findDeadlockedThreads() raw   -> %r" % (raw,))
print("  ... must be None when healthy, NOT an empty list")
print("deadlocked_count              -> %r (expect 0, not None)"
      % (snap.deadlocked_count,))
print("peak / daemon                 -> %r / %r"
      % (snap.peak_threads, snap.daemon_threads))
print("sample_duration_ms            -> %r (%s)"
      % (snap.sample_duration_ms, type(snap.sample_duration_ms).__name__))
print("state text type              -> %s"
      % (type(pairs[0][1]).__name__,))
print("api_route                     -> %r" % (snap.api_route,))
print("last_error                    -> %r" % (snap.last_error,))
print("pool specs loaded             -> %d" % (len(taxonomy.POOL_SPECS),))

failures = []
if raw is not None:
    failures.append("findDeadlockedThreads returned %r, not None" % (raw,))
if snap.deadlocked_count != 0:
    failures.append("deadlocked_count is %r, expected 0"
                    % (snap.deadlocked_count,))
if snap.total_threads < 1:
    failures.append("counted no threads at all")
if snap.last_error:
    failures.append("last_error was set: %s" % (snap.last_error,))

print("")
if failures:
    print("FAILED:")
    for item in failures:
        print("  - " + item)
else:
    print("OK: imports, ThreadMXBean contract and counting all behave "
          "as stubs.py documents.")
'''


def run(container, args, stdin=None):
    return subprocess.run(["docker", "exec"] + (["-i"] if stdin else [])
                          + [container] + args,
                          stdin=stdin, capture_output=True, text=True,
                          errors="replace")


def push(container):
    subprocess.check_call(["docker", "exec", container, "sh", "-c",
                           "rm -rf %s && mkdir -p %s" % (REMOTE, REMOTE)])
    pushed = 0
    for dirpath, _dirnames, filenames in os.walk(SRC):
        relative = os.path.relpath(dirpath, SRC).replace(os.sep, "/")
        remote_dir = REMOTE if relative == "." else "%s/%s" % (REMOTE, relative)
        subprocess.check_call(["docker", "exec", container, "sh", "-c",
                               "mkdir -p '%s'" % (remote_dir,)])
        for name in filenames:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(dirpath, name), "rb") as handle:
                # Streamed, not `docker cp`: Docker Desktop for Windows
                # rewrites the destination path and the copy fails.
                subprocess.check_call(
                    ["docker", "exec", "-i", container, "sh", "-c",
                     "cat > '%s/%s'" % (remote_dir, name)],
                    stdin=handle)
            pushed += 1
    return pushed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container", help="gateway container, e.g. 81-GW1-1")
    args = parser.parse_args()

    jar = run(args.container, ["sh", "-c", "ls %s 2>/dev/null | head -1"
                               % (JYTHON_GLOB,)]).stdout.strip()
    if not jar:
        raise SystemExit("no Jython jar found at %s in %s"
                         % (JYTHON_GLOB, args.container))
    print("interpreter: %s" % (jar,))

    print("pushed %d modules" % (push(args.container),))

    with open(os.path.join(REPO_ROOT, "src", "_probe_tmp.py"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)
    try:
        with open(os.path.join(REPO_ROOT, "src", "_probe_tmp.py"), "rb") as fh:
            subprocess.check_call(
                ["docker", "exec", "-i", args.container, "sh", "-c",
                 "cat > %s/_probe.py" % (REMOTE,)], stdin=fh)
    finally:
        os.remove(os.path.join(REPO_ROOT, "src", "_probe_tmp.py"))

    # -Dpython.import.site=false: the bundled jar has no `site` module on its
    # sys.path outside the gateway's own configured python.home, and without
    # this it dies before running a line of our code.
    result = run(args.container, [
        "sh", "-c",
        "cd %s && java -Dpython.import.site=false -cp %s "
        "org.python.util.jython _probe.py" % (REMOTE, jar)])
    print(result.stdout)
    if result.stderr.strip():
        print(result.stderr, file=sys.stderr)
    if "FAILED" in result.stdout or result.returncode != 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
