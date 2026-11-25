"""Поиск «вирусных» фрагментов: эвристики по тексту, паузам и громкости.

Без внешних API: считаем потенциальную удержку по структуре речи.
Каждый клип получает score 0..1 и объяснение (какие факторы сработали).
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import utils
from .config import ffmpeg

# --- Лексиконы ---------------------------------------------------------------

HOOK_PATTERNS_RU = [
    r"\bпочему\b", r"\bкак\b", r"\bчто если\b", r"\bникогда\b", r"\bвсегда\b",
    r"\bсекрет", r"\bошибк", r"\bникто не\b", r"\bкаждый\b", r"\bглавн",
    r"\bпредставь", r"\bзнаешь", r"\bвыяснил", r"\bоказалось", r"\bправда\b",
    r"\bзапрещ", r"\bбесплатн", r"\bденьг", r"\bмиллион", r"\bтысяч",
    r"\bшок", r"\bсрочно", r"\bвнимание", r"\bстоп\b", r"\bхватит\b",
]
HOOK_PATTERNS_EN = [
    r"\bwhy\b", r"\bhow\b", r"\bwhat if\b", r"\bnever\b", r"\bsecret", r"\bmistake",
    r"\bnobody\b", r"\btruth\b", r"\bwarning\b", r"\binsane", r"\bcrazy\b",
    r"\bactually\b", r"\bhere'?s\b", r"\bstop\b", r"\bmoney\b", r"\bmillion",
]
EMOTION_RU = [
    "страшн", "шок", "безумн", "невероятн", "ужасн", "провал", "провалил", "катастроф",
    "паник", "злил", "ненавиж", "любл", "мечт", "боял", "стыдн", "смешн", "туп",
    "гениальн", "лёгк", "легк", "сложн", "больн", "обидн", "радост", "счастлив",
    "ошибк", "проблем", "кризис", "долг", "банкрот", "богат", "бедн", "потерял",
    "заработал", "вырос", "упал", "провал", "секрет", "обман", "правд", "жесток",
]
EMOTION_EN = [
    "shock", "crazy", "insane", "terrible", "amazing", "incredible", "fail", "failed",
    "disaster", "panic", "hate", "love", "dream", "afraid", "embarrass", "funny",
    "stupid", "genius", "easy", "hard", "painful", "rich", "poor", "lost", "earned",
    "grew", "dropped", "secret", "lie", "truth", "brutal", "mistake",
]
FILLER_START = [
    "и вот", "ну", "вот", "так", "короче", "значит", "эм", "ээ", "как бы", "типа",
    "в общем", "ладно", "окей", "so", "well", "um", "uh", "like", "okay", "yeah",
]

STOPWORDS = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где
есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж
тогда кто этот того потому этого какой совсем ним здесь эту сказать всех никогда сегодня
можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая
много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой
им более всегда конечно всю между это очень мой твой ваш наш весь это как-то что-то
the a an and or but if then than that this these those i you he she it we they me my your
is are was were be been being do does did have has had not no yes just very really so well
to of in on at for with from by about as into over after before under again more most some
""".split())

# С каких слов нельзя начинать клип: без предыдущей фразы зритель не поймёт,
# о чём речь. Главная жалоба на автонарезку — «клип начинается после завязки».
CONTEXT_STARTERS = [
    "потому что", "поэтому", "в итоге", "так вот", "и вот", "вот почему", "вот что",
    "значит", "короче", "это", "этот", "эта", "эти", "такой", "такая", "такое",
    "тогда", "но", "однако", "а", "и", "он", "она", "они", "его", "ее", "ему", "ей",
    "им", "их", "там", "тут", "тоже", "также",
    "because", "so", "this", "that", "these", "those", "it", "they", "them", "he",
    "she", "his", "her", "but", "and", "then", "which", "there",
]

# Слова-связки в конце фразы: мысль не закончена, клип оборвётся.
CONTINUATION_END = [
    "потому что", "потому", "так что", "и", "а", "но", "или", "либо", "которые",
    "который", "которая", "которое", "что", "чтобы", "если", "когда", "пока",
    "because", "so", "and", "but", "or", "which", "that", "if", "when", "while",
]

# Спонсорские вставки: их нельзя резать в клипы — «промокод», «ссылка в описании»
# и «этот выпуск спонсируется» в ленте выглядят как реклама, а не как контент.
HOOK_STARTERS = ["почему", "как", "что если", "а что", "знаешь", "смотри",
                 "представь", "внимание", "главное", "секрет", "ошибка",
                 "why", "how", "what if", "watch", "imagine", "secret", "mistake"]

AD_STRONG = [
    "промокод", "промо код", "промокоду", "скидк", "скидка", "скидку", "купон",
    "ссылка в описании", "в описании", "по ссылке", "спонсор", "реклама",
    "рекламодател", "партнёр", "партнер", "при поддержке", "благодаря",
    "спонсируется", "наш партн", "промо",
    "sponsored", "promo code", "discount", "use code", "link in the description",
    "in the description", "our sponsor", "thanks to our", "partner",
]
AD_WEAK = [
    "подпишись", "подписывайтесь", "подписывайся", "бесплатн", "попробуй",
    "заказать", "магазин", "сервис", "доставка", "тариф", "бесплатный период",
    "первый месяц", "процент", "экономия", "выгодн", "акция",
    "subscribe", "free trial", "our partners", "check out", "sign up",
]

