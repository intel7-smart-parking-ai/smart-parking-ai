"""Per-turn wall-clock budget propagated into the worker thread."""
from contextvars import ContextVar
from contextlib import contextmanager
import time

class BudgetExpired(TimeoutError):
    pass

_deadline = ContextVar('chatbot_v3_deadline', default=None)
_cancelled = ContextVar('chatbot_v3_cancelled', default=None)

def remaining_timeout(default):
    cancelled = _cancelled.get()
    if cancelled is not None and cancelled.is_set():
        raise BudgetExpired('response wait expired')
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BudgetExpired('turn deadline reached')
    return min(default, remaining)

@contextmanager
def turn_budget(deadline, cancelled=None):
    token = _deadline.set(deadline)
    cancel_token = _cancelled.set(cancelled)
    try:
        yield
    finally:
        _cancelled.reset(cancel_token)
        _deadline.reset(token)
