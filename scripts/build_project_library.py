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
    wait_for_ingest(container, before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true",
                        help="also push the result into the gateway container")
    parser.add_argument("--container", default="81-GW1-1",
                        help="gateway container name (default 81-GW1-1)")
    parser.add_argument("--project",
                        default="Gateway_Thread_Pool_Analyzer_and_Historizer",
                        help="Ignition project to push the library into")
    args = parser.parse_args()

    if build() == 0:
        print("nothing built", file=sys.stderr)
        return 1
    if args.deploy:
        deploy(args.container, args.project)
    return 0


if __name__ == "__main__":
    sys.exit(main())
