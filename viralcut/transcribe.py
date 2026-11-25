"""Транскрипция: локальный faster-whisper с проверкой сомнительных мест.

Качество важнее скорости, но без больших моделей:

* аудио для распознавания чистится (high-pass, low-pass, шумоподавление,
  выравнивание громкости) — движку достаётся речь, а не гул и перепады уровня;
* декодирование идёт beam-search с подсказкой-промптом и запасными
  температурами, чтобы не «залипать» на одной гипотезе;
* сегменты с низкой уверенностью перепроверяются повторно: вырезаем окно,
  декодируем ещё раз с контекстом соседей и оставляем вариант, у которого
  выше вероятность модели.

Всё это не требует скачивать другую модель и работает на том же `small`.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import utils
from .config import (DATA_DIR, MODELS_DIR, WHISPER_COMPUTE, WHISPER_DEVICE,
                     WHISPER_LOCAL_DIR, WHISPER_MODEL, ffmpeg)

# Всё, что качает HuggingFace, держим внутри папки проекта.
os.environ.setdefault("HF_HOME", str(DATA_DIR / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_MODEL = None
_MODEL_LONG = None
_MODEL_LOCK = threading.Lock()
ProgressCb = Optional[Callable[[float, str], None]]

# --- параметры распознавания -------------------------------------------------

# Beam-search вместо жадного декодирования: заметно меньше ошибок в словах.
BEAM_SIZE = max(1, int(os.environ.get("VIRALCUT_WHISPER_BEAM", "5")))
# Сколько сомнительных сегментов перепроверять (0 — выключить).
RECHECK_LIMIT = max(0, int(os.environ.get("VIRALCUT_WHISPER_RECHECK", "8")))
# Батчевое декодирование: на 6-минутной записи 2.6× быстрее и точнее, потому что
# faster-whisper режет аудио по VAD и подаёт пачки в модель вместо одного потока.
BATCH_SIZE = max(1, int(os.environ.get("VIRALCUT_WHISPER_BATCH_SIZE", "8")))
BATCH_MIN_SECONDS = float(os.environ.get("VIRALCUT_WHISPER_BATCH_MIN", "60"))
# Порог уверенности, ниже которого сегмент считаем сомнительным.
WEAK_LOGPROB = float(os.environ.get("VIRALCUT_WHISPER_WEAK_LOGP", "-0.62"))
# Подсказка для декодера: пунктуация, регистр и типичные для роликов слова.
ASR_PROMPT = os.environ.get("VIRALCUT_WHISPER_PROMPT", (
    "ViralCut, вертикальные клипы, субтитры, подкаст, интервью. "
    "Русская речь с пунктуацией; имена и термины пишутся так же, как в оригинале."
))


class TranscriptionError(Exception):
    pass


def model_cached() -> bool:
    """Модель уже есть локально (внутри .app или в кэше — сеть не нужна)."""
    if WHISPER_LOCAL_DIR and (Path(WHISPER_LOCAL_DIR) / "model.bin").is_file():
        return True
    try:
        pattern = f"models--Systran--faster-whisper-{WHISPER_MODEL}"
        for path in MODELS_DIR.glob(pattern + "/snapshots/*/model.bin"):
            if path.stat().st_size > 1024 * 1024:
                return True
    except Exception:
        pass
    return False


def model_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _reset_model(long_audio: bool = False) -> None:
    global _MODEL, _MODEL_LONG
    with _MODEL_LOCK:
        if long_audio:
            _MODEL_LONG = None
        else:
            _MODEL = None


def _get_model(long_audio: bool = False):
    global _MODEL, _MODEL_LONG
    with _MODEL_LOCK:
        if long_audio and _MODEL_LONG is not None:
            return _MODEL_LONG
        if not long_audio and _MODEL is not None:
            return _MODEL
        from faster_whisper import WhisperModel

        local_dir = Path(WHISPER_LOCAL_DIR) if WHISPER_LOCAL_DIR else None
        model_ref = str(local_dir) if local_dir and (local_dir / "model.bin").is_file() else WHISPER_MODEL
        # Для длинных аудио не используем экспериментальный MPS — на нём
        # ctranslate2 может «зависнуть» на середине многочасового файла.
        if WHISPER_DEVICE == "auto":
            # Apple Metal доступен только на macOS; на Windows/Linux сразу CPU.
            if long_audio or sys.platform != "darwin":
                candidates = [("cpu", WHISPER_COMPUTE)]
            else:
                candidates = [("mps", "float16"), ("cpu", WHISPER_COMPUTE)]
        else:
            candidates = [(WHISPER_DEVICE, WHISPER_COMPUTE)]
        last_exc = None
        model = None
        for device, compute in candidates:
            try:
                from . import resources

                model = WhisperModel(
                    model_ref,
                    device=device,
                    compute_type=compute,
                    download_root=str(MODELS_DIR),
                    cpu_threads=resources.whisper_threads(),
                    num_workers=1,
                )
                model._vc_device = device  # type: ignore[attr-defined]
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                model = None
                print(f"[whisper] {device}/{compute} недоступен: {str(exc)[:200]}",
                      file=sys.stderr, flush=True)
        if model is None:
            raise TranscriptionError(
                "Локальная модель распознавания речи недоступна. "
                f"Причина: {str(last_exc)[:200]}"
            )
        if long_audio:
            _MODEL_LONG = model
        else:
            _MODEL = model
        return model


def _decode(model, audio: Path, language: Optional[str], vad: bool,
            prompt: Optional[str] = None, beam: Optional[int] = None,
            temperature=None, condition: bool = True):
    """Один проход декодирования с настройками под качество."""
    return model.transcribe(
        str(audio),
        language=language or None,
        task="transcribe",
        beam_size=beam or BEAM_SIZE,
        best_of=beam or BEAM_SIZE,
        temperature=[0.0, 0.2, 0.4, 0.6] if temperature is None else temperature,
        length_penalty=1.0,
        repetition_penalty=1.05,
        no_repeat_ngram_size=3,
        condition_on_previous_text=condition,
        prompt_reset_on_temperature=0.5,
        initial_prompt=(prompt or ASR_PROMPT),
        vad_filter=vad,
        vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 200} if vad else None,
        word_timestamps=True,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        language_detection_threshold=0.5,
        language_detection_segments=1,
    )


def _decode_batched(model, audio: Path, language: Optional[str], vad: bool):
    """Пачечное декодирование (faster-whisper BatchedInferencePipeline)."""
    from faster_whisper import BatchedInferencePipeline

    pipe = BatchedInferencePipeline(model=model)
    kwargs = dict(
        language=language or None,
        task="transcribe",
        beam_size=BEAM_SIZE,
        condition_on_previous_text=True,
        initial_prompt=ASR_PROMPT,
        vad_filter=vad,
        word_timestamps=True,
        batch_size=BATCH_SIZE,
    )
    try:
        return pipe.transcribe(str(audio), **kwargs)
    except TypeError:
        # в других версиях части аргументов нет — повторяем с минимумом
        for key in ("condition_on_previous_text", "initial_prompt"):
            kwargs.pop(key, None)
        return pipe.transcribe(str(audio), **kwargs)


def _segment_payload(seg, offset: float) -> Dict[str, Any]:
    text = (seg.text or "").strip()
    words = []
    for word in (seg.words or []):
        token = (word.word or "").strip()
        if not token:
            continue
        words.append({
            "word": token,
            "start": round(float(word.start) + offset, 3),
            "end": round(float(word.end) + offset, 3),
        })
    if not words and text:
        tokens = text.split()
        step = max(0.05, (seg.end - seg.start) / max(1, len(tokens)))
        words = [{
            "word": token,
            "start": round(seg.start + i * step + offset, 3),
            "end": round(seg.start + (i + 1) * step + offset, 3),
        } for i, token in enumerate(tokens)]
    return {
        "start": round(float(seg.start) + offset, 3),
        "end": round(float(seg.end) + offset, 3),
        "text": text,
        "words": words,
        "quality": {
            "avg_logprob": round(float(getattr(seg, "avg_logprob", 0.0) or 0.0), 3),
            "no_speech_prob": round(float(getattr(seg, "no_speech_prob", 0.0) or 0.0), 3),
        },
    }


def _consume(model, path: Path, language: Optional[str], use_vad: bool,
             offset: float, total_duration: float,
             on_progress: ProgressCb, batched: bool = False) -> Tuple[List[Dict[str, Any]], Any]:
    if batched:
        try:
            segments, tinfo = _decode_batched(model, path, language, use_vad)
        except Exception as exc:  # noqa: BLE001 — пачечный режим не должен ломать распознавание
            print(f"[whisper] пачечный режим не сработал: {str(exc)[:160]}", file=sys.stderr, flush=True)
            segments, tinfo = _decode(model, path, language, use_vad)
    else:
        segments, tinfo = _decode(model, path, language, use_vad)
    out: List[Dict[str, Any]] = []
    for seg in segments:
        payload = _segment_payload(seg, offset)
        if not payload["text"]:
            continue
        out.append(payload)
        if on_progress and total_duration:
            on_progress(min(0.99, (offset + float(seg.end)) / total_duration),
                        "Распознавание речи")
    return out, tinfo


def _transcribe_span(model, path: Path, language: Optional[str], offset: float,
                     total_duration: float, on_progress: ProgressCb, batched: bool = False):
    """Пробует с VAD, при ошибке ONNX повторяет без VAD."""
    try:
        return _consume(model, path, language, True, offset, total_duration, on_progress, batched)
    except Exception as exc:  # noqa: BLE001
        low = str(exc).lower()
        if "onnxruntime" not in low and "vad" not in low and "fusedconv" not in low:
            raise
        traceback.print_exc()
        if on_progress:
            on_progress(min(0.99, offset / max(1.0, total_duration)),
                        "VAD недоступен · распознавание без фильтра тишины")
        return _consume(model, path, language, False, offset, total_duration, on_progress, batched)


def _cut_window(src: Path, dst: Path, start: float, end: float) -> bool:
    """Вырезает фрагмент аудио для повторной проверки."""
    cmd = [ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
           "-to", f"{max(start + 0.2, end):.3f}", "-i", str(src),
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    result = utils.run(cmd)
    return result.returncode == 0 and dst.exists() and dst.stat().st_size > 512


def _recheck(model, wav: Path, workdir: Path, segments: List[Dict[str, Any]],
             language: Optional[str], on_progress: ProgressCb, limit: int) -> int:
    """Перепроверяет сегменты с низкой уверенностью; возвращает число правок.

    Окно вокруг сегмента декодируется заново с контекстом соседних реплик.
    Замена принимается, только если модель стала увереннее и текст не «разъехался»
    по длине — так мы чиним отдельные слова, но не портим удачные сегменты.
    """
    if limit <= 0 or not segments:
        return 0
    weak = [s for s in segments
            if s.get("quality", {}).get("avg_logprob", 0.0) < WEAK_LOGPROB]
    if not weak:
        return 0
    weak.sort(key=lambda s: s["quality"]["avg_logprob"])
    fixed = 0
    for index, seg in enumerate(weak[:limit]):
        clip = workdir / f"recheck_{os.urandom(3).hex()}.wav"
        try:
            if not _cut_window(wav, clip, seg["start"] - 0.30, seg["end"] + 0.40):
                continue
            before = [s["text"] for s in segments if s["end"] <= seg["start"]][-2:]
            context = " ".join(before)[-220:]
            prompt = (context + " " + ASR_PROMPT)[-400:]
            try:
                parts, _ = _decode(model, clip, language, False, prompt=prompt,
                                   beam=BEAM_SIZE, temperature=[0.0], condition=False)
                new_text = " ".join(p.text.strip() for p in parts).strip()
                payloads = [_segment_payload(p, max(0.0, seg["start"] - 0.30)) for p in parts]
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                continue
            if not new_text or not payloads:
                continue
            new_logprob = sum(p["quality"]["avg_logprob"] for p in payloads) / len(payloads)
            old_logprob = seg["quality"]["avg_logprob"]
            ratio = len(new_text) / max(1, len(seg["text"]))
            if new_logprob <= old_logprob + 0.04 or not (0.45 <= ratio <= 2.2):
                continue
            seg["text"] = new_text
            seg["words"] = [w for p in payloads for w in p["words"]]
            seg["start"] = payloads[0]["start"]
            seg["end"] = payloads[-1]["end"]
            seg["quality"]["avg_logprob"] = round(new_logprob, 3)
            seg["quality"]["rechecked"] = True
            fixed += 1
            if on_progress:
                # Прогресс-колбэки снаружи ждут float, поэтому не None.
                on_progress(0.996, f"Проверка спорных фрагментов · {index + 1}")
        finally:
            try:
                clip.unlink(missing_ok=True)
            except OSError:
                pass
    return fixed


def _run_with_timeout(fn, timeout: float):
    """Запускает распознавание части в потоке; при зависании отдаёт None."""
    box = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _split_audio(wav: Path, workdir: Path, chunk_seconds: int) -> tuple[List[tuple[Path, float, float]], Path]:
    folder = workdir / f"whisper_chunks_{os.urandom(4).hex()}"
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", str(wav),
           "-f", "segment", "-segment_time", str(chunk_seconds), "-c", "copy",
           str(folder / "chunk_%03d.wav")]
    result = utils.run(cmd)
    files = sorted(folder.glob("chunk_*.wav"))
    if result.returncode or not files:
        shutil.rmtree(folder, ignore_errors=True)
        return [], folder
    spans = []
    offset = 0.0
    for path in files:
        length = utils.media_duration(path)
        if length <= 0:
            continue
        spans.append((path, offset, length))
        offset += length
    return spans, folder


def transcribe(media: Path, workdir: Path, language: Optional[str] = None,
               on_progress: ProgressCb = None) -> Dict[str, Any]:
    started = time.time()
    if on_progress:
        on_progress(0.005, "Подготовка звука для распознавания…")
    try:
        info = utils.probe(media)
    except utils.MediaError:
        info = {"duration": utils.media_duration(media), "has_audio": True,
                "width": 0, "height": 0}
    duration = info["duration"]

    if not info["has_audio"]:
        raise TranscriptionError("В видео нет аудиодорожки — нарезки с субтитрами невозможны.")

    # Чистим звук: убираем низкочастотный гул и шипение, выравниваем громкость.
    wav = utils.extract_asr_audio(media, workdir / "audio16k.wav")
    chunk_seconds = max(120, int(os.environ.get("VIRALCUT_WHISPER_CHUNK", "600")))
    trigger = max(chunk_seconds, int(os.environ.get("VIRALCUT_WHISPER_CHUNK_MIN", "1800")))
    long_audio = duration > trigger
    batched = (duration >= BATCH_MIN_SECONDS
               and os.environ.get("VIRALCUT_WHISPER_BATCH", "1").strip() not in ("0", "false", "no", "off"))
    model = _get_model(long_audio=long_audio)
    spans: List[tuple[Path, float, float]] = [(wav, 0.0, duration)]
    chunks_folder: Optional[Path] = None
    if duration > trigger:
        split, chunks_folder = _split_audio(wav, workdir, chunk_seconds)
        if split:
            spans = split

    out: List[Dict[str, Any]] = []
    detected_language = language
    language_probability = None
    for index, (path, offset, length) in enumerate(spans):
        if not path.exists():
            traceback.print_exc()
            continue
        timeout = max(240.0, length * 40)
        try:
            result = _run_with_timeout(
                lambda: _transcribe_span(model, path, detected_language, offset,
                                         duration, on_progress, batched),
                timeout,
            )
            if result is None:
                # часть зависла (например, в драйвере Metal) — перезапускаем модель и идём дальше
                print(f"whisper chunk {index} timed out; skipping", file=sys.stderr)
                _reset_model(long_audio)
                model = _get_model(long_audio=long_audio)
                if on_progress:
                    on_progress(min(0.99, (offset + length) / max(1.0, duration)),
                                f"Часть {index + 1} зависла · пропускаю")
                continue
            part, tinfo = result
            out.extend(part)
            if not detected_language and getattr(tinfo, "language", None):
                detected_language = tinfo.language
                language_probability = round(float(getattr(tinfo, "language_probability", 0.0) or 0.0), 3)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            continue
        if len(spans) > 1 and on_progress:
            on_progress(min(0.99, (offset + length) / max(1.0, duration)),
                        f"Распознавание речи · часть {index + 1} из {len(spans)}")

    if chunks_folder is not None:
        shutil.rmtree(chunks_folder, ignore_errors=True)
    if not out:
        raise TranscriptionError("Речь не распознана: возможно, в видео нет голоса.")

    # Второй проход по спорным местам — только когда аудио не разрезано на части.
    fixed = 0
    if len(spans) == 1 and RECHECK_LIMIT > 0:
        if on_progress:
            on_progress(0.995, "Проверка спорных фрагментов…")
        try:
            fixed = _recheck(model, wav, workdir, out, detected_language, on_progress, RECHECK_LIMIT)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    out.sort(key=lambda s: s["start"])
    logprobs = [s.get("quality", {}).get("avg_logprob", 0.0) for s in out]
    weak = sum(1 for value in logprobs if value < WEAK_LOGPROB)
    confidence = round(sum(logprobs) / len(logprobs), 3) if logprobs else 0.0

    return {
        "language": (detected_language or language or "ru"),
        "duration": duration,
        "segments": out,
        "engine": f"whisper-{WHISPER_MODEL}",
        "took": round(time.time() - started, 1),
        "quality": {
            "confidence": confidence,
            "weak_segments": weak,
            "rechecked": fixed,
            "language_probability": language_probability,
        },
    }
