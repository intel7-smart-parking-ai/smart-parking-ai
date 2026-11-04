"""unittest discovery with a per-test traceback watchdog; no expected values changed."""
import faulthandler
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_original = unittest.TestCase.run


def guarded(self, result=None):
    # Long explicit soak tests receive their own 180-second outer budget.
    limit = 180 if os.getenv("RUN_SLOW") == "1" and "slow" in self.id() else 20
    faulthandler.dump_traceback_later(limit, exit=True)
    try:
        return _original(self, result)
    finally:
        faulthandler.cancel_dump_traceback_later()


unittest.TestCase.run = guarded
if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern=sys.argv[1] if len(sys.argv) > 1 else "test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
