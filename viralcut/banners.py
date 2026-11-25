"""Общая библиотека баннеров и наложение их на готовые клипы.

На время показа баннера кадр основного видео замирает, звук основного видео
глушится, баннер накладывается поверх всего кадра, а его звук (если задан)
играет поверх тишины. Затем основное видео продолжается с той же точки.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import resources
from .config import DATA_DIR, ffmpeg
from .utils import MediaError, probe, run

BANNERS_DIR = DATA_DIR / "banners"
FILES_DIR = BANNERS_DIR / "files"
INDEX_PATH = BANNERS_DIR / "library.json"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
POSITIONS = {"top-left", "top", "top-right", "left", "center", "right",
             "bottom-left", "bottom", "bottom-right"}
SAMPLE_RATE = 48000
MARGIN = 0.03
MAX_PER_CLIP = 12

_lock = threading.RLock()


# ---------------------------------------------------------------- библиотека

def _read() -> List[Dict[str, Any]]:
    if not INDEX_PATH.is_file():
        return []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write(items: List[Dict[str, Any]]) -> None:
    BANNERS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def list_banners() -> List[Dict[str, Any]]:
    with _lock:
        return _read()


def get_banner(bid: str) -> Optional[Dict[str, Any]]:
    return next((b for b in list_banners() if b.get("id") == bid), None)


def file_path(name: str) -> Path:
    """Путь к файлу баннера: имя хранится без каталогов."""
    return FILES_DIR / Path(str(name)).name


def media_kind(name: str) -> str:
    suffix = Path(str(name)).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "gif" if suffix == ".gif" else "image"


def clean_conditions(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит условия размещения к безопасному виду."""
    out: Dict[str, Any] = {}
    if fields.get("notes") is not None:
        out["notes"] = str(fields["notes"])[:10000]
    if fields.get("name") is not None:
        out["name"] = str(fields["name"]).strip()[:120] or "Баннер"
    if fields.get("at") is not None:
        out["at"] = round(max(0.0, min(3600.0, float(fields["at"]))), 2)
    if fields.get("duration") is not None:
        out["duration"] = round(max(0.5, min(60.0, float(fields["duration"]))), 2)
    if fields.get("position") is not None:
        if fields["position"] not in POSITIONS:
            raise ValueError("Неизвестная позиция баннера")
        out["position"] = fields["position"]
    if fields.get("size") is not None:
        out["size"] = round(max(0.05, min(1.0, float(fields["size"]))), 3)
    if fields.get("opacity") is not None:
        out["opacity"] = round(max(0.05, min(1.0, float(fields["opacity"]))), 3)
    if fields.get("projects") is not None:
        out["projects"] = [str(p) for p in fields["projects"]][:50]
    if fields.get("clip_indexes") is not None:
        out["clip_indexes"] = sorted({int(i) for i in fields["clip_indexes"] if int(i) > 0})[:200]
    if fields.get("enabled") is not None:
        out["enabled"] = bool(fields["enabled"])
    return out


def _suffix_ok(name: str, allowed: set) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix not in allowed:
        raise ValueError(f"Неподдерживаемый формат файла: {suffix or 'без расширения'}")
    return suffix