_NUM_RE = re.compile(r"\d+[.,]?\d*|%|₽|\$|€|руб|тысяч|миллион|миллиард|процент", re.I)
_SENT_END = re.compile(r"[.!?…]+[\"'»)]*\s*$")
_WORD_RE = re.compile(r"[\w\-']+", re.U)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _count_patterns(text: str, patterns: List[str]) -> int:
    low = _norm(text)
    return sum(1 for p in patterns if re.search(p, low))


def _has_filler_start(text: str) -> bool:
    low = _norm(text).strip(" «\"'—-")
    return any(low.startswith(f) for f in FILLER_START)


def _ad_score(text: str) -> int:
    """Насколько фраза похожа на рекламную вставку (0 — не похожа)."""
    low = _norm(text)
    strong = sum(1 for p in AD_STRONG if p in low)
    weak = sum(1 for p in AD_WEAK if p in low)
    return strong * 2 + weak


def detect_ads(sents: List[Dict[str, Any]], threshold: int = 2) -> List[Dict[str, Any]]:
    """Находит спонсорские блоки: подряд идущие фразы с рекламными маркерами."""
    blocks: List[Dict[str, Any]] = []
    current: List[int] = []

    def flush() -> None:
        if len(current) >= 1 and sum(_ad_score(sents[i]["text"]) for i in current) >= threshold:
            blocks.append({"from": current[0], "to": current[-1],
                           "start": sents[current[0]]["start"],
                           "end": sents[current[-1]]["end"],
                           "score": sum(_ad_score(sents[i]["text"]) for i in current)})
        current.clear()

    for i, sent in enumerate(sents):
        score = _ad_score(sent["text"])
        if score >= 2:
            current.append(i)
        elif current and score == 1:
            current.append(i)  # «подпишись» рядом с промокодом — тот же блок
        else:
            flush()
    flush()
    return blocks


def _ad_overlap(start: float, end: float, blocks: List[Dict[str, Any]]) -> float:
    """Сколько секунд клипа приходится на рекламные блоки."""
    total = 0.0
    for block in blocks:
        total += max(0.0, min(end, block["end"]) - max(start, block["start"]))
    return total


def _hook_bonus(text: str) -> float:
    """Бонус за цепляющее начало: вопрос, цифра, обещание."""
    low = _norm(text)
    bonus = 0.0
    if any(low.startswith(h) for h in HOOK_STARTERS):
        bonus += 3.0
    if re.search(r"\d", low[:40]):
        bonus += 1.5
    if low.rstrip().endswith("?"):
        bonus += 2.0
    return bonus


def _starts_with_context(sent: Dict[str, Any]) -> bool:
    """Фраза начинается с местоимения/связки — без предыдущей фразы непонятна."""
    words = _norm(sent.get("text") or "").strip(" «\"'—-").split()
    if not words:
        return False
    head = " ".join(words[:2])
    first = words[0]
    for marker in CONTEXT_STARTERS:
        if " " in marker:
            if head.startswith(marker):
                return True
        elif first == marker:
            return True
    return False


def _ends_with_continuation(sent: Dict[str, Any]) -> bool:
    """Фраза обрывается на связке — мысль продолжится в следующей."""
    text = _norm(sent.get("text") or "").strip()
    if not text:
        return True
    if text.endswith(("?", "!", "…")):
        return False
    if text.endswith((",", "—", "-", ":", ";", "–")):
        return True  # фраза оборвана посреди мысли
    text = text.rstrip(".,;:")
    words = text.split()
    if not words:
        return True
    tail1 = words[-1]
    tail2 = " ".join(words[-2:])
    if tail2 in ("потому что", "так что", "в том что"):
        return True
    return tail1 in CONTINUATION_END


def _extend_window(sents: List[Dict[str, Any]], i: int, j: int,
                   max_dur: float, max_back: int = 2, max_fwd: int = 2) -> Tuple[int, int]:
    """Дотягивает окно до завязки слева и до вывода справа, если это укладывается в лимит.

    Именно из-за этого автонарезка «начинается после завязки» и «обрывается за
    секунду до главной фразы»: границы берутся по форме предложений, а не по смыслу.
    """
    span = lambda a, b: sents[b]["end"] - sents[a]["start"]
    back = 0
    while back < max_back and i > 0 and _starts_with_context(sents[i]) and span(i - 1, j) <= max_dur:
        i -= 1
        back += 1
    fwd = 0
    while fwd < max_fwd and j + 1 < len(sents):
        nxt = sents[j + 1]
        needs_tail = _ends_with_continuation(sents[j])
        # короткий вопрос + короткий ответ — это завязка и развязка одной мысли
        question_payoff = (
            sents[j]["text"].strip().endswith("?")
            and len(_WORD_RE.findall(sents[j]["text"])) <= 14
            and len(_WORD_RE.findall(nxt["text"])) <= 22
        )
        short_conclusion = _starts_with_context(nxt) and len(_WORD_RE.findall(nxt["text"])) <= 14
        if not (needs_tail or short_conclusion or question_payoff) or span(i, j + 1) > max_dur:
            break
        j += 1
        fwd += 1
    return i, j


