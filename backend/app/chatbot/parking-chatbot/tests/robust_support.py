"""Small bounded HTTP fault fixture shared by robustness tests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from chatbot.adapters import iso, now


class FaultServer:
    def __init__(self):
        self.workers = set()
        self.stop = threading.Event()
        self.reset()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.do_GET()

            def do_GET(self):
                owner.workers.add(threading.current_thread())
                key = owner.key(self.path)
                owner.requests.append((self.command, self.path))
                value = owner.normal(key, self.path)
                status = 201 if key == "payment" else 200
                targeted = key == owner.target
                if targeted:
                    if owner.mode == "headers":
                        owner.stop.wait(30)
                    if owner.mode == "body":
                        self.send_response(200)
                        self.send_header("Content-Length", "1000000")
                        self.end_headers()
                        try:
                            while not owner.stop.wait(.02):
                                self.wfile.write(b" ")
                                self.wfile.flush()
                        except OSError:
                            pass
                        return
                    value, status = owner.value, owner.status
                data = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header(owner.header, "mock")
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="robust-http-fixture")
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def reset(self):
        self.target, self.value, self.status, self.mode = None, {}, 200, None
        self.requests = []
        self.header = "X-ParkFlow-Data-Source"
        self.at = iso(now())

    @staticmethod
    def key(path):
        if path.endswith("/spaces"): return "spaces"
        if path.endswith("/location"): return "location"
        if path.endswith("/fee"): return "fee"
        if path.endswith("/mock-payments"): return "payment"
        if path.endswith("/alerts"): return "alerts"
        return "lots"

    def normal(self, key, path=""):
        if key == "lots":
            return {"items": [{"lot_id": "A", "total_spaces": 4, "empty_spaces": 1,
                              "uncertain_spaces": 1, "unavailable_spaces": 1}], "as_of": self.at}
        if key == "spaces":
            return {"items": [{"space_id": "A-"+str(i).zfill(2), "car_id": "11" if i == 1 else None, "status": state}
                    for i, state in enumerate(("occupied", "empty", "uncertain", "unavailable"), 1)], "as_of": self.at}
        if key == "location":
            return {"car_id": "11", "lot_id": "A", "space_id": "A-01", "session_id": "visit-01",
                    "status": "occupied", "confidence": .9, "observed_at": self.at}
        if key == "fee":
            return {"amount_krw": 2500, "duration_minutes": 70, "entry_at": self.at,
                    "payment_status": "unpaid", "is_daily_capped": False, "as_of": self.at}
        if key == "payment":
            return {"amount_krw": 2500, "payment_id": "payment-01", "paid_at": self.at, "idempotent_replayed": False}
        return {"items": [], "as_of": self.at}

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()
        for worker in self.workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
