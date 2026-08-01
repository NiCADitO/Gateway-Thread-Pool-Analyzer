"""Capture a live gateway's thread names into a test fixture.

This is M0, and it is the reason the pool catalog is trustworthy. Every
prefix in `src/thread_monitor/taxonomy.py` came out of a run of this script
against a real gateway rather than out of documentation.

How it works, and why this way:

The Ignition container ships a JRE, not a JDK -- there is no `jstack` and no
`jcmd` to attach with. But `kill -3` on the gateway JVM makes HotSpot print a
full thread dump to its own stdout, and the container symlinks
`logs/wrapper.log` to `/dev/stdout`, so the dump lands in `docker logs`. That
needs nothing installed, nothing exposed, and no gateway credentials.

`kill -3` is a diagnostic signal, not a termination signal. The JVM prints the
dump and carries on; both lab gateways stayed healthy across repeated runs.

**Only threads with a `java.lang.Thread.State:` line are kept.** A dump also
lists VM-internal threads -- `GC Thread#0`, `G1 Refine#0`, `VM Thread`,
`VM Periodic Task Thread` -- which carry no state line and which
`ThreadMXBean.getAllThreadIds()` does not report. Keeping them would make the
fixture describe a thread set the live sampler can never see, and every count
derived from it would be quietly wrong. On 8.1.11 the dump held 130 names and
the fixture holds the 117 that ThreadMXBean sees.

Usage:
    python scripts/discover_threads.py 81-GW1-1
    python scripts/discover_threads.py 81-GW1-1 --out tests/fixtures/threads_81_11.tsv

Runs on CPython 3 on the host -- it is tooling, not gateway code, so the
Jython constraints in CLAUDE.md do not apply here.
"""
import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAME_RE = re.compile(r'^"(?P<name>.*)"\s')
STATE_RE = re.compile(r"java\.lang\.Thread\.State:\s+(?P<state>[A-Z_]+)")
WRAPPER_PREFIX_RE = re.compile(r"^jvm \d+\s*\|[^|]*\|\s?")
DUMP_HEADER_RE = re.compile(r"Full thread dump (?P<vm>.+?)\s*:")


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          errors="replace")


def java_pid(container):
    """The gateway JVM's pid, which is not pid 1 -- that is the wrapper."""
    result = _run(["docker", "exec", container, "sh", "-c",
                   "ps -eo pid,args"])
    if result.returncode != 0:
        raise SystemExit("could not list processes in %s:\n%s"
                         % (container, result.stderr.strip()))
    for line in result.stdout.splitlines():
        if "/bin/java" in line and "grep" not in line:
            return line.split()[0]
    raise SystemExit("no java process found in %s -- is the gateway running?"
                     % (container,))


def log_line_count(container):
    result = _run(["docker", "logs", container])
    return len((result.stdout + result.stderr).splitlines())


def capture(container, wait_seconds=5):
    """Trigger a dump and return (new log lines, vm description)."""
    pid = java_pid(container)
    before = log_line_count(container)

    result = _run(["docker", "exec", container, "kill", "-3", pid])
    if result.returncode != 0:
        raise SystemExit("kill -3 failed: %s" % (result.stderr.strip(),))

    # HotSpot writes the dump asynchronously; the wrapper then relays it.
    import time
    time.sleep(wait_seconds)

    result = _run(["docker", "logs", container])
    lines = (result.stdout + result.stderr).splitlines()
    fresh = lines[before:]
    if not fresh:
        raise SystemExit(
            "no new log output after kill -3. The dump goes to the JVM's "
            "stdout via logs/wrapper.log -- if that symlink is missing on "
            "this image, capture the dump another way.")

    cleaned = []
    for line in fresh:
        cleaned.append(WRAPPER_PREFIX_RE.sub("", line))

    vm = "unknown"
    for line in cleaned:
        found = DUMP_HEADER_RE.search(line)
        if found:
            vm = found.group("vm")
            break
    return cleaned, vm


def parse(lines):
    """Pair each thread name with its state. Nameless-state threads dropped.

    A thread's state is on the line after its name. Anything without one is a
    VM-internal thread, which ThreadMXBean will not report -- see the module
    docstring.
    """
    pairs = []
    dropped = []
    pending = None
    for line in lines:
        found = NAME_RE.match(line)
        if found:
            if pending is not None:
                dropped.append(pending)
            pending = found.group("name")
            continue
        found = STATE_RE.search(line)
        if found and pending is not None:
            pairs.append((pending, found.group("state")))
            pending = None
    if pending is not None:
        dropped.append(pending)
    return pairs, dropped


def write_fixture(path, pairs, container, vm, dropped):
    header = [
        "# Live thread dump from an Ignition gateway.",
        "#",
        "# Gateway:  container %s" % (container,),
        "# JVM:      %s" % (vm,),
        "# Captured: scripts/discover_threads.py",
        "#",
        "# %d threads. This is exactly the set ThreadMXBean.getAllThreadIds()"
        % (len(pairs),),
        "# reports. The dump also held %d VM-internal thread(s) with no"
        % (len(dropped),),
        "# java.lang.Thread.State line, which ThreadMXBean does not report;",
        "# they are deliberately excluded. See scripts/discover_threads.py.",
        "#",
        "# Format: <thread name>\\t<Thread.State>",
    ]
    body = []
    for name, state in sorted(pairs):
        body.append("%s\t%s" % (name, state))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header + body) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container",
                        help="gateway container name, e.g. 81-GW1-1")
    parser.add_argument("--out",
                        help="write a fixture here instead of summarising")
    parser.add_argument("--wait", type=int, default=5,
                        help="seconds to wait for the dump (default 5)")
    args = parser.parse_args()

    lines, vm = capture(args.container, args.wait)
    pairs, dropped = parse(lines)

    if not pairs:
        raise SystemExit("captured %d log lines but parsed no threads -- the "
                         "dump format may differ on this image" % (len(lines),))

    print("%s: %d threads (%s)" % (args.container, len(pairs), vm))
    print("excluded %d VM-internal thread(s) with no Thread.State: %s"
          % (len(dropped), ", ".join(sorted(dropped)) or "none"))

    if args.out:
        path = args.out
        if not os.path.isabs(path):
            path = os.path.join(REPO_ROOT, path)
        write_fixture(path, pairs, args.container, vm, dropped)
        print("wrote %s" % (os.path.relpath(path, REPO_ROOT),))
        print("now run: python -m pytest")
        return 0

    # No --out: report what the current catalog would do with it, which is
    # the fast way to find a pool the catalog has never seen.
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from thread_monitor import sampler, snapshot
    print()
    print(snapshot.format_report(sampler.count(pairs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
