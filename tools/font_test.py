import subprocess, sys
from pathlib import Path
sys.path.insert(0,'.')
from viralcut import subtitles, render
from viralcut.config import FONTS_DIR
from viralcut.utils import probe

src = Path("data/test/face_src.mp4")
out = Path("data/test/fonts"); out.mkdir(parents=True, exist_ok=True)
words = []
t = 0.0
for w in "ПРИВЕТ ЭТО ТЕСТ КРУПНЫХ СУБТИТРОВ НА ВИДЕО".split():
    words.append({"word": w, "start": t, "end": t+0.35}); t += 0.4

ff = render.ffmpeg()
for key in subtitles.FONTS:
    ass = subtitles.build_ass(words, 0, 6, hook="Проверка шрифта", font=key,
                              sub_size=118, position="bottom", offset=0)
    p = out / f"{key}.ass"; p.write_text(ass, encoding="utf-8")
    img = out / f"{key}.png"
    cmd = [ff, "-y", "-v", "error", "-ss", "1", "-i", str(src), "-frames:v", "1",
           "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass=filename='{p}':fontsdir='{FONTS_DIR}'",
           str(img)]
    subprocess.run(cmd, check=True)
    print("ok", key)