def add_banner(image_name: str, image_bytes: bytes,
               audio_name: Optional[str], audio_bytes: Optional[bytes],
               fields: Dict[str, Any]) -> Dict[str, Any]:
    image_suffix = _suffix_ok(image_name, MEDIA_SUFFIXES)
    if not image_bytes:
        raise ValueError("Файл баннера пустой")
    bid = uuid.uuid4().hex[:12]
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    image_file = f"{bid}{image_suffix}"
    file_path(image_file).write_bytes(image_bytes)
    audio_file = None
    if audio_bytes:
        audio_suffix = _suffix_ok(audio_name or "", AUDIO_SUFFIXES)
        audio_file = f"{bid}_audio{audio_suffix}"
        file_path(audio_file).write_bytes(audio_bytes)
    kind = media_kind(image_file)
    default_hold = 3.0
    if kind == "video":
        try:
            default_hold = float(probe(file_path(image_file)).get("duration") or 3.0)
        except Exception:  # noqa: BLE001
            file_path(image_file).unlink(missing_ok=True)
            raise ValueError("Не удалось прочитать видео баннера")
    cond = clean_conditions({
        "name": Path(image_name).stem or "Баннер",
        "at": 1.0, "duration": default_hold, "position": "center", "size": 1.0, "opacity": 1.0,
        "projects": [], "clip_indexes": [], "enabled": True,
        **{k: v for k, v in fields.items() if v is not None and v != ""},
    })
    banner = {"id": bid, "image": image_file, "audio": audio_file, "kind": kind,
              "created_at": time.time(), **cond}
    with _lock:
        items = _read()
        items.append(banner)
        _write(items)
    return banner