def build_sentences(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Склеивает слова в предложения по пунктуации и паузам."""
    words: List[Dict[str, Any]] = []
    for seg in segments:
        for w in seg.get("words") or []:
            token = (w.get("word") or "").strip()
            if token:
                words.append({"word": token, "start": float(w["start"]), "end": float(w["end"])})
    if not words:
        return []

    sentences: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []

    def flush() -> None:
        if not cur:
            return
        text = " ".join(w["word"] for w in cur)
        text = re.sub(r"\s+([,.!?…:;])", r"\1", text).strip()
        sentences.append({
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
            "text": text,
            "words": list(cur),
        })
        cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            if gap > 0.7 and len(cur) >= 3:
                flush()
        cur.append(w)
        ends_sentence = bool(_SENT_END.search(w["word"])) or w["word"].endswith(("?", "!", "…"))
        too_long = len(" ".join(x["word"] for x in cur)) > 220
        if ends_sentence or too_long:
            flush()
    flush()
    return sentences


def energy_curve(media: Path, hop: float = 0.25) -> Tuple[List[float], float]:
    """RMS-громкость по кадрам (дБ) и средний уровень."""
    try:
        import numpy as np

        audio = utils.read_audio_mono(media)
        sr = 16000
        n = max(1, int(hop * sr))
        frames = len(audio) // n
        if frames == 0:
            return [], -60.0
        trimmed = audio[: frames * n].reshape(frames, n)
        rms = np.sqrt(np.mean(trimmed ** 2, axis=1) + 1e-12)
        db = 20 * np.log10(np.maximum(rms, 1e-6))
        return [float(x) for x in db], float(np.mean(db))
    except Exception:
        return [], -30.0


# Кривая громкости нужна и поиску моментов, и вырезанию пауз, и подтяжке краёв.
# Без кэша один и тот же исходник декодировался по разу на каждый клип.
_ENERGY_CACHE: Dict[Tuple[str, float, int, float], Tuple[List[float], float]] = {}
_ENERGY_LOCK = threading.Lock()
_ENERGY_CACHE_MAX = 4


def cached_energy_curve(media: Path, hop: float = 0.25) -> Tuple[List[float], float]:
    """energy_curve с кэшем по файлу (путь + mtime + размер)."""
    try:
        stat = Path(media).stat()
        key = (str(Path(media).resolve()), round(stat.st_mtime, 3), int(stat.st_size), hop)
    except OSError:
        return energy_curve(media, hop)
    with _ENERGY_LOCK:
        hit = _ENERGY_CACHE.get(key)
    if hit is not None:
        return hit[0], hit[1]
    value = energy_curve(media, hop)
    with _ENERGY_LOCK:
        if len(_ENERGY_CACHE) >= _ENERGY_CACHE_MAX:
            _ENERGY_CACHE.clear()
        _ENERGY_CACHE[key] = value
    return value


def _energy_stats(curve: List[float], mean_db: float, start: float, end: float, hop: float) -> Tuple[float, float]:
    """Средняя громкость и доля тишины в окне."""
    if not curve:
        return 0.0, 0.0
    a, b = int(start / hop), int(end / hop)
    a = max(0, min(a, len(curve) - 1))
    b = max(a + 1, min(b, len(curve)))
    window = curve[a:b]
    if not window:
        return 0.0, 0.0
    mean = sum(window) / len(window)
    # нормализуем относительно среднего по всему видео
    loud = max(0.0, min(1.0, (mean - (mean_db - 6)) / 12.0))
    silence = sum(1 for x in window if x < mean_db - 18) / len(window)
    return loud, silence


def _nearest_quiet(curve: List[float], mean_db: float, t: float, hop: float,
                   window: float = 0.6) -> float:
    """Сдвигает границу к ближайшей тихой точке — стык звучит чище."""
    if not curve:
        return t
    lo = max(0, int((t - window) / hop))
    hi = min(len(curve) - 1, int((t + window) / hop))
    best, best_score = t, 1e9
    for i in range(lo, hi + 1):
        score = abs(curve[i] - (mean_db - 20)) + abs(i * hop - t) * 2.0
        if curve[i] < mean_db - 15 and score < best_score:
            best, best_score = i * hop, score
    return best


def _score_window(sents: List[Dict[str, Any]], media_duration: float,
                  curve: List[float], mean_db: float, hop: float,
                  reactions: Optional[Dict[str, List[Dict[str, float]]]] = None,
                  scenes: Optional[List[float]] = None
                  ) -> Tuple[float, Dict[str, float]]:
    text = " ".join(s["text"] for s in sents)
    words = [w for s in sents for w in s["words"]]
    n_words = max(1, len(words))
    dur = sents[-1]["end"] - sents[0]["start"]
    first = sents[0]["text"]
    low = _norm(text)

    # 1. Хук в первые секунды
    hook = 0.0
    if "?" in first:
        hook += 0.30
    if _NUM_RE.search(first):
        hook += 0.18
    if re.search(r"\b(ты|тебя|твой|вы|ваш|твое)\b", _norm(first)):
        hook += 0.16
    hits = _count_patterns(first, HOOK_PATTERNS_RU + HOOK_PATTERNS_EN)
    hook += min(0.42, hits * 0.16)
    if _has_filler_start(first):
        hook -= 0.28
    if len(_WORD_RE.findall(first)) > 28:
        hook -= 0.12
    if re.search(r"[.!?…]", first):
        hook += 0.08
    hook = max(0.0, min(1.0, hook))

    # 2. Плотность речи
    speech_rate = n_words / max(1.0, dur)
    if 2.0 <= speech_rate <= 5.0:
        rate_score = 1.0 - abs(speech_rate - 3.2) / 3.2
    else:
        rate_score = max(0.0, 0.5 - abs(speech_rate - 3.2) * 0.25)
    loud, silence = _energy_stats(curve, mean_db, sents[0]["start"], sents[-1]["end"], hop)
    retention = 0.55 * rate_score + 0.25 * loud + 0.20 * (1.0 - min(1.0, silence * 2.2))
    retention = max(0.0, min(1.0, retention))

    # 3. Эмоциональная насыщенность
    emo_hits = _count_patterns(text, EMOTION_RU) + _count_patterns(text, EMOTION_EN)
    emo_density = emo_hits / max(1.0, n_words / 100.0)
    emotion = min(1.0, emo_density / 7.0)

    # 4. Конкретика: цифры, деньги, имена
    num_hits = len(_NUM_RE.findall(text))
    specificity = min(1.0, num_hits / 3.0)

    # 5. Завершённость: последнее предложение заканчивается мыслью
    last = sents[-1]["text"].strip()
    completeness = 0.6
    if re.search(r"[.!?…]$", last):
        completeness = 0.85
    if _ends_with_continuation(sents[-1]):
        completeness -= 0.35
    if last.endswith("?") or re.search(r"\b(вот и все|итог|вывод|запомни|вывод)\b", _norm(last)):
        completeness = 1.0
    if len(_WORD_RE.findall(last)) < 3:
        completeness -= 0.3
    completeness = max(0.0, min(1.0, completeness))

    # 6. Формат: оптимальная длина 22–45 секунд
    if 22 <= dur <= 45:
        length_fit = 1.0
    elif dur < 22:
        length_fit = max(0.0, (dur - 10) / 12.0)
    else:
        length_fit = max(0.0, 1.0 - (dur - 45) / 30.0)

    # 7. Наличие «крючка» в конце (вопрос/призыв)
    tail = _norm(" ".join(s["text"] for s in sents[-1:]))
    payoff = 1.0 if ("?" in tail or re.search(r"\b(подпис|коммент|сохран|попроб|сделай)\b", tail)) else 0.45

    # 8. Реакция аудитории: смех и аплодисменты — сильнейший сигнал «это
    # стоит смотреть», но по тексту его не видно, поэтому считаем по звуку.
    window_start, window_end = sents[0]["start"], sents[-1]["end"]
    reaction = 0.0
    if reactions:
        laughter = _overlap_seconds(window_start, window_end, reactions.get("laughter") or [])
        applause = _overlap_seconds(window_start, window_end, reactions.get("applause") or [])
        reaction = min(1.0, (laughter * 1.0 + applause * 0.6) / max(2.0, dur * 0.15))

    # 9. Сцена: клип, который начинается с монтажной склейки, выглядит цельно;
    # частая смена сцен внутри окна — признак динамичного фрагмента.
    visual = 0.0
    if scenes:
        inside = [t for t in scenes if window_start <= t <= window_end]
        starts_on_cut = any(abs(window_start - t) <= 0.4 for t in scenes)
        density = min(1.0, len(inside) / max(1.0, dur / 12.0))
        visual = 0.6 * (1.0 if starts_on_cut else 0.0) + 0.4 * density

    score = (
        0.28 * hook
        + 0.18 * retention
        + 0.16 * emotion
        + 0.08 * specificity
        + 0.12 * completeness
        + 0.12 * length_fit
        + 0.06 * payoff
        + 0.07 * reaction
        + 0.04 * visual
    )
    parts = {
        "hook": round(hook, 3), "retention": round(retention, 3), "emotion": round(emotion, 3),
        "specificity": round(specificity, 3), "completeness": round(completeness, 3),
        "length_fit": round(length_fit, 3), "payoff": round(payoff, 3),
        "reaction": round(reaction, 3), "visual": round(visual, 3),
    }
    return round(min(1.0, max(0.0, score)), 4), parts


def _keywords(text: str, limit: int = 6) -> List[str]:
    counts: Dict[str, int] = {}
    for w in _WORD_RE.findall(text):
        low = w.lower()
        if len(low) < 4 or low in STOPWORDS or low.isdigit():
            continue
        counts[low] = counts.get(low, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [w for w, _ in ranked[:limit]]


def _title_score(text: str) -> float:
    """Насколько фраза годится в заголовок клипа.

    Короткие переходы («Что дальше?», «И вот итог.») в заголовок не идут: они
    ничего не обещают зрителю, а в ленте выглядят мусором.
    """
    t = text.strip()
    length = len(t)
    score = 0.0
    if 25 <= length <= 72:
        score += 2.0
    elif 18 <= length < 25:
        score += 0.8
    elif length < 18:
        score -= 1.5
    if "?" in t:
        score += 1.2
    if _NUM_RE.search(t):
        score += 0.6
    score += min(1.2, 0.4 * _count_patterns(t, HOOK_PATTERNS_RU + HOOK_PATTERNS_EN))
    if _has_filler_start(t):
        score -= 1.0
    if len(_WORD_RE.findall(t)) < 4:
        score -= 1.2
    return score


def _make_title(sents: List[Dict[str, Any]]) -> str:
    """Короткий заголовок: самое «цепляющее» предложение в начале клипа."""
    candidates = [s["text"].strip() for s in sents[:4] if s["text"].strip()]
    best = max(candidates, key=_title_score) if candidates else sents[0]["text"]
    best = re.sub(r"\s+", " ", best).strip(" .,-—")
    if len(best) > 64:
        cut = best[:64]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        best = cut + "…"
    return best[:1].upper() + best[1:] if best else "Фрагмент"


def _make_caption(title: str, text: str, platform: str) -> str:
    tags = _keywords(text, 6)
    base = ["shorts", "reels", "tiktok", "виральное", "нарезка"]
    tag_line = " ".join("#" + re.sub(r"[^\w]", "", t) for t in (tags + base) if t)
    return f"{title}\n\n{tag_line}"


def find_segments(segments: List[Dict[str, Any]], media: Path, duration: float,
                  count: int = 10, min_dur: float = 15, max_dur: float = 60,
                  target_dur: float = 32, platform: str = "shorts") -> List[Dict[str, Any]]:
    """Возвращает список клипов: тайминги, score, заголовок, объяснение."""
    sents = build_sentences(segments)
    if not sents:
        return []
    curve, mean_db = cached_energy_curve(media)
    hop = 0.25
    ad_blocks = detect_ads(sents)
    # Смех/аплодисменты и смены сцен — необязательные сигналы: без numpy,
    # OpenCV или звука поиск моментов продолжает работать по тексту и громкости.
    try:
        reactions: Optional[Dict[str, List[Dict[str, float]]]] = audio_reactions(media)
    except Exception:  # noqa: BLE001
        reactions = None
    try:
        scenes: Optional[List[float]] = scene_changes(media)
    except Exception:  # noqa: BLE001
        scenes = None

    candidates: List[Dict[str, Any]] = []
    n = len(sents)
    for i in range(n):
        start = sents[i]["start"]
        if start + min_dur > duration:
            break
        for j in range(i, n):
            end = sents[j]["end"]
            dur = end - start
            if dur > max_dur:
                break
            if dur < min_dur:
                continue
            # берём окно, близкое к целевому, и следующее за ним
            if dur >= target_dur or j == n - 1:
                bi, bj = _extend_window(sents, i, j, max_dur)
                window = sents[bi:bj + 1]
                w_start, w_end = window[0]["start"], window[-1]["end"]
                if w_end - w_start > max_dur:  # перестраховка на длинных фразах
                    window, w_start, w_end = sents[i:j + 1], start, end
                span_len = max(0.1, w_end - w_start)
                if ad_blocks and _ad_overlap(w_start, w_end, ad_blocks) > max(1.5, span_len * 0.2):
                    # окно в основном про спонсора — в клипы не берём
                    break
                score, parts = _score_window(window, duration, curve, mean_db, hop,
                                             reactions, scenes)
                hook = _hook_bonus(window[0]["text"])
                if hook:
                    score += hook
                    parts = {**parts, "hook": round(hook, 1)}
                candidates.append({
                    "start": round(w_start, 3), "end": round(w_end, 3),
                    "duration": round(w_end - w_start, 2),
                    "score": score, "parts": parts,
                    "text": " ".join(x["text"] for x in window),
                    "words": [w for x in window for w in x["words"]],
                    "title": _make_title(window),
                })
                break

    if not candidates and sents and duration <= 180:
        # короткий клип/запись: берём весь связный фрагмент, даже если он короче min_dur
        start = max(0.0, sents[0]["start"] - 0.2)
        end = min(duration, sents[-1]["end"] + 0.3, start + max(8.0, max_dur))
        if end - start >= 3:
            chunk = sents
            score, parts = _score_window(chunk, duration, curve, mean_db, hop,
                                         reactions, scenes)
            candidates.append({
                "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 2),
                "score": score, "parts": parts,
                "text": " ".join(x["text"] for x in chunk),
                "words": [w for x in chunk for w in x["words"]],
                "title": _make_title(chunk),
            })

    if not candidates:
        return []

    # Разнообразие: не берём пересекающиеся окна
    candidates.sort(key=lambda c: c["score"], reverse=True)
    limit = count if count > 0 else 999

    # Разнообразие по таймлайну: без него топ-N собирается из одного «сильного»
    # куска, а вторая половина видео не попадает в выдачу вовсе (жалоба на
    # смещение к первым 10–15 минутам).
    buckets = max(1, min(limit, 12))
    span = max(1.0, duration / buckets)

    def _bucket(cand: Dict[str, Any]) -> int:
        middle = (cand["start"] + cand["end"]) / 2
        return max(0, min(buckets - 1, int(middle // span)))

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for cand in candidates:
        grouped.setdefault(_bucket(cand), []).append(cand)

    best_score = candidates[0]["score"] if candidates else 0.0
    seeded: List[Dict[str, Any]] = []
    for key in sorted(grouped):
        bucket_items = sorted(grouped[key], key=lambda c: c["score"], reverse=True)
        # слабый «лучший в бакете» не должен вытеснять сильные клипы
        if bucket_items and bucket_items[0]["score"] >= best_score * 0.55:
            seeded.append(bucket_items[0])
    seeded.sort(key=lambda c: c["score"], reverse=True)

    leftovers: List[Dict[str, Any]] = []
    seeded_ids = {id(c) for c in seeded}
    for cand in candidates:
        if id(cand) not in seeded_ids:
            leftovers.append(cand)
    ordered_candidates = seeded + leftovers

    import difflib

    def _norm_title(t: str) -> str:
        return re.sub(r"[^\w]+", " ", _norm(t)).strip()

    picked: List[Dict[str, Any]] = []
    for cand in ordered_candidates:
        if len(picked) >= limit:
            break
        overlap = False
        cand_words = set(_keywords(cand["text"], 14))
        for p in picked:
            inter = min(p["end"], cand["end"]) - max(p["start"], cand["start"])
            if inter > 0:
                short = min(p["end"] - p["start"], cand["end"] - cand["start"])
                if inter / short > 0.18:
                    overlap = True
                    break
            if difflib.SequenceMatcher(None, _norm_title(cand["title"]), _norm_title(p["title"])).ratio() > 0.82:
                overlap = True
                break
            # почти одинаковый текст (повторы, нарезка одного и того же) — тоже дубль
            p_words = set(_keywords(p["text"], 14))
            if cand_words and p_words:
                jaccard = len(cand_words & p_words) / len(cand_words | p_words)
                if jaccard > 0.62:
                    overlap = True
                    break
        if not overlap:
            picked.append(cand)

    picked.sort(key=lambda c: c["score"], reverse=True)
    out: List[Dict[str, Any]] = []
    for idx, c in enumerate(picked):
        start = c["start"]
        end = c["end"]
        # аккуратные границы: отступаем в тишину
        for block in ad_blocks:
            if start < block["start"] < end and start > block["start"] - 0.5:
                start = block["start"]
            if start < block["end"] < end and end < block["end"] + 0.5:
                end = block["end"]
        start = max(0.0, _nearest_quiet(curve, mean_db, start - 0.10, hop, window=0.30))
        end = min(duration, _nearest_quiet(curve, mean_db, end + 0.18, hop))
        if scenes:
            # начинать с монтажной склейки, а не посреди сцены; но не ценой
            # обрезания ниже минимальной длины
            nearest = min(scenes, key=lambda t: abs(t - start))
            if (abs(nearest - start) <= 0.35 and end - nearest >= min_dur * 0.9
                    and nearest < start + 0.35):
                start = max(0.0, nearest)
        words = [w for w in c["words"] if w["end"] > start + 0.05 and w["start"] < end - 0.05]
        clip_text = " ".join(w["word"] for w in words)
        out.append({
            "idx": idx,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 2),
            "score": c["score"],
            "parts": c["parts"],
            "title": c["title"],
            "hook": c["title"],
            "caption": _make_caption(c["title"], clip_text, platform),
            "keywords": _keywords(clip_text, 6),
            "words": words,
        })
    return out


def silence_gaps(curve: List[float], mean_db: float, start: float, end: float,
                 hop: float = 0.25, min_gap: float = 0.45,
                 threshold: float = 9.0) -> List[Dict[str, float]]:
    """Паузы внутри окна: тише среднего на threshold дБ дольше min_gap."""
    gaps: List[Dict[str, float]] = []
    limit = mean_db - threshold
    run_start = None
    for i in range(max(0, int(start / hop)), min(len(curve), int(end / hop) + 1)):
        quiet = float(curve[i]) < limit
        t = i * hop
        if quiet and run_start is None:
            run_start = t
        elif not quiet and run_start is not None:
            if t - run_start >= min_gap:
                gaps.append({"start": round(run_start, 2), "end": round(t, 2),
                             "length": round(t - run_start, 2)})
            run_start = None
    if run_start is not None:
        tail = end
        if tail - run_start >= min_gap:
            gaps.append({"start": round(run_start, 2), "end": round(tail, 2),
                         "length": round(tail - run_start, 2)})
    return gaps


def cut_plan(start: float, end: float, gaps: List[Dict[str, float]],
             padding: float = 0.12, min_keep: float = 0.35) -> List[Dict[str, float]]:
    """План склейки окна без пауз.

    Каждый отрезок знает своё новое время на выходе (`output`), поэтому
    пересчёт слов считается без повторного сложения.
    """
    spans: List[Dict[str, float]] = []
    cursor = start
    out = 0.0
    for gap in sorted(gaps, key=lambda g: float(g["start"])):
        cut_from = max(start, float(gap["start"]) + padding)
        cut_to = min(end, float(gap["end"]) - padding)
        if cut_to <= cut_from or cut_from <= cursor:
            cursor = max(cursor, cut_to)
            continue
        if cut_from - cursor >= min_keep:
            spans.append({"start": round(cursor, 3), "end": round(cut_from, 3),
                          "output": round(out, 3)})
            out += cut_from - cursor
        cursor = max(cursor, cut_to)
    if end - cursor >= min_keep:
        spans.append({"start": round(cursor, 3), "end": round(end, 3), "output": round(out, 3)})
    return spans or [{"start": round(start, 3), "end": round(end, 3), "output": 0.0}]


def remap_words(words: List[Dict[str, Any]], spans: List[Dict[str, float]]
                ) -> List[Dict[str, Any]]:
    """Слова под склейку: выбросить попавшие в паузы, сдвинуть на новое время."""
    out: List[Dict[str, Any]] = []
    for span in spans:
        shift = float(span["output"]) - float(span["start"])
        for word in words:
            w_start, w_end = float(word["start"]), float(word["end"])
            if w_end <= span["start"] or w_start >= span["end"]:
                continue
            out.append({**word,
                        "start": round(max(0.0, w_start - span["start"]) + span["output"], 3),
                        "end": round(max(0.0, w_end - span["start"]) + span["output"], 3)})
    return out


# --- Реакции аудитории и смены сцен -----------------------------------------
#
# Смех и аплодисменты — это не речь, и по тексту их не видно. Считаем по звуку:
# смех — всплеск в 1.5–7 кГц с ритмичной огибающей 4–7 Гц («ха-ха-ха»), аплодисменты
# — выровненный широкополосный шум с высокой спектральной плоскостью. Смена сцены
# — скачок гистограммы между соседними кадрами (OpenCV). Обе вещи необязательные:
# если numpy/OpenCV нет или файл не читается, поиск моментов работает как раньше.

_SR = 16000
_N_FFT = 512          # 32 мс — компромисс между частотой и разрешением огибающей
_HOP = 256            # 16 мс
_WIN_SECONDS = 1.0
_STEP_SECONDS = 0.25


def _frame_features(audio: Any) -> Optional[Dict[str, Any]]:
    """Покадровые признаки: RMS, спектральная плоскость, доля ВЧ, переходы нуля."""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    if audio is None or len(audio) < _N_FFT:
        return None
    count = 1 + (len(audio) - _N_FFT) // _HOP
    if count <= 0:
        return None
    window = np.hanning(_N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / _SR)
    high_band = (freqs >= 1500) & (freqs <= 7000)
    rms = np.empty(count, dtype=np.float32)
    flatness = np.empty(count, dtype=np.float32)
    high_ratio = np.empty(count, dtype=np.float32)
    zcr = np.empty(count, dtype=np.float32)
    block = 1024
    for start in range(0, count, block):
        stop = min(count, start + block)
        idx = np.arange(_N_FFT)[None, :] + _HOP * np.arange(start, stop)[:, None]
        frames = audio[idx]
        spec = np.abs(np.fft.rfft(frames * window, axis=1)) + 1e-10
        power = spec * spec
        total = power.sum(axis=1) + 1e-12
        rms[start:stop] = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        flatness[start:stop] = np.exp(np.mean(np.log(power), axis=1)) / (power.mean(axis=1) + 1e-12)
        high_ratio[start:stop] = power[:, high_band].sum(axis=1) / total
        signs = np.signbit(frames[:, 1:]) != np.signbit(frames[:, :-1])
        zcr[start:stop] = signs.mean(axis=1)
    return {"rms": rms, "flatness": flatness, "high_ratio": high_ratio,
            "zcr": zcr, "sec_per_frame": _HOP / _SR}


def _modulation(envelope: Any) -> float:
    """Доля энергии огибающей в полосе 3–9 Гц: «ха-ха-ха» ритмично, аплодисменты — нет."""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return 0.0
    if len(envelope) < 8:
        return 0.0
    centered = envelope - float(np.mean(envelope))
    if not np.any(centered):
        return 0.0
    spectrum = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
    freqs = np.fft.rfftfreq(len(centered), _HOP / _SR)
    total = float(spectrum.sum()) + 1e-12
    band = (freqs >= 3.0) & (freqs <= 9.0)
    return float(spectrum[band].sum()) / total if np.any(band) else 0.0


def _reaction_segments(features: Dict[str, Any], kind: str) -> List[Dict[str, float]]:
    """Скользящее окно 1 с → отрезки, где признаки похожи на смех или аплодисменты.

    Считаем сначала покадровые признаки, а окно считаем «сработавшим» только
    если не меньше 75% кадров в нём подходят. Иначе окно, накрывшее тишину на
    0.5 с, сдвигало границы реакции назад — клип начинался «за полсекунды до».
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    spf = float(features["sec_per_frame"])
    rms = features["rms"]
    flatness = features["flatness"]
    high_ratio = features["high_ratio"]
    zcr = features["zcr"]
    if len(rms) < 8:
        return []
    # «тишина» — 5-й процентиль: реакции на фоне тишины видно, а непрерывный
    # гул/музыка не считаются «реакцией» сами по себе
    quiet_floor = max(float(np.percentile(rms, 5)) * 2.0, 0.004)
    if kind == "laughter":
        frame_ok = ((high_ratio > 0.30) & (flatness > 0.015) & (flatness < 0.70)
                    & (zcr > 0.05) & (zcr < 0.55) & (rms > quiet_floor))
    else:
        frame_ok = ((flatness > 0.05) & (high_ratio > 0.50)
                    & (zcr > 0.26) & (rms > quiet_floor))

    win = max(8, int(round(_WIN_SECONDS / spf)))
    step = max(1, int(round(_STEP_SECONDS / spf)))
    if len(rms) < win:
        return []
    # доля «подходящих» кадров в окне: у смеха огибающая ритмичная и часть кадров
    # приходится на «провалы» между «ха», у аплодисментов шум ровный
    min_active = 0.20 if kind == "laughter" else 0.60
    positives: List[Tuple[float, float, float]] = []
    for start in range(0, len(rms) - win + 1, step):
        stop = start + win
        env = rms[start:stop]
        if float(np.mean(env > quiet_floor)) < min_active:
            continue
        mod = _modulation(env)
        # признаки усредняем с весом по энергии: «провалы» между «ха» не
        # размывают картину, а тишина вокруг реакции почти не влияет
        weights = env.astype(np.float64) + 1e-9
        flat = float(np.average(flatness[start:stop], weights=weights))
        high = float(np.average(high_ratio[start:stop], weights=weights))
        crossings = float(np.average(zcr[start:stop], weights=weights))
        if kind == "laughter":
            if mod <= 0.38 or not (0.02 < flat < 0.65) or high <= 0.30:
                continue
            if not (0.04 < crossings < 0.58):
                continue
            score = min(1.0, high * 1.6) * (0.45 + min(1.0, mod))
        else:
            if mod >= 0.55 or flat <= 0.05 or high <= 0.50 or crossings <= 0.24:
                continue
            score = min(1.0, flat * 4.0) * min(1.0, high * 1.2)
        positives.append((start * spf, stop * spf, float(score)))

    if not positives:
        return []
    merged: List[Dict[str, float]] = []
    for start, stop, score in positives:
        if merged and start - merged[-1]["end"] <= 0.35:
            merged[-1]["end"] = round(stop, 2)
            merged[-1]["score"] = round(max(merged[-1]["score"], score), 3)
        else:
            merged.append({"start": round(start, 2), "end": round(stop, 2),
                           "score": round(score, 3)})

    # края уточняем по сглаженной энергии ВЧ-полосы: окно, накрывшее тишину,
    # иначе начинало клип «за полсекунды до реакции»
    high_energy = (rms * high_ratio).astype(np.float32)
    kernel_size = max(3, int(round(0.20 / spf)))
    smooth = np.convolve(high_energy,
                         np.ones(kernel_size, dtype=np.float32) / kernel_size, mode="same")
    for seg in merged:
        a = max(0, int(round(seg["start"] / spf)))
        b = min(len(smooth), int(round(seg["end"] / spf)))
        if b <= a:
            continue
        inside = smooth[a:b]
        level = float(np.median(inside))
        if level <= 0:
            continue
        threshold = max(quiet_floor * 0.02, level * 0.25)
        inner = np.where(inside >= threshold)[0]
        if len(inner):
            seg["start"] = round((a + int(inner[0])) * spf, 2)
            seg["end"] = round((a + int(inner[-1]) + 1) * spf, 2)

    min_len = 0.6 if kind == "laughter" else 0.9
    return [seg for seg in merged if seg["end"] - seg["start"] >= min_len]


def audio_reactions(media: Path) -> Dict[str, List[Dict[str, float]]]:
    """Смех и аплодисменты в дорожке: отрезки {start, end, score}."""
    empty: Dict[str, List[Dict[str, float]]] = {"laughter": [], "applause": []}
    try:
        audio = utils.read_audio_mono(Path(media), _SR)
    except Exception:  # noqa: BLE001 — нет звука/файла: реакций просто нет
        return empty
    features = _frame_features(audio)
    if not features:
        return empty
    return {"laughter": _reaction_segments(features, "laughter"),
            "applause": _reaction_segments(features, "applause")}


def _overlap_seconds(start: float, end: float, segments: List[Dict[str, float]]) -> float:
    total = 0.0
    for seg in segments or []:
        total += max(0.0, min(end, float(seg["end"])) - max(start, float(seg["start"])))
    return total


def scene_changes(media: Path, width: int = 96, height: int = 54,
                  threshold: float = 0.42, limit: int = 3000) -> List[float]:
    """Границы сцен (секунды) по гистограммам кадров через OpenCV.

    Кадры берём только ключевые: в обычном монтаже сцена начинается с ключевого
    кадра, поэтому полный декод не нужен — для часовой записи это секунды, а не
    минуты. Кадры мелкие (96×54): для гистограммы детали не важны, зато 3000
    кадров — это ~45 МБ вместо сотен. Гистограммы сравниваем cv2.compareHist:
    плавное движение камеры скачком не считается, а склейка — считается.
    """
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001 — без OpenCV работаем без сцен
        return []
    cmd = [ffmpeg(), "-hide_banner", "-nostats", "-v", "info",
           "-skip_frame", "nokey", "-i", str(media), "-an",
           # passthrough: иначе ffmpeg дорисовывает кадры до CFR (dup=…) и
           # порядок «кадр ↔ pts_time» перестаёт совпадать
           "-vf", f"scale={width}:{height}:flags=fast_bilinear,showinfo",
           "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600,
                              **utils.subprocess_kwargs())
    except Exception:  # noqa: BLE001
        return []
    frame_size = width * height * 3
    data = proc.stdout or b""
    count = min(len(data) // frame_size, limit)
    stderr = (proc.stderr or b"").decode("utf-8", "replace")
    times = [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", stderr)]
    if count < 2 or len(times) < count:
        return []
    cuts: List[float] = []
    previous = None
    for i in range(count):
        raw = data[i * frame_size:(i + 1) * frame_size]
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [24, 12], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if previous is not None:
            distance = float(cv2.compareHist(previous, hist, cv2.HISTCMP_BHATTACHARYYA))
            if distance >= threshold:
                cuts.append(round(times[i], 3))
        previous = hist
    # склейки ближе 0.3 с — это дребезг одного и того же перехода
    clean: List[float] = []
    for t in cuts:
        if not clean or t - clean[-1] > 0.3:
            clean.append(t)
    return clean
