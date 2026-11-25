import os
import shutil
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict
from . import banners as banner_lib, db, render
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="banners")
_lock = threading.Lock()
_busy = set()

def submit_banner_apply(project_id):
    from . import jobs
    if jobs.is_running(project_id):
        return 0
    accepted = 0
    with _lock:
        for clip in db.list_clips(project_id):
            cid = clip['id']
            if cid in _busy or clip['status'] != 'ready' or not clip.get('path'):
                continue
            _busy.add(cid)
            db.update_clip(cid, status='rendering', error=None)
            _pool.submit(_work, cid)
            accepted += 1
    return accepted

def _work(cid):
    try:
        apply_banners(cid)
    except Exception as exc:
        db.update_clip(cid, status='ready', error=str(exc)[:500])
    finally:
        with _lock:
            _busy.discard(cid)

def apply_banners(clip_id: str) -> Dict[str, Any]:
    """Накладывает активные баннеры библиотеки на готовый клип (кнопка «Применить»).

    Работает от чистой копии клипа, поэтому повторное нажатие не накладывает
    рекламу дважды, а выключенный баннер убирается из клипа.
    """
    clip = db.get_clip(clip_id)
    if not clip or not clip.get("path"):
        raise RuntimeError("Клип ещё не отрендерен")
    project = db.get_project(clip["project_id"])
    if not project:
        raise RuntimeError("Проект не найден")
    out_path = Path(clip["path"])
    if not out_path.is_file():
        raise RuntimeError("Файл клипа не найден")

    index = int(clip.get("idx", 0)) + 1
    chosen = banner_lib.banners_for_clip(project["id"], index)
    base = banner_lib.ensure_base(out_path)
    applied = bool(chosen) and banner_lib.apply_to_clip(base, out_path, chosen)
    if not applied:
        chosen = []
        same = False
        try:
            same = os.path.samefile(base, out_path)
        except OSError:
            same = False
        if not same:
            staged = out_path.with_name(f".clean-{out_path.name}")
            shutil.copy2(base, staged)
            os.replace(staged, out_path)

    workdir = out_path.parent.parent
    thumb_path = workdir / "thumbs" / f"{clip['id']}.jpg"
    preview_path = workdir / "previews" / f"{clip['id']}.mp4"
    thumb = None
    try:
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        made = render.make_thumb(out_path, thumb_path.with_name(f".t-{thumb_path.name}"), at=0.6)
        if made:
            os.replace(made, thumb_path)
            thumb = str(thumb_path)
    except (OSError, RuntimeError) as exc:
        print(f"[banners] не удалось обновить превью: {exc}", file=sys.stderr, flush=True)
    try:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        made = render.make_preview(out_path, preview_path.with_name(f".p-{preview_path.name}"))
        if made:
            os.replace(made, preview_path)
    except (OSError, RuntimeError) as exc:
        print(f"[banners] не удалось обновить превью-ролик: {exc}", file=sys.stderr, flush=True)

    settings = dict(clip.get("settings") or {})
    settings["banners"] = [b["id"] for b in chosen]
    db.update_clip(clip_id, status="ready", thumb=thumb or clip.get("thumb"),
                   settings=settings, error=None)
    return db.get_clip(clip_id) or clip


