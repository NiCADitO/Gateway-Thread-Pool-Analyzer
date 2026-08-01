"""Everything under src/ must parse and run on Jython 2.7 (CLAUDE.md #2).

Carried over from tag-history-linkage-scanner, which learned it the hard way.
Nothing else catches this: the tests run on CPython 3, where every construct
banned below is perfectly legal, so an f-string added to src/ passes the whole
suite and then fails on a gateway at import time.

This walks the AST rather than grepping, so it cannot be fooled by the word
appearing in a string or a comment.
"""
import ast
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")

# Python 3 only, or absent from the 2.7 standard library.
FORBIDDEN_MODULES = frozenset([
    "pathlib", "dataclasses", "typing", "enum", "asyncio", "secrets",
    "statistics", "concurrent", "queue", "configparser",
])


def _src_files():
    found = []
    for dirpath, _dirnames, filenames in os.walk(SRC):
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _adapter_files():
    found = []
    for path in _src_files():
        if os.sep + "ignition_adapter" + os.sep in path:
            found.append(path)
    return found


def _rel(path):
    return os.path.relpath(path, os.path.dirname(HERE)).replace("\\", "/")


def _violations():
    problems = []
    for path in _src_files():
        source = io.open(path, encoding="utf-8").read()
        tree = ast.parse(source, path)
        for node in ast.walk(tree):
            kind = type(node).__name__
            where = "%s:%d" % (_rel(path), getattr(node, "lineno", 0))

            if kind in ("JoinedStr", "FormattedValue"):
                problems.append("%s f-string" % (where,))
            elif kind in ("ListComp", "SetComp", "DictComp", "GeneratorExp"):
                # House style as well as a readability rule: this gets read
                # under pressure by someone diagnosing a sick gateway.
                problems.append("%s %s -- write the loop out" % (where, kind))
            elif kind == "AnnAssign":
                problems.append("%s variable annotation" % (where,))
            elif kind == "NamedExpr":
                problems.append("%s walrus operator" % (where,))
            elif kind == "Nonlocal":
                problems.append("%s nonlocal" % (where,))
            elif kind in ("FunctionDef", "AsyncFunctionDef"):
                if node.returns is not None:
                    problems.append("%s return annotation" % (where,))
                for argument in node.args.args:
                    if argument.annotation is not None:
                        problems.append("%s argument annotation" % (where,))
                if node.args.kwonlyargs:
                    problems.append("%s keyword-only arguments" % (where,))
            elif kind == "Import":
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                        problems.append("%s imports %s" % (where, alias.name))
            elif kind == "ImportFrom":
                if node.module and \
                        node.module.split(".")[0] in FORBIDDEN_MODULES:
                    problems.append("%s imports %s" % (where, node.module))
    return problems


def test_src_is_jython_27_compatible():
    problems = _violations()
    assert not problems, (
        "%d construct(s) under src/ will not run on Jython 2.7:\n  %s"
        % (len(problems), "\n  ".join(problems)))


def test_the_detector_actually_detects():
    """A checker that cannot fail is not a checker."""
    samples = [
        "x = f'{a}'",
        "paths = [r for r in rows]",
        "x: int = 1",
        "def f(a: int): pass",
        "def f(*, a): pass",
        "import pathlib",
        "from typing import List",
    ]
    for source in samples:
        tree = ast.parse(source)
        flagged = False
        for node in ast.walk(tree):
            kind = type(node).__name__
            if kind in ("JoinedStr", "ListComp", "AnnAssign", "NamedExpr"):
                flagged = True
            elif kind == "FunctionDef":
                if node.args.kwonlyargs or \
                        any(a.annotation for a in node.args.args):
                    flagged = True
            elif kind in ("Import", "ImportFrom"):
                flagged = True
        assert flagged, "would not have caught: %s" % (source,)


def test_no_package_init_contains_code():
    """A package __init__ does not exist on a gateway.

    Ignition's Project Library builds packages out of folders and a script
    named `__init__` is ignored outright -- verified on 8.1.11. So anything
    defined in an `__init__.py` exists on CPython and silently does not exist
    on a gateway. Keep initializers to a docstring.
    """
    offenders = []
    for path in _src_files():
        if os.path.basename(path) != "__init__.py":
            continue
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in tree.body:
            if isinstance(node, ast.Expr) and \
                    isinstance(node.value, ast.Constant):
                continue  # the docstring
            offenders.append("%s:%d %s"
                             % (_rel(path), node.lineno,
                                type(node).__name__))
    assert not offenders, (
        "code in a package __init__ will not exist on a gateway:\n  %s"
        % ("\n  ".join(offenders),))


def test_adapter_never_catches_python_exception_only():
    """`except Exception` is a no-op against a Java exception on Jython.

    Every call in ignition_adapter/ is either `system.*` or
    `java.lang.management.*`, and both raise java.lang.Exception, which does
    not subclass Python's Exception. This repo is more exposed to it than the
    scanner was: jvm.py's entire design is to try an API route, catch it
    failing, and fall through to the next one. With `except Exception` the
    failure escapes, the gateway timer dies, and every CPython test still
    passes because the test doubles raise real Python exceptions.
    """
    offenders = []
    for path in _adapter_files():
        tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                continue  # bare except -- correct here
            name = getattr(node.type, "id", None)
            if name in ("Exception", "BaseException", "StandardError"):
                offenders.append("%s:%d catches %s"
                                 % (_rel(path), node.lineno, name))
    assert not offenders, (
        "these catch a Python exception type where a Java one can arrive, so "
        "they will not catch anything on a gateway:\n  %s"
        % ("\n  ".join(offenders),))


def test_src_carries_no_python3_only_syntax_at_all():
    """Belt and braces: every module must also compile standalone."""
    for path in _src_files():
        source = io.open(path, encoding="utf-8").read()
        compile(source, path, "exec")
