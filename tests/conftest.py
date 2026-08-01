"""Shared fixtures. CPython 3 -- modern syntax is fine in tests/."""
import os

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures")


def load_dump(name):
    """Read a captured thread dump into (name, state) pairs.

    Fixture format is one tab-separated pair per line, '#' comments ignored.
    See docs/development.md for how a fixture is captured off a live gateway.
    """
    path = os.path.join(FIXTURE_DIR, name)
    pairs = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            thread_name, _, state = line.partition("\t")
            pairs.append((thread_name, state))
    return pairs


def available_dumps():
    """Every captured dump in fixtures/, so a new capture is tested for free."""
    return sorted(f for f in os.listdir(FIXTURE_DIR) if f.endswith(".tsv"))


@pytest.fixture
def dump_81_11():
    """Ignition 8.1.11 on Java 11. Has datasources and history configured."""
    return load_dump("threads_81_11.tsv")


@pytest.fixture
def dump_81_48():
    """Ignition 8.1.48 on Java 17. No datasources configured."""
    return load_dump("threads_81_48.tsv")


@pytest.fixture
def all_dumps():
    """Every fixture, as {filename: pairs}.

    Used by checks that only make sense across the whole captured corpus --
    "no PoolSpec is dead" is true of the union, not of any single gateway,
    because a gateway with no datasources legitimately has no history threads.
    """
    return {name: load_dump(name) for name in available_dumps()}


def pytest_generate_tests(metafunc):
    """Parametrize any test asking for `dump_pairs` over every fixture file."""
    if "dump_pairs" in metafunc.fixturenames:
        names = available_dumps()
        metafunc.parametrize(
            "dump_pairs",
            [load_dump(n) for n in names],
            ids=names,
        )
