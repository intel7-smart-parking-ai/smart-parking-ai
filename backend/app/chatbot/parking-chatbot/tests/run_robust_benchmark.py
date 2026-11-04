"""Repeatable local mock engine benchmark, profile, memory and process startup probes."""
import argparse
import cProfile
import csv
from datetime import datetime
import gc
import io
import json
import os
from pathlib import Path
import pstats
import socket
import statistics
import subprocess
import sys
import time
import tracemalloc
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from chatbot import engine as e
from chatbot.adapters import CachedAdapter, MockAdapter, KST
from safety_check import assert_safe


def quantiles(values):
    ordered = sorted(values)
    return {"p50_ms": statistics.median(ordered), "p95_ms": ordered[int((len(ordered)-1)*.95)], "max_ms": max(ordered)}


def canonical(value):
    ignored = {"updated_at", "runtime_id", "state_version", "confirmation_token", "token", "latency_ms", "expires_at", "set_at"}
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if k not in ignored}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def startup(kind):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [sys.executable, "server_dev.py", str(port)] if kind == "dev" else [
        sys.executable, "-m", "uvicorn", "server_fastapi:app", "--host", "127.0.0.1", "--port", str(port)]
    env = {**os.environ, "DATA_SOURCE": "mock", "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"}
    began = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while time.perf_counter() - began < 10:
            if process.poll() is not None:
                return {"unavailable": True, "exit": process.returncode}
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/chat/config", timeout=.2) as response:
                    if response.status == 200:
                        return {"first_response_ms": (time.perf_counter()-began)*1000}
            except OSError:
                time.sleep(.02)
        return {"unavailable": True, "timeout": True}
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()
    rows = []
    for file in sorted((ROOT/"tests").glob("questions*.csv")):
        with file.open(encoding="utf-8") as stream:
            rows.extend(csv.DictReader(stream))
    fixed = datetime(2026, 11, 3, 12, 0, tzinfo=KST)
    runs, snapshots = [], []
    with patch("chatbot.adapters.now", return_value=fixed), patch.object(e, "now", return_value=fixed):
        e.data = CachedAdapter(MockAdapter())
        for _ in range(3):
            times = []
            e.reset_all()
            for index, row in enumerate(rows):
                before = time.perf_counter()
                answer = e.handle("bench"+str(index % 20), text=row["question"])
                times.append((time.perf_counter()-before)*1000)
                assert_safe(answer)
            runs.append(quantiles(times))
        for row in rows:
            e.reset_all()
            answer = e.handle("snapshot", text=row["question"])
            snapshots.append(canonical(answer))
        e.reset_all()
        profile = cProfile.Profile()
        profile.enable()
        for index in range(1000):
            e.handle("profile", text=rows[index % len(rows)]["question"])
        profile.disable()
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(20)
        gc.collect()
        e.reset_all()
        tracemalloc.start()
        gc.collect()
        before_memory = tracemalloc.get_traced_memory()[0]
        for index in range(1000):
            assert_safe(e.handle("memory", text=rows[index % len(rows)]["question"]))
        gc.collect()
        after_memory, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    output = {"runs": runs, "questions": len(rows), "memory_1000": {
        "before": before_memory, "after": after_memory, "peak": peak},
        "startup": {kind: startup(kind) for kind in ("dev", "fastapi")}, "profile": stream.getvalue()}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    path.with_suffix(".answers.json").write_text(json.dumps(snapshots, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k != "profile"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
