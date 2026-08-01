"""Map src/ into an Ignition Project Library resource tree, and push it.

Adapted from tag-history-linkage-scanner, parameterised for container and
project rather than hardcoded. Its two hard-won behaviours are kept verbatim
and are commented where they are, because both look like pointless complexity
until they bite.

Layout confirmed by probing an 8.1.11 gateway:

    ignition/script-python/<pkg>/<subpkg>/<module>/code.py
    ignition/script-python/<pkg>/<subpkg>/<module>/resource.json

Only the leaf carries a resource.json; intermediate package folders need none.

So `src/thread_monitor/sampler.py` becomes importable on the gateway as
`from thread_monitor import sampler` -- the same import the tests use, which
is why nothing in src/ needs a gateway-specific import shim.

`__init__.py` files are skipped. Ignition ignores a script named `__init__`
outright, which is why every `__init__.py` under src/ is a docstring and
nothing more (enforced by tests/test_jython_compatibility.py).

The signature in resource.json is left as zeros on purpose. The gateway
recomputes it and marks the resource externally modified.

UNCONFIRMED ON 8.3. The 8.3.8 gateway here has no projects yet and its
data/projects/ holds only a .resources directory, so the layout above is
verified on 8.1.11 only. Run with --project against an 8.3 project once one
exists and record the result in docs/development.md.

Usage:
    python scripts/build_project_library.py
    python scripts/build_project_library.py --deploy \
        --container 81-GW1-1 --project Gateway_Thread_Pool_Analyzer_and_Historizer

Runs on CPython 3 on the host -- tooling, not gateway code.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "src")
OUT = os.path.join(REPO_ROOT, "ignition-project", "generated", "script-python")

GATEWAY_DATA = "/usr/local/bin/ignition/data"

# Scope "A" is what the Designer writes for library scripts; views use "G".
RESOURCE = {
    "scope": "A",
    "version": 1,
    "restricted": False,
    "overridable": True,
    "files": ["code.py"],
    "attributes": {
        "lastModification": {"actor": "external",
                             "timestamp": "2026-07-31T21:00:00Z"},
        "lastModificationSignature": "0" * 64,
    },
}


def apply_config(history_provider, provision):
    """Rewrite the generated copy of ignition_adapter/config.py.

    src/ stays generic and committed with safe defaults; the per-gateway
    values are injected into the BUILD OUTPUT only. That way deploying to two
    gateways with different historians is two commands rather than two edits
    and a remembered revert.
    """
    target = os.path.join(OUT, "ignition_adapter", "config", "code.py")
    if not os.path.isfile(target):
        raise SystemExit("no generated config.py at %s" % (target,))

    source = io.open(target, encoding="utf-8").read()
    source = source.replace('HISTORY_PROVIDER = ""',
                            'HISTORY_PROVIDER = "%s"' % (history_provider,))
    if provision:
        source = source.replace("PROVISION_ON_START = False",
                                "PROVISION_ON_START = True")
    io.open(target, "w", encoding="utf-8", newline="\n").write(source)

    print("Config: history provider %r, provision-on-start %s"
          % (history_provider, bool(provision)))


def build():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)

    written = 0
    skipped = []
    for dirpath, _dirnames, filenames in os.walk(SRC):
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            if name == "__init__.py":
                skipped.append(os.path.relpath(os.path.join(dirpath, name),
                                               SRC))
                continue

            source_path = os.path.join(dirpath, name)
            relative = os.path.relpath(source_path, SRC)
            module_dir = os.path.join(OUT, os.path.splitext(relative)[0])
            os.makedirs(module_dir)

            code = io.open(source_path, encoding="utf-8").read()
            io.open(os.path.join(module_dir, "code.py"), "w",
                    encoding="utf-8", newline="\n").write(code)
            io.open(os.path.join(module_dir, "resource.json"), "w",
                    encoding="utf-8", newline="\n").write(
                        json.dumps(RESOURCE, indent=2) + "\n")
            written += 1

    print("Wrote %d script resources to %s"
          % (written, os.path.relpath(OUT, REPO_ROOT)))
    print("Skipped %d __init__.py (a gateway has no package initializer)"
          % (len(skipped),))
    return written


def _ingest_count(container):
    """How many times the gateway has reloaded project scripts so far.

    Counted rather than timestamped: `docker logs --since` interprets bare
    times in the host's local zone while the gateway logs in two zones at
    once, and getting that wrong reads as "never ingested".
    """
    try:
        out = subprocess.check_output(["docker", "logs", container],
                                      stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        return None
    if not isinstance(out, str):
        out = out.decode("utf-8", "replace")
    return out.count("Restarting gateway scripts")


def wait_for_ingest(container, before, timeout=240):
    """Block until the gateway picks the push up, or say that it has not.

    Writing the files is NOT deploying them. The gateway rescans its project
    directory on a timer -- observed at up to several minutes on 8.1.11 -- and
    until that fires it keeps running the code it loaded last time. Anything
    run in that window reports on the OLD code while every file on disk says
    otherwise, which is indistinguishable from the change not working.
    """
    if before is None:
        print("Could not read the gateway log, so ingestion was not waited "
              "for. The push landed on disk; the gateway will pick it up on "
              "its next project scan.")
        return False

    waited = 0
    while waited < timeout:
        time.sleep(5)
        waited += 5
        after = _ingest_count(container)
        if after is not None and after > before:
            print("Gateway ingested the push after %ds "
                  "(restarted project scripts)." % (waited,))
            return True
        sys.stdout.write("  waiting for the gateway to rescan... %ds\r"
                         % (waited,))
        sys.stdout.flush()

    print("\nNo ingestion seen in %ds. The files ARE on disk -- the gateway "
          "has not rescanned yet. Anything run before it does will use the "
          "PREVIOUS code." % (timeout,))
    return False


def _project_path(project):
    return "%s/projects/%s" % (GATEWAY_DATA, project)


def check_project(container, project):
    """Fail early and clearly if the project does not exist.

    Pushing into a nonexistent project silently creates an orphan directory
    the gateway never reads, and the symptom is "my code does not run" with
    every file present on disk.
    """
    path = _project_path(project)
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-c",
         "test -f '%s/project.json' && echo OK" % (path,)],
        capture_output=True, text=True)
    if "OK" not in result.stdout:
        listing = subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             "ls %s/projects/ 2>/dev/null" % (GATEWAY_DATA,)],
            capture_output=True, text=True)
        raise SystemExit(
            "no project '%s' on %s.\nProjects present: %s\n"
            "Create it in the Designer first -- this script does not invent "
            "project.json (CLAUDE.md #5)."
            % (project, container,
               " ".join(listing.stdout.split()) or "(none)"))


# ---------------------------------------------------------------------------
# Gateway timer event script
# ---------------------------------------------------------------------------
#
# Shape taken verbatim from a timer script the DESIGNER wrote on 8.3.8, not
# invented -- see ignition-project/NOTES.md. Confirmed by editing `delay` on
# disk from 1000 to 10000 and watching the gateway honour it.
#
# The body is tab-indented on purpose: that is what the Designer emits, and
# mixing tabs and spaces in a Jython file is a syntax error rather than a
# style opinion.

TIMER_NAME = "threadMonitor"

TIMER_BODY = (
    "def handleTimerEvent():\n"
    "\tfrom ignition_adapter import entry\n"
    "\tentry.sample_and_write()\n"
)


def timer_resource(delay_ms):
    return {
        "scope": "G",
        "version": 1,
        "restricted": False,
        "overridable": True,
        "files": ["handleTimerEvent.py"],
        "attributes": {
            "sharedThread": True,
            "delay": delay_ms,
            # Fixed delay, never fixed rate: the next sample cannot start
            # until the last finished, so a slow sample degrades resolution
            # instead of stacking samples on top of each other.
            "fixedDelay": True,
            "enabled": True,
            "lastModification": {"actor": "external",
                                 "timestamp": "2026-07-31T21:00:00Z"},
            "lastModificationSignature": "0" * 64,
        },
    }


def push_timer(container, project, delay_ms):
    """Create/replace the gateway timer event script. 8.3 ONLY.

    **This does not work on 8.1 and the failure is silent.** Tested on 8.1.11:
    the gateway ingested the resource, logged
    `Setting LastModification to "external" on .../threadMonitor
    [ignition/timer]`, and even recomputed its signature -- and then never
    executed it, not across a project rescan and not across a full gateway
    restart. 8.1 stores gateway event scripts as the older binary
    `event-scripts` type; `ignition/timer/` is an 8.3 change. An 8.1 resource
    pushed this way is inert, and looks installed.

    So on 8.1 the timer must be created once in the Designer. That is a
    one-time cost -- the body is three lines and never changes, because all
    the real code lives in the project library, which IS pushable on 8.1.
    """
    remote = "%s/ignition/timer/%s" % (_project_path(project), TIMER_NAME)
    subprocess.check_call(["docker", "exec", container, "sh", "-c",
                           "mkdir -p '%s'" % (remote,)])

    payloads = [
        ("handleTimerEvent.py", TIMER_BODY),
        ("resource.json", json.dumps(timer_resource(delay_ms), indent=2)
         + "\n"),
    ]
    for filename, content in payloads:
        proc = subprocess.Popen(
            ["docker", "exec", "-i", container, "sh", "-c",
             "cat > '%s/%s'" % (remote, filename)],
            stdin=subprocess.PIPE)
        proc.communicate(content.encode("utf-8"))
        if proc.returncode != 0:
            raise SystemExit("failed writing %s" % (filename,))

    print("Pushed timer '%s' at %dms (fixed delay) to %s:%s"
          % (TIMER_NAME, delay_ms, container, project))
    print("  verify with: docker logs %s 2>&1 | grep GatewayThreadMonitor"
          % (container,))


def restart_and_wait(container, timeout=300):
    """Restart the gateway and block until it reports healthy.

    This is the 8.3 deploy path. 8.1 watches its project directory and picks
    external writes up within seconds; 8.3 does NOT -- see the --restart help
    and ignition-project/NOTES.md for the evidence. On 8.3 the files sit on
    disk being ignored until the gateway reloads them at boot.
    """
    print("Restarting %s (8.3 does not watch the project directory)..."
          % (container,))
    subprocess.check_call(["docker", "restart", container],
                          stdout=subprocess.DEVNULL)

    waited = 0
    while waited < timeout:
        time.sleep(5)
        waited += 5
        probe = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            capture_output=True, text=True)
        status = probe.stdout.strip()
        if status == "healthy":
            print("Gateway healthy after %ds." % (waited,))
            return True
        sys.stdout.write("  waiting for the gateway... %ds (%s)\r"
                         % (waited, status or "?"))
        sys.stdout.flush()

    print("\nGateway did not report healthy in %ds." % (timeout,))
    return False


def deploy(container, project):
    check_project(container, project)
    target = "%s/ignition/script-python" % (_project_path(project),)
    subprocess.check_call(["docker", "exec", container, "sh", "-c",
                           "mkdir -p %s" % (target,)])

    before = _ingest_count(container)
    pushed = 0
    for dirpath, _dirnames, filenames in os.walk(OUT):
        if "code.py" not in filenames:
            continue
        relative = os.path.relpath(dirpath, OUT).replace(os.sep, "/")
        remote = "%s/%s" % (target, relative)
        subprocess.check_call(["docker", "exec", container, "sh", "-c",
                               "mkdir -p '%s'" % (remote,)])
        for filename in ("code.py", "resource.json"):
            local = os.path.join(dirpath, filename)
            with open(local, "rb") as handle:
                # Streamed rather than `docker cp`: Docker Desktop for Windows
                # rewrites the destination path and the copy fails.
                subprocess.check_call(
                    ["docker", "exec", "-i", container, "sh", "-c",
                     "cat > '%s/%s'" % (remote, filename)],
                    stdin=handle)
        pushed += 1
    print("Pushed %d script resources to %s:%s"
          % (pushed, container, project))
    return before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true",
                        help="also push the result into the gateway container")
    parser.add_argument("--container", default="81-GW1-1",
                        help="gateway container name (default 81-GW1-1)")
    parser.add_argument("--project",
                        default="Gateway_Thread_Pool_Analyzer_and_Historizer",
                        help="Ignition project to push the library into")
    parser.add_argument("--restart", action="store_true",
                        help="REQUIRED ON 8.3. Restart the gateway after "
                             "pushing instead of waiting for a rescan. 8.1 "
                             "watches its project directory and reloads "
                             "within seconds; 8.3 does not watch it at all, "
                             "so files sit on disk being ignored until the "
                             "gateway reloads them at boot. Verified: an "
                             "external edit on 8.3 went unnoticed for 5 "
                             "minutes and loaded immediately on restart.")
    parser.add_argument("--with-timer", action="store_true",
                        help="also create the gateway timer event script. "
                             "8.3 ONLY -- on 8.1 the resource is ingested "
                             "and then never executed, so it looks installed "
                             "and is inert. Create it in the Designer there.")
    parser.add_argument("--delay", type=int, default=10000,
                        help="timer interval in ms (default 10000)")
    parser.add_argument("--history-provider", default="",
                        help="tag history provider name to historize into, "
                             "from the gateway's Config > Tags > History "
                             "page. Injected into the generated config.py; "
                             "src/ is left generic.")
    parser.add_argument("--provision", action="store_true",
                        help="turn on one-shot auto-provisioning, so the "
                             "first timer sample after this deploy creates "
                             "the tag tree. Requires --history-provider.")
    args = parser.parse_args()

    if args.provision and not args.history_provider:
        print("--provision requires --history-provider", file=sys.stderr)
        return 1

    if build() == 0:
        print("nothing built", file=sys.stderr)
        return 1
    if args.history_provider or args.provision:
        apply_config(args.history_provider, args.provision)
    if args.deploy:
        before = deploy(args.container, args.project)
        if args.with_timer:
            push_timer(args.container, args.project, args.delay)
        if args.restart:
            restart_and_wait(args.container)
        else:
            wait_for_ingest(args.container, before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