def update_banner(bid: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        items = _read()
        for banner in items:
            if banner.get("id") == bid:
                banner.update(clean_conditions(fields))
                _write(items)
                return banner
    return None


def delete_banner(bid: str) -> bool:
    with _lock:
        items = _read()
        target = next((b for b in items if b.get("id") == bid), None)
        if target is None:
            return False
        _write([b for b in items if b.get("id") != bid])
    for key in ("image", "audio"):
        if target.get(key):
            file_path(target[key]).unlink(missing_ok=True)
    return True


def banners_for_clip(project_id: str, clip_index: int) -> List[Dict[str, Any]]:
    """Активные баннеры, подходящие под условия проекта и номера клипа."""
    selected = []
    for banner in list_banners():
        if not banner.get("enabled", True):
            continue
        if banner.get("projects") and project_id not in banner["projects"]:
            continue
        if banner.get("clip_indexes") and int(clip_index) not in banner["clip_indexes"]:
            continue
        if not file_path(banner.get("image", "")).is_file():
            continue
        selected.append(banner)
    return sorted(selected, key=lambda b: float(b.get("at", 0.0)))


# ------------------------------------------------------------------- рендер

def _pause_plan(banners: List[Dict[str, Any]], length: float
                ) -> List[Tuple[str, float, float, Optional[Dict[str, Any]]]]:
    """Сегменты: ('main', from, to) и ('freeze', at, at, banner) в шкале клипа."""
    plan: List[Tuple[str, float, float, Optional[Dict[str, Any]]]] = []
    cursor = 0.0
    for banner in sorted(banners, key=lambda b: float(b.get("at", 0.0))):
        at = max(cursor, min(float(banner.get("at", 0.0)), max(0.0, length - 0.05)))
        if at > cursor + 0.01:
            plan.append(("main", cursor, at, None))
            cursor = at
        plan.append(("freeze", at, at, banner))
    if cursor < length:
        plan.append(("main", cursor, length, None))
    return plan


def _position(name: str, mx: int, my: int) -> Tuple[str, str]:
    x = {"left": f"{mx}", "top-left": f"{mx}", "bottom-left": f"{mx}",
         "right": f"W-w-{mx}", "top-right": f"W-w-{mx}", "bottom-right": f"W-w-{mx}"}.get(name, "(W-w)/2")
    y = {"top": f"{my}", "top-left": f"{my}", "top-right": f"{my}",
         "bottom": f"H-h-{my}", "bottom-left": f"H-h-{my}", "bottom-right": f"H-h-{my}"}.get(name, "(H-h)/2")
    return x, y


def _build_graph(length: float, width: int, height: int, source_video: str,
                 source_audio: str, banners: List[Dict[str, Any]],
                 image_inputs: Dict[str, int], audio_inputs: Dict[str, int]) -> str:
    parts: List[str] = []
    v_labels: List[str] = []
    a_labels: List[str] = []
    overlays: List[Tuple[Dict[str, Any], float]] = []
    clock = 0.0

    for kind, start, end, banner in _pause_plan(banners, length):
        if kind == "main":
            v_label = f"vm{len(v_labels)}"
            a_label = f"am{len(a_labels)}"
            parts.append(
                f"{source_video}trim=start={start:.3f}:end={max(start + 0.02, end):.3f},"
                f"setpts=PTS-STARTPTS[{v_label}]")
            parts.append(
                f"{source_audio}atrim=start={start:.3f}:end={max(start + 0.02, end):.3f},"
                f"asetpts=PTS-STARTPTS,aresample={SAMPLE_RATE},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[{a_label}]")
            v_labels.append(v_label)
            a_labels.append(a_label)
            clock += max(0.02, end - start)
            continue

        hold = float(banner.get("duration", 3.0))
        frame_at = max(0.0, min(start, max(0.0, length - 0.05)))
        v_label = f"vf{len(v_labels)}"
        parts.append(
            f"{source_video}trim=start={frame_at:.3f},trim=end_frame=1,"
            f"loop=loop=-1:size=1:start=0,setpts=N/(30*TB),"
            f"trim=duration={hold:.3f},setpts=PTS-STARTPTS,"
            f"fps=30[{v_label}]")
        v_labels.append(v_label)
        a_label = f"af{len(a_labels)}"
        parts.append(f"aevalsrc=0:d={hold:.3f}:s={SAMPLE_RATE},"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo[{a_label}]")
        a_labels.append(a_label)
        overlays.append((banner, clock))
        clock += hold

    parts.append(f"{''.join(f'[{v}]' for v in v_labels)}"
                 f"concat=n={len(v_labels)}:v=1:a=0[vcat]")
    parts.append(f"{''.join(f'[{a}]' for a in a_labels)}"
                 f"concat=n={len(a_labels)}:v=0:a=1[acat]")

    video_chain = "[vcat]"
    audio_chain = "[acat]"
    margin_x = int(round(width * MARGIN))
    margin_y = int(round(height * MARGIN))

    for index, (banner, out_at) in enumerate(overlays):
        hold = float(banner.get("duration", 3.0))
        bw = max(16, int(round(width * float(banner.get("size", 1.0)))) // 2 * 2)
        opacity = float(banner.get("opacity", 1.0))
        parts.append(f"[{image_inputs[banner['id']]}:v]fps=30,trim=duration={hold:.3f},"
                     f"setpts=PTS-STARTPTS+{out_at:.3f}/TB,format=rgba,"
                     f"scale=w={bw}:h={height}:force_original_aspect_ratio=decrease:"
                     f"force_divisible_by=2:flags=lanczos,"
                     f"colorchannelmixer=aa={opacity:.3f}[bn{index}]")
        x, y = _position(str(banner.get("position", "center")), margin_x, margin_y)
        # баннер поверх всего кадра, включая субтитры: он накладывается последним слоем
        parts.append(f"{video_chain}[bn{index}]overlay={x}:{y}:"
                     f"enable='between(t,{out_at:.3f},{out_at + hold - 0.001:.3f})'"
                     f":eof_action=pass:format=auto[ov{index}]")
        video_chain = f"[ov{index}]"

        if banner["id"] in audio_inputs:
            delay = int(round(out_at * 1000))
            parts.append(
                f"[{audio_inputs[banner['id']]}:a]atrim=0:{hold:.3f},asetpts=PTS-STARTPTS,"
                f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume=1.0,adelay={delay}|{delay},apad=whole_dur={max(0.0, length):.3f}"
                f"[ba{index}]")
            parts.append(f"{audio_chain}[ba{index}]amix=inputs=2:duration=first:"
                         f"dropout_transition=0:normalize=0[ma{index}]")
            audio_chain = f"[ma{index}]"

    parts.append(f"{video_chain}format=yuv420p[vout]")
    parts.append(f"{audio_chain}anull[aout]")
    return ";".join(parts)


def total_length(length: float, banners: List[Dict[str, Any]]) -> float:
    holds = sum(float(b.get("duration", 3.0)) for b in banners if float(b.get("at", 0.0)) <= length)
    return length + holds


def apply_to_clip(source: Path, target: Path,
                  banners: List[Dict[str, Any]]) -> bool:
    """Накладывает баннеры на клип `source`, результат пишет в `target`."""
    if not banners:
        return False
    info = probe(source)
    length = float(info["duration"] or 0.0)
    if length <= 0.2:
        return False
    width, height = int(info["width"]), int(info["height"])
    if width <= 0 or height <= 0:
        raise MediaError("Не удалось определить размер клипа")

    chosen = [b for b in banners if float(b.get("at", 0.0)) < length][:MAX_PER_CLIP]
    if not chosen:
        return False

    threads = str(resources.ffmpeg_threads())
    cmd = resources.wrap_nice([ffmpeg(), "-y", "-hide_banner", "-v", "error", "-nostats",
                               "-threads", threads, "-filter_threads", threads,
                               "-i", str(source)])
    next_index = 1
    source_video = "[0:v]"
    source_audio = "[0:a]"
    if not info.get("has_audio"):
        cmd += ["-f", "lavfi", "-t", f"{length + 1.0:.3f}",
                "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
        source_audio = f"[{next_index}:a]"
        next_index += 1

    image_inputs: Dict[str, int] = {}
    for banner in chosen:
        image = file_path(banner["image"])
        if not image.is_file():
            continue
        kind = media_kind(image.name)
        if kind == "gif":
            cmd += ["-ignore_loop", "0"]
        elif kind == "video":
            cmd += ["-stream_loop", "-1"]
        else:
            cmd += ["-loop", "1", "-framerate", "30"]
        cmd += ["-i", str(image)]
        image_inputs[banner["id"]] = next_index
        next_index += 1

    chosen = [b for b in chosen if b["id"] in image_inputs]
    if not chosen:
        return False

    audio_inputs: Dict[str, int] = {}
    for banner in chosen:
        if banner.get("audio") and file_path(banner["audio"]).is_file():
            cmd += ["-i", str(file_path(banner["audio"]))]
            audio_inputs[banner["id"]] = next_index
            next_index += 1
        elif media_kind(banner["image"]) == "video":
            try:
                if probe(file_path(banner["image"])).get("has_audio"):
                    audio_inputs[banner["id"]] = image_inputs[banner["id"]]
            except Exception:  # noqa: BLE001
                pass

    graph = _build_graph(length, width, height, source_video, source_audio,
                         chosen, image_inputs, audio_inputs)
    total = total_length(length, chosen)

    # Пишем во временный файл и подменяем имя: target может быть жёсткой ссылкой
    # на чистую копию, и запись «на месте» испортила бы исходник.
    tmp = target.with_name(f".bn-{target.name}")
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}",
            "-threads", threads, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-r", "30",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
            str(tmp)]

    proc = run(cmd, timeout=3600)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 10240:
        tmp.unlink(missing_ok=True)
        raise MediaError("Не удалось наложить баннеры: "
                         + (proc.stderr or "").strip()[-600:])
    os.replace(tmp, target)
    return True


def ensure_base(clip_path: Path) -> Path:
    """Чистая копия клипа без баннеров (жёсткая ссылка, иначе копия файла).

    Рендер заменяет файл клипа целиком (os.replace), поэтому ссылка остаётся
    указывать на прежнее содержимое и служит исходником для повторного
    наложения баннеров.
    """
    base_dir = clip_path.parent / ".base"
    base_dir.mkdir(parents=True, exist_ok=True)
    base = base_dir / clip_path.name
    if base.exists():
        return base
    import shutil as _shutil
    _shutil.copy2(clip_path, base)
    return base
