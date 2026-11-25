"""Граничные случаи нарезки через API: короткое видео, тишина, отмена, ретрай.

Запуск: .venv312/bin/python tests/test_edge_cases.py http://127.0.0.1:8863
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

FF = Path("viralcut-studio/dist/ViralCutStudio/_internal/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0")
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8863"


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def upload(path: Path) -> str:
    boundary = "----vcboundary"
    data = path.read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode() + data + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{BASE}/api/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())["id"]


def wait_review(pid: str, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1)
        project = api(f"/api/projects/{pid}")["project"]
        if project["status"] in ("review", "error", "cancelled"):
            return project
    return api(f"/api/projects/{pid}")["project"]


def wait_done(pid: str, timeout: float = 600) -> tuple[dict, float]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        detail = api(f"/api/projects/{pid}")
        if detail["project"]["status"] in ("done", "error", "cancelled"):
            return detail["project"], time.time() - t0
    return api(f"/api/projects/{pid}")["project"], time.time() - t0


def make(path: Path, args: list[str]) -> Path:
    subprocess.run([str(FF), "-y", "-loglevel", "error", *args, str(path)], check=True)
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vc-edge-"))
    results = []

    # 1. короткое видео (8 с) — не должно падать
    short = make(tmp / "short.mp4", ["-f", "lavfi", "-i", "testsrc2=size=854x480:rate=30:duration=8",
                                     "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
                                     "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"])
    pid = upload(short)
    project = wait_review(pid)
    if project["status"] == "review":
        api(f"/api/projects/{pid}/start", "POST", {"options": {"count": 3}})
        project, elapsed = wait_done(pid)
        clips = api(f"/api/projects/{pid}").get("clips") or []
        results.append(("короткое видео 8 с", project["status"], f"{len(clips)} клипов за {elapsed:.0f} с"))
    else:
        results.append(("короткое видео 8 с", project["status"], (project.get("error") or "")[:80]))

    # 2. видео без речи — понятная ошибка
    silent = make(tmp / "silent.mp4", ["-f", "lavfi", "-i", "testsrc2=size=854x480:rate=30:duration=20",
                                       "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                                       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"])
    pid = upload(silent)
    project = wait_review(pid)
    if project["status"] == "review":
        api(f"/api/projects/{pid}/start", "POST", {"options": {"count": 2}})
        project, elapsed = wait_done(pid, timeout=420)
    results.append(("видео без речи", project["status"], (project.get("error") or "")[:110]))

    # 3. отмена на середине
    pid = upload(Path("docs/promo/live-action/tests/long.mp4"))
    project = wait_review(pid)
    if project["status"] == "review":
        api(f"/api/projects/{pid}/start", "POST", {"options": {"count": 6}})
        time.sleep(20)
        api(f"/api/projects/{pid}/cancel", "POST")
        time.sleep(5)
        detail = api(f"/api/projects/{pid}")
        project = detail["project"]
        running = [c for c in detail.get("clips") or [] if c["status"] == "rendering"]
        results.append(("отмена на середине", project["status"],
                        f"осталось 'rendering' клипов: {len(running)}"))
        # ретрай после отмены
        api(f"/api/projects/{pid}/retry", "POST", {})
        project, elapsed = wait_done(pid, timeout=900)
        clips = api(f"/api/projects/{pid}").get("clips") or []
        ok = sum(1 for c in clips if c["status"] == "ready")
        results.append(("ретрай после отмены", project["status"], f"{ok} готовых клипов за {elapsed:.0f} с"))

    print()
    for name, status, detail in results:
        print(f"  {name:24s} → {status:10s} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
