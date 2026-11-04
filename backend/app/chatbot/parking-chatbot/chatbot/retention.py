"""Trim finished in-memory records without discarding protected work."""

from .diagnostics import logger, warn_periodically


def prune_finished(records, limit, eligible):
    """Remove oldest eligible items above the limit, preserving relative order."""
    excess = len(records) - limit
    if excess <= 0:
        return 0
    removed = 0
    retained = []
    for record in records:
        if removed < excess and eligible(record):
            removed += 1
        else:
            retained.append(record)
    if removed:
        records[:] = retained
    return removed


def warn_if_protected(kind, count, limit, *, full=False):
    if count > limit or (full and count == limit):
        warn_periodically(logger(__name__), kind, "Protected records retained: %s count=%d limit=%d",
                          kind, count, limit)
