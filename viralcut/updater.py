"""Проверка обновлений: один запрос к GitHub Releases раз в несколько часов.

Почему так, а не «при каждом запуске»: приложение локальное, API GitHub без
токена даёт 60 запросов в час на IP, а пользователю важно видеть обновление,
а не создавать нагрузку. Результат кешируется в `data/update.json`, поэтому
офлайн-запуск ничего не ждёт и не падает.

Что уходит наружу: только `GET /releases/latest` с User-Agent `ViralCut/<версия>`.
Ни видео, ни пути, ни настройки не отправляются. Отключается переменной
`VIRALCUT_UPDATE_CHECK=0` или через `POST /api/update/settings`.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ._version import __version__
from .config import DATA_DIR

STATE_FILE = DATA_DIR / "update.json"
DEFAULT_API = "https://api.github.com/repos/vanwild11/viralcut/releases/latest"
RELEASES_PAGE = "https://github.com/vanwild11/viralcut/releases/latest"
# Проверяем не чаще, чем раз в 6 часов; форс-запрос — по кнопке.
TTL_SECONDS = float(os.environ.get("VIRALCUT_UPDATE_TTL", str(6 * 3600)))
TIMEOUT_SECONDS = 5.0

_CHECKING = threading.Lock()

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


def _env_enabled() -> bool:
    return os.environ.get("VIRALCUT_UPDATE_CHECK", "1").strip().lower() not in (
        "0", "false", "no", "off")


def parse_version(value: Any) -> tuple:
    """`v1.2.3` → (1, 2, 3, 1); пререлиз сравнивается меньше стабильной версии."""
    text = str(value or "").strip().lstrip("vV")
    match = _VERSION_RE.match(text)
    if not match:
        return ()
    major, minor, patch, suffix = match.groups()
    return (int(major), int(minor), int(patch), 0 if suffix else 1, suffix or "")


def is_newer(candidate: Any, current: Any = None) -> bool:
    """Строго новее ли версия (`1.2.10` > `1.2.9`, стабильная > её пререлиза)."""
    latest = parse_version(candidate)
    current_parsed = parse_version(current or __version__)
    if not latest or not current_parsed:
        return False
    return latest > current_parsed


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def enabled() -> bool:
    state = _load()
    if isinstance(state.get("enabled"), bool):
        return bool(state["enabled"]) and _env_enabled()
    return _env_enabled()


def set_enabled(flag: bool) -> Dict[str, Any]:
    state = _load()
    state["enabled"] = bool(flag)
    _save(state)
    return payload()


def _first_line(text: str, limit: int = 120) -> str:
    for line in str(text or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def payload() -> Dict[str, Any]:
    """Текущее состояние для интерфейса: без сети и без ожидания."""
    state = _load()
    latest = state.get("latest") or None
    available = bool(latest) and is_newer(latest) and enabled()
    return {
        "current": __version__,
        "enabled": enabled(),
        "available": available,
        "latest": latest,
        "url": state.get("url") or RELEASES_PAGE,
        "notes": state.get("notes") or "",
        "checked_at": state.get("checked_at"),
        "error": state.get("error"),
    }


def _fetch(url: str) -> Optional[Dict[str, Any]]:
    request = urllib.request.Request(url, headers={
        "User-Agent": f"ViralCut/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def check(force: bool = False) -> Dict[str, Any]:
    """Проверяет релизы (с учётом кеша) и возвращает свежий payload."""
    state = _load()
    if not enabled():
        return payload()
    now = time.time()
    checked_at = float(state.get("checked_at") or 0)
    if not force and checked_at and (now - checked_at) < TTL_SECONDS:
        return payload()

    with _CHECKING:
        # Пока ждали блокировку, другой поток мог уже обновить кеш.
        state = _load()
        checked_at = float(state.get("checked_at") or 0)
        if not force and checked_at and (time.time() - checked_at) < TTL_SECONDS:
            return payload()
        try:
            release = _fetch(os.environ.get("VIRALCUT_UPDATE_URL") or DEFAULT_API) or {}
            tag = str(release.get("tag_name") or "").strip()
            prerelease = bool(release.get("prerelease"))
            draft = bool(release.get("draft"))
            parsed_current = parse_version(__version__)
            current_is_pre = bool(parsed_current) and parsed_current[3] == 0
            state.update({
                "checked_at": time.time(),
                "latest": None if (draft or (prerelease and not current_is_pre)) else tag or None,
                "url": release.get("html_url") or RELEASES_PAGE,
                "notes": str(release.get("body") or "")[:1200],
                "error": None,
            })
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
            # Офлайн и ошибки сети не должны всплывать в интерфейсе: просто
            # запоминаем время попытки, чтобы не долбить API каждые 20 секунд.
            state.update({"checked_at": time.time(), "error": str(exc)[:200]})
        _save(state)
    return payload()


def maybe_check_async() -> None:
    """Запускает проверку в фоне, если кеш устарел. Не блокирует запрос."""
    if not enabled():
        return
    checked_at = float(_load().get("checked_at") or 0)
    if checked_at and (time.time() - checked_at) < TTL_SECONDS:
        return
    if _CHECKING.locked():
        return

    def run() -> None:
        try:
            check()
        except Exception:  # noqa: BLE001 — фоновая задача не должна ронять сервер
            pass

    thread = threading.Thread(target=run, name="update-check", daemon=True)
    thread.start()
