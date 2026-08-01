"""Name matchers used to assign a thread to a pool bucket.

Deliberately not regular expressions. This runs against every live thread on
every sample, and `startswith` is both faster and far easier to read at 2am
than a pattern. A matcher that needs a regex is a sign the thread name is
carrying structure that should be parsed, not matched.

Every matcher exposes:

    matches(name)   -> True/False
    describe()      -> human string, printed in the pool catalog and in
                       `python scripts/explain_taxonomy.py`

`describe()` exists so the README's pool catalog can be generated from the
taxonomy rather than maintained alongside it and drifting.

Jython 2.7: no f-strings, no comprehensions, old-style classes.
"""


class Prefix(object):
    """Matches when the thread name starts with `value`."""

    def __init__(self, value):
        self.value = value

    def matches(self, name):
        return name.startswith(self.value)

    def describe(self):
        return "starts with '%s'" % (self.value,)


class Exact(object):
    """Matches one exact thread name.

    For the singleton threads -- 'VM Thread', 'Finalizer' -- where a prefix
    would be needlessly loose.
    """

    def __init__(self, value):
        self.value = value

    def matches(self, name):
        return name == self.value

    def describe(self):
        return "is exactly '%s'" % (self.value,)


class Contains(object):
    """Matches when `value` appears anywhere in the thread name.

    Used sparingly. Ignition's store-and-forward threads embed the datasource
    name in the middle -- 'gateway-storeforward-pipeline[MyDB]-engine[...]' --
    so the discriminating token is not at either end.
    """

    def __init__(self, value):
        self.value = value

    def matches(self, name):
        return self.value in name

    def describe(self):
        return "contains '%s'" % (self.value,)


class AnyOf(object):
    """Matches when any child matcher matches. Short-circuits."""

    def __init__(self, children):
        self.children = children

    def matches(self, name):
        for child in self.children:
            if child.matches(name):
                return True
        return False

    def describe(self):
        parts = []
        for child in self.children:
            parts.append(child.describe())
        return " or ".join(parts)


def prefix(value):
    return Prefix(value)


def exact(value):
    return Exact(value)


def contains(value):
    return Contains(value)


def any_of(*children):
    """any_of(prefix('a'), exact('b')) -- variadic for readable taxonomy entries."""
    return AnyOf(list(children))
