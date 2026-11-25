"""Локальный тест анализа и рендера без сервера (для разработки)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from viralcut import analyze, render, subtitles, utils  # noqa: E402

root = Path(__file__).resolve().parent.parent
source = root / "data/test/source_long.mp4"
transcript = json.load(open(root / "data/test/transcript_long.json"))
out = root / "data/test/out"
out.mkdir(parents=True, exist_ok=True)

t = time.time()
clips = analyze.find_segments(transcript["segments"], source, transcript["duration"],
                              count=8, min_dur=15, max_dur=60, target_dur=32)
print(f"analysis {time.time()-t:.1f}s, found {len(clips)}")
for c in clips:
    print(f"  {c['start']:7.2f}-{c['end']:7.2f} ({c['duration']:5.1f}s) score={c['score']:.2f} "
          f"{c['parts']} :: {c['title'][:70]}")

for i, c in enumerate(clips[:2]):
    ass = subtitles.build_ass(c["words"], c["start"], c["end"], hook=c["title"])
    ass_path = out / f"test_{i}.ass"
    ass_path.write_text(ass, encoding="utf-8")
    t = time.time()
    res = render.render_clip(source, c["start"], c["end"], out / f"test_{i}.mp4",
                             ass_path=ass_path, aspect="9:16", mode="blur")
    print(f"rendered {res['path'].name} in {time.time()-t:.1f}s")
    render.make_thumb(res["path"], out / f"test_{i}.jpg", at=1.0)
print("done")
