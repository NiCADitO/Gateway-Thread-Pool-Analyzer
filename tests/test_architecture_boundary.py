"""thread_monitor/ must never touch Ignition or Java.

This repo's equivalent of the scanner's read-only guarantee: a structural rule
enforced by a test rather than by discipline.

The whole reason the counting core can be tested against real captured thread
dumps with no gateway running is that it takes plain (name, state) string pairs
and returns plain data. The first `from java.lang import Thread` added to
sampler.py for convenience makes the entire test suite unrunnable on CPython,
and the pressure to add one is real -- comparing against the actual Thread.State
enum looks tidier than comparing strings.

Do not weaken or add exemptions to this test.
"""
import ast
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "src", "thread_monitor")

FORBIDDEN_ROOTS = frozenset(["system", "java", "javax", "com", "org"])


def _core_files():
    found = []
    for dirpath, _dirnames, filenames in os.walk(CORE):
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _rel(path):
    return os.path.relpath(path, os.path.dirname(HERE)).replace("\\", "/")


def test_core_imports_nothing_from_ignition_or_java():
    offenders = []
    for path in _core_files():
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            where = "%s:%d" % (_rel(path), getattr(node, "lineno", 0))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_ROOTS:
                        offenders.append("%s imports %s"
                                         % (where, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and \
                        node.module.split(".")[0] in FORBIDDEN_ROOTS:
                    offenders.append("%s imports from %s"
                                     % (where, node.module))
    assert not offenders, (
        "thread_monitor/ must stay pure -- move this into "
        "ignition_adapter/:\n  %s" % ("\n  ".join(offenders),))


def test_core_never_references_the_system_namespace():
    """Catches `system.tag.writeBlocking(...)` with no import.

    On a gateway `system` is injected into every script's namespace, so a call
    to it needs no import statement and the import test above would miss it
    entirely.
    """
    offenders = []
    for path in _core_files():
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "system":
                offenders.append("%s:%d references `system`"
                                 % (_rel(path), node.lineno))
    assert not offenders, "\n  ".join(offenders)


def test_core_modules_import_cleanly_on_cpython():
    """The point of the boundary, stated as a test.

    If this fails, the fixture-driven test suite cannot run at all.
    """
    from thread_monitor import matchers, sampler, snapshot, tagpaths, taxonomy
    assert taxonomy.POOL_SPECS
    assert sampler.count([]) is not None
    assert tagpaths.all_paths()
    assert snapshot.ALL_STATES
    assert matchers.prefix("x").matches("xy")
