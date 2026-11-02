"""수신 커서 기반 재전송 큐. 프로세스 내 at-least-once, 클라이언트 ID 중복 제거."""
from collections import deque
from copy import deepcopy
import threading
import time


class Notifications:
    def __init__(self, max_items=100, ttl=1800):
        self.lock = threading.RLock()
        self.streams = {}
        self.max_items, self.ttl = max_items, ttl

    def _stream(self, sid):
        return self.streams.setdefault(sid, {"seq": 0, "ack": 0, "dropped": 0, "items": deque()})

    def enqueue(self, sid, payload, key=None, expires_at=None):
        with self.lock:
            s = self._stream(sid)
            if key:
                existing = next((x for x in s["items"] if x["key"] == key), None)
                if existing:
                    return existing["id"]
            s["seq"] += 1
            item = {"id": s["seq"], "payload": deepcopy(payload), "key": key,
                    "expires": expires_at or time.monotonic() + self.ttl}
            s["items"].append(item)
            while len(s["items"]) > self.max_items:
                s["dropped"] = s["items"].popleft()["id"]
            return item["id"]

    def read(self, sid, cursor=0):
        with self.lock:
            s = self._stream(sid)
            if type(cursor) is not int or cursor < 0 or cursor > s["seq"]:
                raise ValueError("유효하지 않은 알림 커서입니다.")
            s["ack"] = max(s["ack"], cursor)
            acknowledged = []
            while s["items"] and (s["items"][0]["id"] <= s["ack"] or s["items"][0]["expires"] <= time.monotonic()):
                removed = s["items"].popleft()
                if removed["id"] <= cursor:
                    acknowledged.append(deepcopy(removed["payload"]))
                if removed["id"] > s["ack"]:
                    s["dropped"] = max(s["dropped"], removed["id"])
            items = [{**deepcopy(x["payload"]), "event_id": x["id"]} for x in s["items"]
                     if x["id"] > cursor and x["expires"] > time.monotonic()]
            return {"messages": items, "cursor": s["seq"], "resync_required": cursor < s["dropped"],
                    "acknowledged": acknowledged}

    def discard(self, sid, key):
        with self.lock:
            s = self._stream(sid)
            s["items"] = deque(x for x in s["items"] if x["key"] != key)

    def discard_prefix(self, sid, prefix):
        with self.lock:
            s=self._stream(sid)
            s["items"]=deque(x for x in s["items"] if not (x.get("key") or "").startswith(prefix))

    def forget(self, sid):
        with self.lock:
            self.streams.pop(sid, None)

    def reset(self):
        with self.lock:
            self.streams.clear()
