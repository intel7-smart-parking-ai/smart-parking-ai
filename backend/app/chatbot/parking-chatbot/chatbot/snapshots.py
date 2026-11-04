"""Read-only JSON-shaped values that serialize through both HTTP servers."""
from copy import deepcopy


def _readonly(*args, **kwargs):
    raise TypeError("Snapshot is read-only")


class FrozenDict(dict):
    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _readonly

    def __deepcopy__(self, memo):
        # Existing callers that explicitly request a detached copy may edit it.
        return deepcopy(dict(self), memo)


class FrozenList(list):
    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = clear = extend = _readonly
    insert = pop = remove = reverse = sort = _readonly

    def __deepcopy__(self, memo):
        return deepcopy(list(self), memo)


def freeze(value):
    """Freeze owned JSON data recursively, sharing any already frozen subtree."""
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict((key, freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    return value
