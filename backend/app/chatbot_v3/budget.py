"""Per-turn wall-clock budget propagated into the worker thread."""
from contextvars import ContextVar
from contextlib import contextmanager
import time

class BudgetExpired(TimeoutError):
    pass

_deadline = ContextVar('chatbot_v3_deadline', default=None)

def remaining_timeout(default):
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BudgetExpired('turn deadline reached')
    return min(default, remaining)

@contextmanager
def turn_budget(deadline):
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)
