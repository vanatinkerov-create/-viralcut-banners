"""Генерация ASS-субтитров: шрифт, размер и положение настраиваются."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .utils import ass_time

PLAY_W, PLAY_H = 1080, 1920

ACCENT_PALETTE = {
    "yellow": ("#FFE24A", "&H4AE2FF&"),
    "gold": ("#FFC53D", "&H3DC5FF&"),
    "orange": ("#FF8A3D", "&H3D8AFF&"),
    "coral": ("#FF6B6B", "&H6B6BFF&"),
    "red": ("#FF3D57", "&H573DFF&"),
    "rose": ("#FF5C8A", "&H8A5CFF&"),
    "pink": ("#FF6BD6", "&HD66BFF&"),
    "magenta": ("#E84BFF", "&HFF4BE8&"),
    "violet": ("#B36BFF", "&HFF6BB3&"),
    "purple": ("#8B5CF6", "&HF65C8B&"),
    "indigo": ("#6B7CFF", "&HFF7C6B&"),
    "blue": ("#3D8BFF", "&HFF8B3D&"),
    "sky": ("#3DC8FF", "&HFFC83D&"),
    "cyan": ("#6BE8FF", "&HFFE86B&"),
    "teal": ("#2DE0C8", "&HC8E02D&"),
    "mint": ("#6BFFB0", "&HB0FF6B&"),
    "green": ("#6BFF8C", "&H8CFF6B&"),
    "lime": ("#C6FF4A", "&H4AFFC6&"),
    "peach": ("#FFB38A", "&H8AB3FF&"),
    "lavender": ("#C9B6FF", "&HFFB6C9&"),
    "white": ("#FFFFFF", "&HFFFFFF&"),
}
ACCENTS = {key: value[1] for key, value in ACCENT_PALETTE.items()}

# key -> family в TTF, файл, метрики для одинаковой визуальной высоты и переносов
FONTS: Dict[str, Dict[str, Any]] = {
    "montserrat": {
        "label": "Montserrat", "family": "Montserrat ExtraBold",
        "file": "Montserrat-ExtraBold.ttf", "cap": 0.700, "avg": 0.828,
    },
    "russo": {
        "label": "Russo One", "family": "Russo One",
        "file": "RussoOne-Regular.ttf", "cap": 0.700, "avg": 0.759,
    },
    "ptsans": {
        "label": "PT Sans Narrow", "family": "PT Sans Narrow",
        "file": "PT_Sans-Narrow-Web-Bold.ttf", "cap": 0.700, "avg": 0.546,
    },
    "lato": {
        "label": "Lato Black", "family": "Lato Black",
        "file": "Lato-Black.ttf", "cap": 0.726, "avg": 0.766,
    },
    "play": {
        "label": "Play", "family": "Play",
        "file": "Play-Bold.ttf", "cap": 0.649, "avg": 0.727,
    },
    "philosopher": {
        "label": "Philosopher", "family": "Philosopher",
        "file": "Philosopher-Bold.ttf", "cap": 0.660, "avg": 0.715,
    },
    "oswald": {
        "label": "Oswald", "family": "Oswald Bold",
        "file": "Oswald-Bold.ttf", "cap": 0.810, "avg": 0.884,
    },
    "robotocond": {
        "label": "Roboto Condensed", "family": "Roboto Condensed Bold",
        "file": "RobotoCondensed-Bold.ttf", "cap": 0.711, "avg": 0.879,
    },
    "inter": {
        "label": "Inter Black", "family": "Inter Black",
        "file": "Inter-Black.ttf", "cap": 0.728, "avg": 1.080,
    },
    "rubik": {
        "label": "Rubik ExtraBold", "family": "Rubik ExtraBold",
        "file": "Rubik-ExtraBold.ttf", "cap": 0.700, "avg": 1.113,
    },
    "manrope": {
        "label": "Manrope ExtraBold", "family": "Manrope ExtraBold",
        "file": "Manrope-ExtraBold.ttf", "cap": 0.720, "avg": 1.062,
    },
    "unbounded": {
        "label": "Unbounded", "family": "Unbounded Bold",
        "file": "Unbounded-Bold.ttf", "cap": 0.750, "avg": 1.397,
    },
    "caveat": {
        "label": "Caveat", "family": "Caveat Bold",
        "file": "Caveat-Bold.ttf", "cap": 0.610, "avg": 0.764,
    },
    "yeseva": {
        "label": "Yeseva One", "family": "Yeseva One",
        "file": "YesevaOne-Regular.ttf", "cap": 0.700, "avg": 1.090,
    },
    "exo2": {
        "label": "Exo 2", "family": "Exo 2 Bold",
        "file": "Exo2-Bold.ttf", "cap": 0.690, "avg": 1.033,
    },
    "comfortaa": {
        "label": "Comfortaa", "family": "Comfortaa Bold",
        "file": "Comfortaa-Bold.ttf", "cap": 0.781, "avg": 1.166,
    },
    "golos": {
        "label": "Golos Text", "family": "Golos Text Bold",
        "file": "GolosText-Bold.ttf", "cap": 0.700, "avg": 1.048,
    },
    "onest": {
        "label": "Onest", "family": "Onest ExtraBold",
        "file": "Onest-ExtraBold.ttf", "cap": 0.708, "avg": 1.042,
    },
    "marmelad": {
        "label": "Marmelad", "family": "Marmelad",
        "file": "Marmelad-Regular.ttf", "cap": 0.709, "avg": 0.964,
    },
    "badscript": {
        "label": "Bad Script", "family": "Bad Script",
        "file": "BadScript-Regular.ttf", "cap": 0.960, "avg": 0.755,
    },
    "marckscript": {
        "label": "Marck Script", "family": "Marck Script",
        "file": "MarckScript-Regular.ttf", "cap": 0.638, "avg": 0.868,
    },
    "ruslan": {
        "label": "Ruslan Display", "family": "Ruslan Display",
        "file": "RuslanDisplay-Regular.ttf", "cap": 0.500, "avg": 1.268,
    },
    "stalinist": {
        "label": "Stalinist One", "family": "Stalinist One",
        "file": "StalinistOne-Regular.ttf", "cap": 0.700, "avg": 1.926,
    },
    "underdog": {
        "label": "Underdog", "family": "Underdog",
        "file": "Underdog-Regular.ttf", "cap": 0.710, "avg": 0.943,
    },
}

POSITIONS = ("bottom", "middle", "top")

# Темп субтитров: сколько слов в реплике и сколько она висит после речи.
# «Спокойно» — короткие реплики и пауза на чтение, «Быстро» — плотная нарезка.
SPEED_PRESETS = {
    "calm": {"max_words": 8, "max_chars": 30, "max_gap": 1.10, "hold": 0.60, "min_cue": 1.8, "fade": (90, 130)},
    "normal": {"max_words": 6, "max_chars": 24, "max_gap": 0.80, "hold": 0.40, "min_cue": 1.4, "fade": (75, 105)},
    "fast": {"max_words": 4, "max_chars": 18, "max_gap": 0.55, "hold": 0.20, "min_cue": 1.1, "fade": (60, 85)},
}
SPEEDS = tuple(SPEED_PRESETS)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font_family},{sub_size},{sub_primary},{sub_secondary},&H00000000,&H78000000,0,0,0,0,100,100,1,0,1,{outline},3,2,70,70,320,1
Style: Hook,{font_family},{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H50000000,0,0,0,0,100,100,0.5,0,1,8,4,8,80,80,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2190-\u21FF\u2B00-\u2BFF]+"
)

# эмпирические коэффициенты libass: кегль ~0.45 от номинала, ширина символа ~0.62
_RENDER_FACTOR = 0.45
_WIDTH_FACTOR = 0.62
_SAFE_WIDTH = 940.0


def font_exists(key: str) -> bool:
    if key in FONTS:
        return True
    try:
        from . import custom_fonts

        return custom_fonts.get(key) is not None
    except Exception:
        return False


def font_info(key: str) -> Dict[str, Any]:
    if key in FONTS:
        return FONTS[key]
    try:
        from . import custom_fonts

        custom = custom_fonts.get(key)
        if custom:
            return {**FONTS["montserrat"], **custom}
    except Exception:
        pass
    return FONTS["montserrat"]


def fonts_payload() -> List[Dict[str, str]]:
    return [{"key": k, "label": v["label"]} for k, v in FONTS.items()]


def _clean(text: str) -> str:
    text = _EMOJI_RE.sub("", text)
    text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
    return re.sub(r"\s+", " ", text).strip()


def _chars_per_line(font: Dict[str, Any], size: int) -> int:
    advance = max(0.3, float(font["avg"])) * max(20, size) * _WIDTH_FACTOR
    return max(8, min(24, int(_SAFE_WIDTH / advance)))


def _wrap(text: str, max_chars: int, max_lines: int = 2) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) <= max_chars or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _cues(words: List[Dict[str, Any]], max_chars: int = 32, max_words: int = 6,
          max_gap: float = 0.55) -> List[List[Dict[str, Any]]]:
    cues: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for w in words:
        token = (w.get("word") or "").strip()
        if not token:
            continue
        if cur:
            gap = float(w["start"]) - float(cur[-1]["end"])
            length = len(" ".join(x["word"] for x in cur)) + len(token) + 1
            if gap > max_gap or length > max_chars or len(cur) >= max_words:
                cues.append(cur)
                cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", token):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    return cues


def _pos_tag(position: str, offset: int) -> str:
    """Возвращает override-тег положения. offset > 0 — выше/дальше от края."""
    if position == "top":
        y = max(60, 260 - offset)
        return f"\\an8\\pos({PLAY_W // 2},{y})"
    if position == "middle":
        y = max(120, min(PLAY_H - 120, 960 - offset))
        if free_pos and len(free_pos) == 2:
            return (f"\\an5\\pos({int(float(free_pos[0]) * PLAY_W)},"
                    f"{int(float(free_pos[1]) * PLAY_H)})")
        return f"\\an5\\pos({PLAY_W // 2},{y})"
    y = max(200, min(PLAY_H - 80, 1580 - offset))
    return f"\\an2\\pos({PLAY_W // 2},{y})"


def _plain(token: str) -> str:
    return re.sub(r"[^\w]+", "", str(token or "").lower().replace("ё", "е"))


def to_srt(words: List[Dict[str, Any]], start: float, end: float,
           max_words: int = 6) -> str:
    """SRT для тех, кто монтирует сам: слова собираются в короткие реплики."""
    def stamp(seconds: float) -> str:
        seconds = max(0.0, float(seconds) - start)
        hours, rest = divmod(int(seconds), 3600)
        minutes, secs = divmod(rest, 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    cues: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for w in words or []:
        if float(w.get("end", 0)) <= start or float(w.get("start", 0)) >= end:
            continue
        current.append(w)
        text = " ".join(x["word"] for x in current)
        if len(current) >= max_words or text.rstrip().endswith((".", "!", "?", "…")):
            cues.append(current)
            current = []
    if current:
        cues.append(current)

    lines: List[str] = []
    for index, cue in enumerate(cues, start=1):
        text = " ".join(w["word"] for w in cue).strip()
        if not text:
            continue
        lines.append(f"{index}\n{stamp(max(start, float(cue[0]['start'])))} --> "
                     f"{stamp(min(end, float(cue[-1]['end'])))}\n{text}\n")
    return "\n".join(lines)


def retime_words(words: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Переносит правленый текст обратно на тайминги слов.

    Пользователь исправляет «бак» на «баг» или имя бренда, а субтитры должны
    остаться синхронными: неизменённые слова сохраняют свои тайминги, новые
    распределяются по времени заменённого куска. Так правка текста не требует
    ручной расстановки времени по словам.
    """
    import difflib

    clean = (text or "").strip()
    if not clean:
        return []
    original = [dict(w) for w in (words or [])]
    if not original:
        return []
    new_tokens = clean.split()
    ref = [_plain(w.get("word")) for w in original]
    hyp = [_plain(t) for t in new_tokens]

    matcher = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    out: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                item = dict(original[i1 + k])
                item["word"] = new_tokens[j1 + k]
                out.append(item)
            continue
        if tag == "delete":
            continue
        # replace / insert: растягиваем новые слова на время исходного куска
        if i1 < i2:
            t0 = float(original[i1]["start"])
            t1 = float(original[i2 - 1]["end"])
        else:
            if i1 < len(original):
                t0 = float(original[i1].get("start", 0.0))
                t1 = t0 + 0.4
            else:
                t0 = float(original[-1].get("end", 0.0))
                t1 = t0 + 0.4
        if j2 - j1 == 1 and i2 > i1:
            # одно слово вместо куска — оставляем ровно исходный интервал
            out.append({"word": new_tokens[j1], "start": round(t0, 3), "end": round(t1, 3)})
            continue
        count = max(1, j2 - j1)
        step = max(0.06, (t1 - t0) / count)
        for k in range(j2 - j1):
            start = t0 + k * step
            out.append({"word": new_tokens[j1 + k], "start": round(start, 3),
                        "end": round(start + step * 0.95, 3)})
    out.sort(key=lambda w: float(w.get("start", 0.0)))
    return out


