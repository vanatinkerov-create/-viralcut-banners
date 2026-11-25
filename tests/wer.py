"""WER и разбор ошибок распознавания: сравнение транскрипта с эталонным текстом.

Запуск: .venv312/bin/python tests/wer.py data/asr_test/transcript.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.long_script import PARAGRAPHS  # noqa: E402


def normalize(text: str) -> list[str]:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]|_", " ", text)
    return [w for w in text.split() if w]


def edit_distance(ref: list[str], hyp: list[str]) -> tuple[int, int, int, int]:
    """Расстояние Левенштейна + число замен/вставок/удалений."""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m], 0, 0, 0


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    hyp = [w["word"] for seg in data["segments"] for w in (seg.get("words") or [])]
    ref = [w for p in PARAGRAPHS for w in normalize(p)]
    hyp_n = normalize(" ".join(hyp))
    dist, *_ = edit_distance(ref, hyp_n)
    wer = dist / max(1, len(ref))
    print(f"слов в эталоне: {len(ref)}")
    print(f"слов в транскрипте: {len(hyp_n)}")
    print(f"ошибок (Левенштейн): {dist}")
    print(f"WER: {wer * 100:.1f}%")

    # где именно ошиблись: скользящее окно 12 слов
    worst = []
    for i in range(0, max(1, len(ref) - 11)):
        seg_ref = ref[i:i + 12]
        best = None
        for j in range(max(0, i - 4), min(len(hyp_n) - 11, i + 5) + 1):
            if j < 0 or j + 12 > len(hyp_n) + 12:
                continue
            d, *_ = edit_distance(seg_ref, hyp_n[j:j + 12])
            if best is None or d < best[0]:
                best = (d, j)
        if best and best[0] >= 3:
            worst.append((best[0], " ".join(seg_ref)))
    worst.sort(reverse=True)
    print("\nхудшие участки:")
    for score, text in worst[:8]:
        print(f"  {score:2d} ошибок: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "data/asr_test/transcript.json"))
