"""Bound repeated transport failures independently for each backend group."""
import threading
import time

from . import config


class CircuitBreaker:
    def __init__(self):
        self.lock = threading.Lock()
        self.generation = 0
        self.failures = 0
        self.until = None
        self.probing = False
        self.opened = 0

    def admit(self, *, may_probe):
        with self.lock:
            if self.until is None:
                return self.generation
            if time.monotonic() < self.until or self.probing or not may_probe:
                return None
            self.probing = True
            return self.generation

    def complete(self, ticket, failed):
        with self.lock:
            if ticket != self.generation:
                return
            if not failed:
                self.failures = 0
                self.until = None
                self.probing = False
                return
            self.failures += 1
            if self.probing or self.failures >= config.BACKEND_BREAKER_FAILS:
                self.until = time.monotonic() + config.BACKEND_BREAKER_OPEN_SEC
                self.generation += 1
                self.opened += 1
                self.probing = False

    def stats(self):
        with self.lock:
            state = ("closed" if self.until is None else
                     "half_open" if self.probing or time.monotonic() >= self.until else "open")
            return {"state": state, "opened": self.opened}
