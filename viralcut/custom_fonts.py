"""Пользовательские шрифты: загрузка, метрики и папка для libass."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, FONTS_DIR

CUSTOM_DIR = DATA_DIR / "fonts"
RUNTIME_DIR = DATA_DIR / "fonts_runtime"
META_PATH = CUSTOM_DIR / "fonts.json"
MAX_BYTES = 30 * 1024 * 1024

LOWER = "аеоинтсврлкмдупя"


def _meta() -> List[Dict[str, Any]]:
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_meta(items: List[Dict[str, Any]]) -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = META_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(META_PATH)


def list_custom() -> List[Dict[str, Any]]:
    return [f for f in _meta() if (CUSTOM_DIR / f.get("file", "")).exists()]


def get(key: str) -> Optional[Dict[str, Any]]:
    for font in list_custom():
        if font.get("key") == key:
            return font
    return None


def payload() -> List[Dict[str, Any]]:
    return [{"key": f["key"], "label": f["label"], "family": f.get("family"),
             "css": f.get("css") or f"VCCustom_{f['key']}", "custom": True,
             "has_cyrillic": bool(f.get("has_cyrillic", True))} for f in list_custom()]


def sync() -> Path:
    """Собирает папку со всеми шрифтами (симлинки) для libass."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    expected: Dict[str, Path] = {}
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        expected[path.name] = path
    for font in list_custom():
        expected[font["file"]] = CUSTOM_DIR / font["file"]
    for existing in RUNTIME_DIR.iterdir():
        if existing.name not in expected:
            try:
                existing.unlink()
            except OSError:
                pass
    for name, src in expected.items():
        link = RUNTIME_DIR / name
        try:
            if link.is_symlink() and Path(os.readlink(link)) == src:
                continue
            if link.exists() or link.is_symlink():
                link.unlink()
        except OSError:
            pass
        try:
            link.symlink_to(src)
        except OSError:
            shutil.copy2(src, link)
    return RUNTIME_DIR


def libass_dir() -> Path:
    return sync()


_FAMILY_SAFE = re.compile(r"[^A-Za-z0-9 _-]+")


def _clean_family(name: str | None) -> str:
    """Имя семейства из файла шрифта идёт в CSS, ASS и HTML — только белый список.

    Шрифт может объявить `family` вида `X"; } body{...}` или `<img onerror=...>`,
    и это попало бы в @font-face, ASS-стиль и подпись в интерфейсе.
    """
    cleaned = _FAMILY_SAFE.sub(" ", str(name or "")).strip(" -_")
    return (cleaned[:48] or "Custom Font")


def _metrics(data: bytes) -> Dict[str, Any]:
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data))
    upem = int(font["head"].unitsPerEm) or 1000
    family = None
    try:
        record = font["name"].getDebugName(16) or font["name"].getDebugName(1)
        family = str(record).strip() if record else None
    except Exception:
        family = None
    cap = 0.0
    try:
        cap = float(getattr(font["OS/2"], "sCapHeight", 0) or 0) / upem
    except Exception:
        cap = 0.0
    if cap <= 0:
        try:
            glyph = font["glyf"]["H"]
            cap = float(glyph.yMax - glyph.yMin) / upem
        except Exception:
            cap = 0.7
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    widths = [hmtx[cmap[ord(ch)]][0] for ch in LOWER if ord(ch) in cmap]
    raw = (sum(widths) / len(widths) / upem) if widths else 0.6
    cyrillic = all(ord(ch) in cmap for ch in "АЯая")
    return {
        "family": _clean_family(family),
        "cap": round(max(0.3, min(1.4, cap)), 3),
        "avg": round(max(0.3, min(2.4, raw * 1.75)), 3),
        "has_cyrillic": bool(cyrillic),
    }


def _suffix(filename: str, data: bytes) -> str:
    head = data[:4]
    if head in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        return ".otf" if head == b"OTTO" else ".ttf"
    suffix = Path(filename or "").suffix.lower()
    if suffix in (".ttf", ".otf"):
        return suffix
    raise ValueError("Поддерживаются только файлы TTF и OTF")


def save(filename: str, data: bytes) -> Dict[str, Any]:
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > MAX_BYTES:
        raise ValueError("Файл больше 30 МБ — выберите шрифт поменьше")
    suffix = _suffix(filename, data)
    digest = hashlib.sha1(data).hexdigest()[:10]
    key = f"custom_{digest}"
    existing = get(key)
    if existing:
        return existing
    info = _metrics(data)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{key}{suffix}"
    (CUSTOM_DIR / stored).write_bytes(data)
    items = _meta()
    family = info["family"]
    labels = {f.get("label") for f in items}
    label = family
    n = 2
    while label in labels:
        label = f"{family} ({n})"
        n += 1
    entry = {
        "key": key, "label": label, "family": info["family"], "file": stored,
        "cap": info["cap"], "avg": info["avg"], "has_cyrillic": info["has_cyrillic"],
        "original": Path(filename or "").name, "css": f"VCCustom_{key}", "custom": True,
    }
    items.append(entry)
    _write_meta(items)
    sync()
    return entry


def delete(key: str) -> bool:
    items = _meta()
    kept = []
    removed = None
    for font in items:
        if font.get("key") == key:
            removed = font
        else:
            kept.append(font)
    if not removed:
        return False
    try:
        (CUSTOM_DIR / removed.get("file", "")).unlink(missing_ok=True)
    except OSError:
        pass
    _write_meta(kept)
    sync()
    return True
