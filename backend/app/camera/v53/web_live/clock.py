"""Server-aligned UTC clock for observation and dispatch timestamps.

11/11 demo site: the camera PC ran 0.51 s ahead of the demo server, whose receiver
rejects a dispatch more than 0.5 s in the future (422). Windows time sync is still
the real fix; this keeps the camera usable when the PCs drift apart by a little.
Only offsets within MAX_OFFSET seconds are applied; anything larger is a setup error.
"""
import json
import logging
import math
import os
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

MAX_OFFSET = 5.0
RESYNC_SECONDS = 120
LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_offset = 0.0


def set_offset(seconds):
    """Apply server-minus-PC seconds. Returns the applied value (0 when rejected)."""
    global _offset
    try:value=float(seconds)
    except (TypeError, ValueError):value=float('nan')
    if not math.isfinite(value) or abs(value) > MAX_OFFSET:
        LOG.warning('clock offset %r ignored (must be within ±%.0fs); PC clock used as is', seconds, MAX_OFFSET)
        value = 0.0
    with _lock:
        _offset = value
    return value


def offset():
    with _lock:
        return _offset


def utcnow():
    return datetime.now(timezone.utc) + timedelta(seconds=offset())


def from_env(env=None):
    raw = (os.environ if env is None else env).get('V53_CLOCK_OFFSET_SECONDS', '').strip()
    return set_offset(raw) if raw else None


def measure(base, samples=5, timeout=3, opener=None):
    """Median of server-minus-PC offsets from /api/v1/health 'timestamp'. None if unavailable."""
    opener = opener or urllib.request.urlopen
    values = []
    for _ in range(samples):
        try:
            t0 = time.time()
            with opener(base.rstrip('/') + '/api/v1/health', timeout=timeout) as response:
                body = json.loads(response.read(4096).decode('utf-8'))
            t1 = time.time()
            stamp = datetime.fromisoformat(str(body['timestamp']).replace('Z', '+00:00'))
            if stamp.tzinfo is None:continue
            values.append(stamp.timestamp() - (t0 + t1) / 2)
        except Exception:
            continue
    return statistics.median(values) if len(values) >= 3 else None


def start_sync(base, interval=RESYNC_SECONDS, stop_event=None):
    """Re-measure in the background so PC drift during a long demo stays corrected."""
    stop_event = stop_event or threading.Event()

    def run():
        last = None
        while not stop_event.is_set():
            value = measure(base)
            if value is not None:
                applied = set_offset(value)
                if last is None or abs(applied - last) >= .1:
                    LOG.info('clock offset to server %.2fs applied to camera timestamps', applied)
                last = applied
            stop_event.wait(interval)

    thread = threading.Thread(target=run, daemon=True, name='v53-clock')
    thread.start()
    return thread


def delta():
    """The correction as a timedelta, for callers that read their own datetime.now()."""
    return timedelta(seconds=offset())
