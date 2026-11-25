from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import banners as banner_lib, db
from . import banner_tasks as jobs
app = APIRouter()
class BannerIn(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    at: Optional[float] = None
    duration: Optional[float] = None
    position: Optional[str] = None
    size: Optional[float] = None
    opacity: Optional[float] = None
    projects: Optional[List[str]] = None
    clip_indexes: Optional[List[int]] = None
    enabled: Optional[bool] = None


MAX_BANNER_BYTES = 32 * 1024 * 1024


def _banner_payload(banner: Dict[str, Any]) -> Dict[str, Any]:
    """Баннер для интерфейса: условия + ссылка на картинку."""
    out = dict(banner)
    out["image_url"] = f"/api/banners/{banner['id']}/file"
    out["audio_url"] = (f"/api/banners/{banner['id']}/audio"
                        if banner.get("audio") else None)
    return out


@app.get("/api/banners")
def banners_list() -> Dict[str, Any]:
    return {"banners": [_banner_payload(b) for b in banner_lib.list_banners()],
            "positions": sorted(banner_lib.POSITIONS)}


@app.post("/api/banners")
async def banners_add(file: UploadFile = File(...),
                      audio: Optional[UploadFile] = File(None),
                      conditions: Optional[str] = Form(None)) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    if conditions:
        try:
            import json as _json

            loaded = _json.loads(conditions)
            if isinstance(loaded, dict):
                fields = loaded
        except ValueError:
            raise HTTPException(400, "Не удалось разобрать условия размещения")
    payload = await file.read()
    if len(payload) > MAX_BANNER_BYTES:
        raise HTTPException(413, "Файл баннера больше 32 МБ")
    audio_bytes = None
    audio_name = None
    if audio is not None and audio.filename:
        audio_bytes = await audio.read()
        audio_name = audio.filename
        if len(audio_bytes) > MAX_BANNER_BYTES:
            raise HTTPException(413, "Аудио баннера больше 32 МБ")
    try:
        banner = banner_lib.add_banner(file.filename or "banner.png", payload,
                                       audio_name, audio_bytes, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"banner": _banner_payload(banner)}


@app.post("/api/banners/{bid}")
def banners_update(bid: str, body: BannerIn) -> Dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        banner = banner_lib.update_banner(bid, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not banner:
        raise HTTPException(404, "Баннер не найден")
    return {"banner": _banner_payload(banner)}


@app.delete("/api/banners/{bid}")
def banners_delete(bid: str) -> Dict[str, Any]:
    if not banner_lib.delete_banner(bid):
        raise HTTPException(404, "Баннер не найден")
    return {"ok": True}


@app.get("/api/banners/{bid}/file")
def banners_file(bid: str) -> FileResponse:
    banner = banner_lib.get_banner(bid)
    if not banner:
        raise HTTPException(404, "Баннер не найден")
    path = banner_lib.file_path(banner["image"])
    if not path.is_file():
        raise HTTPException(404, "Файл баннера потерян")
    return FileResponse(path)


@app.get("/api/banners/{bid}/audio")
def banners_audio(bid: str) -> FileResponse:
    banner = banner_lib.get_banner(bid)
    if not banner or not banner.get("audio"):
        raise HTTPException(404, "Звук баннера не задан")
    path = banner_lib.file_path(banner["audio"])
    if not path.is_file():
        raise HTTPException(404, "Файл звука потерян")
    return FileResponse(path)


@app.post("/api/projects/{pid}/banners/apply")
def project_banners_apply(pid: str) -> Dict[str, Any]:
    """Накладывает активные баннеры на готовые клипы проекта (кнопка «Применить»)."""
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "Проект не найден")
    clips = [c for c in db.list_clips(pid) if c.get("path")]
    if not clips:
        raise HTTPException(400, "Нет готовых клипов — сначала запустите нарезку")
    count = jobs.submit_banner_apply(pid)
    if not count:
        raise HTTPException(409, "Клипы уже обрабатываются")
    return {"ok": True, "clips": count}


