"""Пользовательские настройки приложения (data/settings.json).

Храним только то, что человек выбирает сам:
  * encoder — "auto" | "gpu" | "cpu": чем кодировать клипы;
  * download_dir — куда складывать скачанные исходники;
  * clips_dir — куда сохранять готовые клипы;
  * ask_folder — спрашивать папку при каждом сохранении.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

from .config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULTS: Dict[str, Any] = {
    "encoder": "auto",          # auto = GPU, если железо умеет; cpu = всегда libx264
    "download_dir": "",
    "clips_dir": "",
    "ask_folder": True,
}
_LOCK = threading.Lock()


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**DEFAULTS, **data}
    except (OSError, ValueError):
        pass
    return dict(DEFAULTS)


def _save(data: Dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get() -> Dict[str, Any]:
    with _LOCK:
        data = _load()
    # пустые папки не отдаём как валидные пути
    for key in ("download_dir", "clips_dir"):
        value = str(data.get(key) or "")
        data[key] = value if value and Path(value).is_dir() else ""
    return data


def encoder_mode() -> str:
    """auto | gpu | cpu — с учётом переменной окружения как оверрайда."""
    env = os.environ.get("VIRALCUT_HW_ENCODE", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return "cpu"
    if env in ("1", "true", "yes", "on"):
        return "gpu"
    mode = str(get().get("encoder") or "auto")
    return mode if mode in ("auto", "gpu", "cpu") else "auto"


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        data = _load()
        if "encoder" in patch and patch["encoder"] in ("auto", "gpu", "cpu"):
            data["encoder"] = patch["encoder"]
        for key in ("download_dir", "clips_dir"):
            if key in patch:
                value = str(patch[key] or "").strip()
                data[key] = value
        if "ask_folder" in patch:
            data["ask_folder"] = bool(patch["ask_folder"])
        _save(data)
    return get()