def build_ass(words: List[Dict[str, Any]], clip_start: float, clip_end: float,
              hook: str = "", accent: str = "yellow", uppercase: bool = True,
              show_hook: bool = True, sub_size: int = 118, hook_size: int = 96,
              font: str = "montserrat",
              position: str = "bottom", offset: int = 0,
              free_pos: Optional[List[float]] = None,
              speed: str = "calm") -> str:
    """Собирает ASS-файл для фрагмента. Тайминги слов абсолютные (от начала видео)."""
    accent_color = ACCENTS.get(accent, ACCENTS["yellow"])
    info = font_info(font)
    scale = 0.700 / float(info["cap"])
    size = max(40, min(240, int(round(sub_size * scale))))
    hook_size_eff = max(30, int(round(hook_size * scale)))
    chars = _chars_per_line(info, size)
    if position not in POSITIONS:
        position = "bottom"

    preset = SPEED_PRESETS.get(str(speed), SPEED_PRESETS["calm"])
    header = ASS_HEADER.format(
        font_family=info["family"], sub_size=size, hook_size=hook_size_eff,
        outline=7, sub_primary=accent_color, sub_secondary="&H00FFFFFF&",
    )
    events: List[str] = []
    clip_len = max(0.1, clip_end - clip_start)
    pos = _pos_tag(position, int(offset))

    local = []
    for w in words:
        s = max(0.0, float(w["start"]) - clip_start)
        e = max(s + 0.05, float(w["end"]) - clip_start)
        if s >= clip_len:
            continue
        token = _clean(str(w.get("word") or ""))
        if not token:
            continue
        if uppercase:
            token = token.upper()
        local.append({"word": token, "start": s, "end": min(e, clip_len)})

    # Whisper иногда даёт одинаковые/пересекающиеся тайминги слов — выравниваем,
    # иначе несколько реплик рисуются одновременно и текст накладывается.
    local.sort(key=lambda item: item["start"])
    cursor = 0.0
    for item in local:
        item["start"] = max(item["start"], cursor)
        item["end"] = max(item["end"], item["start"] + 0.08)
        cursor = item["end"]

    if show_hook and hook.strip():
        hook_text = _clean(hook).upper() if uppercase else _clean(hook)
        if len(hook_text) > 50:
            hook_text = hook_text[:49].rsplit(" ", 1)[0].rstrip(" ,.-—") + "…"
        hook_lines = _wrap(hook_text, max(10, int(chars * 1.15)), 3)
        body = "\\N".join(hook_lines)
        start, end = 0.15, min(clip_len - 0.1, max(1.8, min(4.2, clip_len * 0.3)))
        # если субтитры сверху — хук уводим вниз, чтобы не перекрывались
        hook_pos = "\\an2\\pos(540,1700)" if position == "top" else "\\an8\\pos(540,240)"
        events.append(
            f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Hook,,0,0,0,,"
            f"{{{hook_pos}\\fad(180,220)\\fscx104\\fscy104}}"
            f"{body}"
        )

    # Короткие реплики + караоке-подсветка: текст не «мигает» на каждом слове,
    # а плавно закрашивается цветом акцента и висит ещё немного после речи.
    cue_chars = max(12, min(int(chars * 2 - 2), int(preset["max_chars"])))
    cues = _cues(local, max_chars=cue_chars, max_words=int(preset["max_words"]),
                 max_gap=float(preset["max_gap"]))

    timed: List[Any] = []
    for index, cue in enumerate(cues):
        if not cue:
            continue
        start = max(0.0, float(cue[0]["start"]))
        end = min(clip_len, max(float(cue[-1]["end"]) + float(preset["hold"]),
                                start + float(preset["min_cue"])))
        if index + 1 < len(cues) and cues[index + 1]:
            nxt = max(start + 0.3, float(cues[index + 1][0]["start"]) - 0.02)
            end = min(end, nxt)
        timed.append((cue, start, end))

    fade_in, fade_out = preset["fade"]
    for cue, cue_start, cue_end in timed:
        if cue_end - cue_start < 0.35:
            continue
        tokens = [w["word"] for w in cue]
        # перенос строк: 1–2 строки, сбалансированные по ширине
        line_groups = [[i for i in range(len(tokens))]]
        total_len = sum(len(t) + 1 for t in tokens) - 1
        if total_len > chars and len(tokens) > 1:
            best, best_diff = None, 1e9
            for split in range(1, len(tokens)):
                left = " ".join(tokens[:split])
                right = " ".join(tokens[split:])
                if len(left) > chars + 2 or len(right) > chars + 2:
                    continue
                diff = abs(len(left) - len(right))
                if diff < best_diff:
                    best, best_diff = split, diff
            if best:
                line_groups = [list(range(best)), list(range(best, len(tokens)))]

        # длительности \k: каждое слово подсвечивается ровно в свой момент
        karaoke: List[int] = []
        for wi, word in enumerate(cue):
            nxt = float(cue[wi + 1]["start"]) if wi + 1 < len(cue) else float(cue_end)
            duration = max(0.03, nxt - float(word["start"]))
            karaoke.append(max(1, int(round(duration * 100))))

        rendered: List[str] = []
        for group in line_groups:
            rendered.append(" ".join(
                f"{{\\k{karaoke[ti]}}}{tokens[ti]}" for ti in group
            ))
        body = "\\N".join(rendered)
        tags = (f"\\fad({int(fade_in)},{int(fade_out)})"
                f"\\fscx104\\fscy104\\t(0,170,\\fscx100\\fscy100)")
        events.append(
            f"Dialogue: 0,{ass_time(cue_start)},{ass_time(cue_end)},Sub,,0,0,0,,"
            f"{{{pos}{tags}}}{body}"
        )

    return header + "\n".join(events) + "\n"
