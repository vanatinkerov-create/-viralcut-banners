"""Окно стримера (facecam): поиск камеры в кадре и параметры вставки.

Стримы и записи игр почти всегда устроены одинаково: большой кадр игры/экрана и
маленькое окно с лицом стримера в углу. Нарезка в вертикаль теряет это окно,
поэтому мы находим его и вставляем отдельной картинкой поверх готового клипа.

Поиск без ручной разметки: смотрим лица по кадрам и ищем устойчивое маленькое
лицо в одном и том же углу (в отличие от обычного разговора, где лицо занимает
большую часть кадра и гуляет по центру).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import utils
from .config import ASSETS_DIR

_MODEL = ASSETS_DIR / "models" / "yunet.onnx"
_MAX_CAM_SHARE = 0.34  # окно камеры не больше трети ширины кадра


def _detector():
    import cv2

    if _MODEL.exists():
        try:
            return cv2.FaceDetectorYN.create(str(_MODEL), "", (320, 320), 0.6, 0.3, 5000), "yunet"
        except Exception:  # noqa: BLE001
            pass
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml"), "haar"


def _faces(det, kind: str, frame) -> List[Tuple[float, float, float, float]]:
    import cv2

    h, w = frame.shape[:2]
    out: List[Tuple[float, float, float, float]] = []
    if kind == "yunet":
        try:
            det.setInputSize((w, h))
            _, found = det.detect(frame)
        except Exception:  # noqa: BLE001
            found = None
        for f in (found if found is not None else []):
            x, y, fw, fh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            if float(f[-1]) > 0.5 and fw > 12:
                out.append((x, y, fw, fh))
        return out
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for (x, y, fw, fh) in det.detectMultiScale(gray, 1.1, 5, minSize=(18, 18)):
        out.append((float(x), float(y), float(fw), float(fh)))
    return out


def _corner(cx: float, cy: float, w: int, h: int) -> str:
    vertical = "top" if cy < h / 2 else "bottom"
    horizontal = "left" if cx < w / 2 else "right"
    return f"{vertical}-{horizontal}"


def detect_facecam(source: Path, start: float = 0.0, end: Optional[float] = None,
                   samples: int = 24) -> Optional[Dict[str, Any]]:
    """Возвращает координаты окна стримера в пикселях исходника или None.

    Ключевое отличие камеры стримера от лица в игре: она **не двигается**.
    Поэтому лица кластеризуются по позиции, и побеждает кластер, который
    встречается чаще всего и при этом стоит на месте.
    """
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return None

    info = utils.probe(source)
    duration = float(info["duration"] or 0)
    end = duration if end is None else min(end, duration)
    if duration < 3 or end - start < 2:
        return None

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None
    det, kind = _detector()
    timestamps = [start + (end - start) * (i + 0.5) / samples for i in range(samples)]
    width, height = int(info["width"]), int(info["height"])
    boxes: List[Tuple[float, float, float, float]] = []
    try:
        for ts in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            scale = min(1.0, 720 / max(1, w))
            small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
            for (x, y, fw, fh) in _faces(det, kind, small):
                fx, fy, fw, fh = x / scale, y / scale, fw / scale, fh / scale
                if fw > width * _MAX_CAM_SHARE or fw < width * 0.03:
                    continue  # крупный план или слишком мелко для окна камеры
                boxes.append((fx, fy, fw, fh))
    finally:
        cap.release()

    if len(boxes) < max(3, samples * 0.35):
        return None

    # кластеризация по центру: камера стоит на месте, лица в игре гуляют
    clusters: List[Dict[str, Any]] = []
    tolerance = width * 0.06
    for (fx, fy, fw, fh) in boxes:
        cx, cy = fx + fw / 2, fy + fh / 2
        for cluster in clusters:
            if abs(cx - cluster["cx"]) < tolerance and abs(cy - cluster["cy"]) < tolerance:
                cluster["items"].append((fx, fy, fw, fh))
                cluster["cx"] = sum(b[0] + b[2] / 2 for b in cluster["items"]) / len(cluster["items"])
                cluster["cy"] = sum(b[1] + b[3] / 2 for b in cluster["items"]) / len(cluster["items"])
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "items": [(fx, fy, fw, fh)]})

    best = None
    best_score = 0.0
    for cluster in clusters:
        items = cluster["items"]
        if len(items) < max(3, samples * 0.4):
            continue
        xs = [b[0] + b[2] / 2 for b in items]
        ys = [b[1] + b[3] / 2 for b in items]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        spread = max(max(xs) - min(xs), max(ys) - min(ys)) / max(1.0, width)
        if spread > 0.12:  # лицо гуляет по всему кадру — это не окно камеры
            continue
        ws = [b[2] for b in items]
        mean_w = sum(ws) / len(ws)
        std_w = (sum((x - mean_w) ** 2 for x in ws) / len(ws)) ** 0.5 / max(1.0, width)
        coverage = len(items) / samples
        # окно камеры: лицо одного размера и стоит на месте
        score = coverage * (1.0 - spread / 0.12) * (1.0 - min(1.0, std_w / 0.06))
        if score > best_score:
            best_score = score
            best = (cluster, coverage, spread)

    if best is None:
        return None

    cluster, coverage, spread = best
    items = cluster["items"]
    widths = sorted(b[2] for b in items)
    heights = sorted(b[3] for b in items)
    xs = sorted(b[0] for b in items)
    ys = sorted(b[1] for b in items)
    mid = len(items) // 2
    fw, fh = widths[mid], heights[mid]
    fx, fy = xs[mid], ys[mid]
    pad = 0.5
    cam_w = min(int(fw * (1 + pad * 2)), int(width * _MAX_CAM_SHARE))
    cam_h = min(int(fh * (1 + pad * 2)), int(height * _MAX_CAM_SHARE))
    cx = fx + fw / 2
    cy = fy + fh / 2
    x = max(0, min(int(cx - cam_w / 2), width - cam_w))
    y = max(0, min(int(cy - cam_h / 2), height - cam_h))
    return {
        "x": x, "y": y, "w": cam_w, "h": cam_h,
        "corner": _corner(cx, cy, width, height),
        "coverage": round(coverage, 2),
        "drift": round(spread * 100, 1),
        "source": [width, height],
    }


# --- маска со скруглёнными углами -------------------------------------------

def ensure_mask(size: int, radius: int = 20) -> Optional[Path]:
    """PNG-маска со скруглённым квадратом (кэшируется по размеру и радиусу)."""
    from .config import DATA_DIR

    cache = DATA_DIR / "cache" / "pip"
    cache.mkdir(parents=True, exist_ok=True)
    radius = max(0, min(int(radius), size // 2))
    path = cache / f"mask_{size}_{radius}.png"
    if path.exists() and path.stat().st_size > 0:
        return path
    inner = size / 2.0 - radius
    centre = size / 2.0 - 0.5
    expr = (f"if(lte(hypot(max(0\\,abs(X-{centre:.1f})-{inner:.1f})"
            f"\\,max(0\\,abs(Y-{centre:.1f})-{inner:.1f}))\\,{radius}),255,0)")
    cmd = [utils.ffmpeg(), "-y", "-v", "error", "-f", "lavfi",
           "-i", f"color=c=black:s={size}x{size},format=gray",
           "-vf", f"geq=lum='{expr}'", "-frames:v", "1", str(path)]
    try:
        proc = utils.run(cmd, timeout=30)
        if proc.returncode != 0 or not path.exists():
            print(f"[pip] маска не создалась: {(proc.stderr or '')[-200:]}", file=__import__("sys").stderr)
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[pip] маска не создалась: {exc}", file=__import__("sys").stderr)
        return None
    return path


def build_filters(cam: Dict[str, Any], size: int, x: int, y: int, mask: Path,
                  main_step: str, src: str = "[0:v]") -> str:
    """Собирает фильтры: основной кадр + окно камеры со скруглением.

    main_step — готовая цепочка основной картинки, оканчивающаяся меткой
    `[_basem]`. Результат заканчивается меткой `[base]`.
    src — метка входного видеопотока: при вырезании пауз это результат
    trim+concat `[vsrc]`, а не исходный `[0:v]`. Координаты окна камеры при этом
    остаются абсолютными — кадр до кропа не масштабируется.
    """
    cw, ch = int(cam["w"]), int(cam["h"])
    cx, cy = int(cam["x"]), int(cam["y"])
    step = main_step.replace(src, "[_main]", 1)
    return (
        f"{src}split=2[_main][_cam];"
        f"{step};"
        f"[_cam]crop={cw}:{ch}:{cx}:{cy},scale={size}:{size}:flags=lanczos,setsar=1,format=rgba[_camr];"
        f"[1:v]format=gray,scale={size}:{size}[_mask];"
        f"[_camr][_mask]alphamerge[_pip];"
        f"[_basem][_pip]overlay={x}:{y}:format=auto:shortest=1[base]"
    )
