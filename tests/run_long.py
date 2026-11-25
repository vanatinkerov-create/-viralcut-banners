"""Запуск обработки проекта и замер времени по этапам (для длинных тестов)."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8861"
PID = sys.argv[2] if len(sys.argv) > 2 else Path("/tmp/longpid").read_text().strip()
BODY = sys.argv[3] if len(sys.argv) > 3 else '{"options":{"count":12,"min_dur":18,"max_dur":55,"target_dur":34}}'


def call(path: str, method: str = "GET", body: str | None = None) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 data=body.encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    started = time.time()
    call(f"/api/projects/{PID}/start", "POST", BODY)
    last_stage = ""
    while True:
        time.sleep(5)
        detail = call(f"/api/projects/{PID}")
        project = detail["project"]
        stage = project.get("stage") or ""
        if stage != last_stage:
            print(f"[{time.time() - started:6.1f}s] {project['status']:9s} {project.get('progress', 0):5.1f}%  {stage[:70]}",
                  flush=True)
            last_stage = stage
        if project["status"] in ("done", "error", "cancelled"):
            print(f"ИТОГО {time.time() - started:.1f}s, статус {project['status']}", flush=True)
            clips = detail.get("clips") or []
            print(f"клипов: {len(clips)}")
            for c in clips:
                print(f"  #{c['idx'] + 1:02d} {c['start']:7.1f}–{c['end']:7.1f} ({c['end'] - c['start']:5.1f}s) "
                      f"score {c.get('score', 0):.3f} | {str(c.get('title'))[:60]}")
            return 0
        if time.time() - started > 3600:
            print("таймаут 60 минут", flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
