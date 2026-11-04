"""Namespaced logging and bounded suppression for recurring backend warnings."""
import logging
from threading import Lock
from time import monotonic

_lock = Lock()
_warnings = {}
_INTERVAL = 60


def logger(module):
    return logging.getLogger(f"parking_chatbot.{module.rsplit('.', 1)[-1]}")


def warn_periodically(log, key, message, *args):
    current = monotonic()
    with _lock:
        last, suppressed = _warnings.get(key, (float("-inf"), 0))
        if current - last < _INTERVAL:
            _warnings[key] = last, suppressed + 1
            return
        if key not in _warnings and len(_warnings) >= 64:
            _warnings.pop(next(iter(_warnings)))
        _warnings[key] = current, 0
    log.warning(message + " (suppressed=%d)", *args, suppressed)
