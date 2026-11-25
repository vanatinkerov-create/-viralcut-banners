"""SQLite-хранилище проектов и клипов."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import DB_PATH

# Запись и нарезка идут в разных потоках и оба пишут meta: без блокировки
# один поток затирал live_processed/live_clips другого.
_META_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    duration REAL,
    language TEXT,
    source TEXT,
    thumb TEXT,
    meta TEXT,
    options TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    title TEXT,
    hook TEXT,
    caption TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    path TEXT,
    thumb TEXT,
    words TEXT,
    settings TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id, idx);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Точка входа миграций: первое изменение схемы не должно ломать базы
# пользователей. Сейчас схема = версия 1, дальше добавляем шаги списком.
SCHEMA_VERSION = 2


def _migrate(conn: sqlite3.Connection) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    if current >= SCHEMA_VERSION:
        return
    if current < 1:
        pass  # версия 1 — базовая схема
    if current < 2:
        # Раньше ретрай во время отмены мог создать вторую партию клипов с теми
        # же idx. Убираем дубли (оставляем последнюю запись) и запрещаем их схемой.
        conn.execute(
            "DELETE FROM clips WHERE rowid NOT IN ("
            " SELECT MAX(rowid) FROM clips GROUP BY project_id, idx)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_project_idx ON clips(project_id, idx)")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def count_projects() -> int:
    """Дешёвый счётчик для /api/health: без чтения и парсинга meta."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
    return int(row["n"] if row else 0)


def set_stage_if_active(pid: str, stage: str) -> None:
    """Обновляет этап только у проекта, который ещё не в терминальном статусе.

    Фоновый heartbeat не должен «оживлять» завершённый/упавший проект.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET stage=?, updated_at=? WHERE id=?"
            " AND status NOT IN ('done', 'error', 'cancelled')",
            (stage, time.time(), pid),
        )


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def create_project(url: str, options: Dict[str, Any], title: Optional[str] = None) -> Dict[str, Any]:
    pid = uuid.uuid4().hex[:12]
    now = time.time()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, url, title, status, stage, progress, created_at, updated_at, options)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, url, title or url, "queued", "В очереди", 0, now, now, json.dumps(options, ensure_ascii=False)),
        )
    return get_project(pid)


def get_project(pid: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    p = _row_to_dict(row)
    p["options"] = json.loads(p["options"] or "{}")
    p["meta"] = json.loads(p["meta"] or "{}")
    return p


def list_projects(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, url, title, status, stage, progress, created_at, duration, language, thumb, meta"
            " FROM projects ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for row in rows:
        item = _row_to_dict(row)
        item["meta"] = json.loads(item.get("meta") or "{}") if item.get("meta") else {}
        out.append(item)
    return out


def update_project(pid: str, **fields: Any) -> None:
    if not fields:
        return
    for key in ("meta", "options"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE projects SET {cols} WHERE id=?", (*fields.values(), pid))


def merge_meta(pid: str, updates: Dict[str, Any], drop: tuple = ()) -> None:
    """Атомарно добавляет ключи в meta, не затирая чужие обновления."""
    with _META_LOCK:
        with connect() as conn:
            row = conn.execute("SELECT meta FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                return
            meta = json.loads(row["meta"] or "{}")
            for key in drop:
                meta.pop(key, None)
            meta.update(updates)
            conn.execute("UPDATE projects SET meta=?, updated_at=? WHERE id=?",
                         (json.dumps(meta, ensure_ascii=False), time.time(), pid))


def set_recording_stage(pid: str, stage: str) -> None:
    """Этап записи не должен перебивать распознавание/рендер и «Останавливаю…»."""
    sql = (
        "UPDATE projects SET stage=?, updated_at=? WHERE id=? AND (stage IS NULL OR ("
        "stage NOT LIKE 'Распознавание%' AND stage NOT LIKE 'Рендер%'"
        " AND stage NOT LIKE 'Окно%' AND stage NOT LIKE 'Ошибка окна%'"
        " AND stage NOT LIKE 'Останавливаю%'))"
    )
    with connect() as conn:
        conn.execute(sql, (stage, time.time(), pid))


def delete_project(pid: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))


def add_clip(project_id: str, idx: int, start: float, end: float, score: float,
             title: str, hook: str, caption: str, words: List[Dict[str, Any]],
             settings: Dict[str, Any], status: str = "rendering") -> Dict[str, Any]:
    cid = uuid.uuid4().hex[:12]
    with connect() as conn:
        # повторный прогон того же проекта заменяет клип с этим номером
        conn.execute("DELETE FROM clips WHERE project_id=? AND idx=?", (project_id, idx))
        conn.execute(
            "INSERT INTO clips (id, project_id, idx, start, end, score, title, hook, caption, status, words, settings, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, project_id, idx, start, end, score, title, hook, caption, status,
             json.dumps(words, ensure_ascii=False), json.dumps(settings, ensure_ascii=False),
             time.time(), time.time()),
        )
    return get_clip(cid)


def get_clip(cid: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id=?", (cid,)).fetchone()
    if not row:
        return None
    c = _row_to_dict(row)
    c["words"] = json.loads(c["words"] or "[]")
    c["settings"] = json.loads(c["settings"] or "{}")
    return c


def update_clip(cid: str, **fields: Any) -> None:
    if not fields:
        return
    for key in ("words", "settings"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE clips SET {cols} WHERE id=?", (*fields.values(), cid))


def list_clips(project_id: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clips WHERE project_id=? ORDER BY idx", (project_id,)).fetchall()
    clips = []
    for row in rows:
        c = _row_to_dict(row)
        c["words"] = json.loads(c["words"] or "[]")
        c["settings"] = json.loads(c["settings"] or "{}")
        clips.append(c)
    return clips


def clear_clips(project_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE project_id=?", (project_id,))


def reset_clips_pending(project_id: str) -> int:
    """Возвращает готовые клипы в очередь — при пересборке в HD."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE clips SET status='pending', error=NULL, updated_at=? WHERE project_id=?",
            (time.time(), project_id))
        return cur.rowcount


def reset_stale_jobs() -> None:
    """Помечает задачи, прерванные остановкой сервера."""
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET status='done', stage='Запись стрима прервана перезапуском приложения'"
            " WHERE status IN ('queued','running','preparing') AND meta LIKE '%live%true%'"
        )
        conn.execute(
            "UPDATE projects SET status='error', error='Обработка прервана перезапуском сервера',"
            " stage='Ошибка' WHERE status IN ('queued','running','preparing','processing')"
        )
