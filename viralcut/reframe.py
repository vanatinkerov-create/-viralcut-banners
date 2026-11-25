"""Умное кадрирование: поиск лиц и плавное слежение для вертикального кадра.

Возвращает путь окна кадрирования (keyframes), который render.py превращает
в выражение для ffmpeg crop. Без лиц — центральный кроп.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import utils
from .config import ASSETS_DIR

_MODEL = ASSETS_DIR / "models" / "yunet.onnx"


def _make_detector():
    import cv2

    if _MODEL.exists():
        try:
            return "yunet", cv2.FaceDetectorYN.create(str(_MODEL), "", (320, 320), 0.6, 0.3, 5000)
        except Exception:
            pass
    return "haar", None


def _detect(detector: Tuple[str, Any], frame) -> List[Dict[str, float]]:
    import cv2

    kind, det = detector
    h, w = frame.shape[:2]
    out: List[Dict[str, float]] = []
    if kind == "yunet" and det is not None:
        try:
            det.setInputSize((w, h))
            _, faces = det.detect(frame)
        except Exception:
            faces = None
        if faces is not None:
            for f in faces:
                x, y, fw, fh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
                score = float(f[-1])
                if score < 0.5 or fw < 16:
                    continue
                out.append({"cx": x + fw / 2, "cy": y + fh / 2, "w": fw, "h": fh, "score": score})
        return out

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    for (x, y, fw, fh) in faces:
        out.append({"cx": x + fw / 2, "cy": y + fh / 2, "w": float(fw), "h": float(fh), "score": 1.0})
    return out


def _mouth_patch(frame, face: Dict[str, float], size: int = 8):
    """Мини-патч области рта: по нему ловим артикуляцию между кадрами."""
    import cv2

    h, w = frame.shape[:2]
    cx, cy = face["cx"], face["cy"]
    fw, fh = max(8.0, face["w"]), max(8.0, face["h"])
    # рот — в нижней трети лица
    mx0 = int(max(0, cx - fw * 0.22))
    mx1 = int(min(w, cx + fw * 0.22))
    my0 = int(max(0, cy + fh * 0.18))
    my1 = int(min(h, cy + fh * 0.42))
    if mx1 - mx0 < 4 or my1 - my0 < 3:
        return None
    patch = frame[my0:my1, mx0:mx1]
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return cv2.resize(patch, (size, size)).astype("float32")


def _mouth_motion(prev_faces, prev_patches, faces, patches) -> List[float]:
    """Насколько сильно изменился рот каждого лица относительно прошлого кадра."""
    scores: List[float] = []
    for face, patch in zip(faces, patches):
        if patch is None or not prev_faces:
            scores.append(0.0)
            continue
        best, best_dist = 0.0, 1e9
        for pface, ppatch in zip(prev_faces, prev_patches):
            if ppatch is None:
                continue
            dist = abs(pface["cx"] - face["cx"]) + abs(pface["cy"] - face["cy"])
            if dist > face["w"] * 1.2:
                continue
            if dist < best_dist:
                best_dist = dist
                best = float(abs(patch - ppatch).mean())
        scores.append(best)
    return scores


def _median(values: Sequence[float], window: int = 5) -> List[float]:
    out: List[float] = []
    half = window // 2
    for i in range(len(values)):
        chunk = sorted(values[max(0, i - half): min(len(values), i + half + 1)])
        out.append(chunk[len(chunk) // 2])
    return out


def _smooth(values: List[Optional[float]], alpha: float = 0.22,
            max_speed: float = 160.0, dt: float = 0.5,
            deadband: float = 0.0) -> List[float]:
    """Заполняет пропуски, сглаживает EMA, гасит дребезг и ограничивает скорость.

    Три причины «дёрганой» картинки: шум детектора (метры прыгают на десятки
    пикселей между кадрами), слишком быстрый EMA и отсутствие мёртвой зоны —
    окно отрабатывает микродвижения головы. Медиана + медленный EMA + deadband
    оставляют движение плавным, но не дают лицу выйти из кадра.
    """
    filled: List[float] = []
    last: Optional[float] = None
    for v in values:
        if v is None:
            if last is None:
                filled.append(float("nan"))
            else:
                filled.append(last)
        else:
            last = v
            filled.append(v)

    known = [v for v in filled if not math.isnan(v)]
    if not known:
        return []
    default = known[len(known) // 2]
    filled = [default if math.isnan(v) else v for v in filled]
    med = _median(filled, 5)

    out: List[float] = []
    current = med[0]
    for target in med:
        current += (target - current) * alpha
        prev = out[-1] if out else current
        delta = current - prev
        limit = max_speed * dt
        if abs(delta) > limit:
            current = prev + math.copysign(limit, delta)
        elif abs(current - prev) < deadband:
            # микродрожание: держим окно на месте, а не бегаем за шумом детектора
            current = prev
        out.append(current)
    return out


def _densify(times: List[float], values: List[float], fps: float = 6.0) -> Tuple[List[float], List[float]]:
    """Уплотняет путь до 6 кадров в секунду.

    ffmpeg интерполирует ключевые кадры линейно, поэтому при 2 замерах в секунду
    движение выглядит ступеньками. Частые keyframe превращают его в плавную панораму.
    """
    if len(times) < 2 or not values:
        return times, values
    try:
        import numpy as np

        start, end = float(times[0]), float(times[-1])
        count = max(2, int(round((end - start) * fps)) + 1)
        dense_t = np.linspace(start, end, count)
        dense_v = np.interp(dense_t, np.asarray(times, dtype=float), np.asarray(values, dtype=float))
        # лёгкое сглаживание по плотной сетке: убирает изломы на стыках
        if count > 5:
            kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
            kernel /= kernel.sum()
            padded = np.pad(dense_v, (2, 2), mode="edge")
            dense_v = np.convolve(padded, kernel, mode="valid")
        return [round(float(t), 3) for t in dense_t], [round(float(v), 1) for v in dense_v]
    except Exception:  # noqa: BLE001 — без numpy остаются исходные точки
        return times, values


def _rdp(times: List[float], values: List[float], tolerance: float) -> List[int]:
    """Алгоритм Дугласа–Пекера: оставляет только точки, меняющие форму пути."""
    n = len(times)
    if n <= 2:
        return list(range(n))
    keep = {0, n - 1}
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        t0, t1 = times[start], times[end]
        v0, v1 = values[start], values[end]
        span = max(1e-6, t1 - t0)
        worst, worst_i = 0.0, -1
        for i in range(start + 1, end):
            # отклонение точки от прямой (в пикселях)
            predicted = v0 + (values[i] - v0) * 0 if False else v0 + (v1 - v0) * ((times[i] - t0) / span)
            dev = abs(values[i] - predicted)
            if dev > worst:
                worst, worst_i = dev, i
        if worst > tolerance and worst_i > 0:
            keep.add(worst_i)
            stack.append((start, worst_i))
            stack.append((worst_i, end))
    return sorted(keep)


def _simplify_path(times: List[float], xs: List[float], ys: List[float],
                   max_points: int = 60) -> Tuple[List[float], List[float], List[float]]:
    """Сжимает путь до безопасного числа keyframe.

    ffmpeg парсит выражение целиком: примерно после 2000 символов он падает с
    «Missing ')' or too many args», и рендер умирает. Каждое слагаемое — около
    26 символов, поэтому держим ≤60 точек (≈1.5 КБ). Дуглас–Пекер при этом
    сохраняет форму с точностью в пару пикселей, а движение остаётся плавным:
    между точками ffmpeg интерполирует линейно, а сама кривая уже сглажена.
    """
    n = len(times)
    if n <= 3:
        return times, xs, ys
    tol = 0.6
    for _ in range(8):
        idx = sorted(set(_rdp(times, xs, tol)) | set(_rdp(times, ys, tol)))
        if len(idx) <= max_points:
            return [times[i] for i in idx], [xs[i] for i in idx], [ys[i] for i in idx]
        tol *= 1.7
    # даже грубый допуск не помог — равномерно прореживаем
    step = max(1, (n - 1) // max(2, max_points - 1))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [times[i] for i in idx], [xs[i] for i in idx], [ys[i] for i in idx]


def _coarse_path(path: Dict[str, Any], max_points: int = 18) -> Dict[str, Any]:
    """Аварийный вариант пути: редкие точки. Нужен, если ffmpeg всё же отказался."""
    times = path["times"]
    if len(times) <= max_points:
        return path
    step = max(1, math.ceil((len(times) - 1) / (max_points - 1)))
    idx = list(range(0, len(times), step))[:max_points]
    if idx[-1] != len(times) - 1:
        idx[-1] = len(times) - 1
    return {**path, "times": [times[i] for i in idx],
            "x": [path["x"][i] for i in idx], "y": [path["y"][i] for i in idx]}


def build_crop_path(source: Path, start: float, end: float, aspect: str = "9:16",
                    sample_fps: float = 3.0, max_width: int = 960) -> Optional[Dict[str, Any]]:
    """Анализирует фрагмент и возвращает keyframes окна кадрирования.

    Результат: {"x": [...], "y": [...], "times": [...], "crop_w": int, "crop_h": int}
    Координаты — в пикселях исходного видео, times — секунды от начала клипа.
    """
    try:
        import cv2
    except Exception:
        return None

    info = utils.probe(source)
    src_w, src_h = info["width"], info["height"]
    aw, ah = (9, 16)
    try:
        aw, ah = [int(v) for v in aspect.split(":")]
    except Exception:
        pass

    # окно кадрирования под целевой аспект, максимально возможное (без апскейла по ширине)
    if src_w / src_h > aw / ah:
        crop_h = src_h
        crop_w = max(2, int(round(src_h * aw / ah)))
    else:
        crop_w = src_w
        crop_h = max(2, int(round(src_w * ah / aw)))

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        duration = max(0.1, end - start)
        step = max(1, int(round(fps / max(0.5, sample_fps))))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

        detector = _make_detector()
        samples: List[Tuple[float, List[Dict[str, float]], List[Any]]] = []
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if ts < start - 0.05:
                frame_idx += 1
                continue
            if ts > end:
                break
            if frame_idx % step == 0:
                h, w = frame.shape[:2]
                scale = min(1.0, max_width / max(1, w))
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
                faces = _detect(detector, small)
                if scale < 1:
                    for face in faces:
                        for key in ("cx", "cy", "w", "h"):
                            face[key] /= scale
                mouth = [_mouth_patch(frame, face) for face in faces]
                samples.append((max(0.0, ts - start), faces, mouth))
            frame_idx += 1

        if not samples:
            return None

        dt = max(0.05, duration / max(1, len(samples) - 1))
        xs: List[Optional[float]] = []
        ys: List[Optional[float]] = []
        prev_cx: Optional[float] = None

        prev_faces: List[Dict[str, float]] = []
        prev_mouth: List[Any] = []
        speaker_idx: Optional[int] = None
        last_switch = -99.0
        for ts, faces, mouth in samples:
            if not faces:
                xs.append(None)
                ys.append(None)
                prev_faces, prev_mouth = [], []
                continue
            order = sorted(range(len(faces)), key=lambda k: faces[k]["w"] * faces[k]["h"], reverse=True)
            faces = [faces[k] for k in order]
            mouth = [mouth[k] for k in order]
            motion = _mouth_motion(prev_faces, prev_mouth, faces, mouth)
            if speaker_idx is None or speaker_idx >= len(faces):
                speaker_idx = 0
            elif len(faces) > 1 and len(motion) == len(faces):
                best = max(range(len(motion)), key=lambda k: motion[k])
                current = motion[speaker_idx] if speaker_idx < len(motion) else 0.0
                best_motion = motion[best]
                far = abs(faces[best]["cx"] - faces[speaker_idx]["cx"]) > crop_w * 0.9
                # Переключаемся на другого человека только когда он явно говорит,
                # а текущий молчит (или он рядом и просто активнее). Иначе окно
                # прыгает между людьми и картинка «дёргается» — главная жалоба.
                strong = best_motion > max(2.0, current * (4.0 if far else 2.2))
                if (best != speaker_idx and strong and ts - last_switch > 2.5):
                    speaker_idx = best
                    last_switch = ts
            speaker = speaker_idx
            primary = faces[speaker] if 0 <= speaker < len(faces) else faces[0]
            if prev_cx is not None and len(faces) > 1 and speaker == 0:
                # без явного говорящего держимся за тем же человеком, а не прыгаем
                def cost(f: Dict[str, float]) -> float:
                    return abs(f["cx"] - prev_cx) / max(1.0, src_w) + 0.35 * (1 - f["w"] / max(1.0, faces[0]["w"]))
                primary = min(faces, key=cost)
            prev_faces, prev_mouth = faces, mouth
            prev_cx = primary["cx"]

            # если два лица рядом и оба влезают в окно — центрируем между ними
            cx = primary["cx"]
            near = [f for f in faces if abs(f["cx"] - primary["cx"]) < crop_w * 0.95]
            if len(near) >= 2:
                cx = sum(f["cx"] for f in near) / len(near)

            xs.append(cx)
            ys.append(primary["cy"])

        if not any(v is not None for v in xs):
            center_x = max(0.0, (src_w - crop_w) / 2)
            center_y = max(0.0, (src_h - crop_h) / 2)
            return {"times": [0.0, duration], "x": [center_x, center_x],
                    "y": [center_y, center_y], "crop_w": crop_w, "crop_h": crop_h,
                    "faces": 0, "samples": len(samples), "coverage": 0.0,
                    "source": [src_w, src_h]}

        smooth_x = _smooth(xs, alpha=0.22, max_speed=max(70.0, crop_w * 0.5), dt=dt,
                           deadband=crop_w * 0.006)
        smooth_y = _smooth(ys, alpha=0.16, max_speed=max(45.0, crop_h * 0.3), dt=dt,
                           deadband=crop_h * 0.006)

        max_x = max(0.0, src_w - crop_w)
        max_y = max(0.0, src_h - crop_h)
        base_times = [float(ts) for (ts, _, _) in samples]
        times: List[float] = []
        path_x: List[float] = []
        path_y: List[float] = []
        for ts, cx, cy in zip(base_times, smooth_x, smooth_y):
            x = max(0.0, min(max_x, cx - crop_w / 2))
            # голова должна оставаться в кадре целиком: держим лицо по центру-верху
            y = max(0.0, min(max_y, cy - crop_h * 0.42))
            times.append(round(ts, 3))
            path_x.append(round(x, 1))
            path_y.append(round(y, 1))

        # густая сетка keyframe: движение перестаёт быть ступеньками
        # (x и y уплотняем по одной и той же исходной сетке времени)
        times, path_x = _densify(base_times, path_x, fps=6.0)
        _, path_y = _densify(base_times, path_y, fps=6.0)
        # и сжимаем до разумного числа точек: ffmpeg не ест выражения-простыни
        times, path_x, path_y = _simplify_path(times, path_x, path_y)

        face_frames = sum(1 for _, f, _ in samples if f)
        return {
            "times": times,
            "x": path_x,
            "y": path_y,
            "crop_w": crop_w,
            "crop_h": crop_h,
            "faces": face_frames,
            "samples": len(samples),
            "coverage": round(face_frames / max(1, len(samples)), 3),
            "source": [src_w, src_h],
        }
    finally:
        cap.release()


def _piecewise(times: List[float], values: List[float], clamp_max: float, var: str = "t") -> str:
    """Выражение ffmpeg: кусочно-линейная интерполяция через сумму ramp-функций."""
    if not times or not values:
        return "0"
    if len(times) == 1:
        return f"{values[0]:.1f}"
    slopes: List[float] = []
    for i in range(len(times) - 1):
        dt = max(1e-3, times[i + 1] - times[i])
        slopes.append((values[i + 1] - values[i]) / dt)

    expr = f"{values[0]:.1f}"
    prev_slope = 0.0
    for i, slope in enumerate(slopes):
        delta = slope - prev_slope
        prev_slope = slope
        if abs(delta) < 1e-4:
            continue
        expr += f"+({delta:.3f})*max(0\\,{var}-{times[i]:.3f})"
    # после последнего keyframe движение прекращается
    delta = -prev_slope
    if abs(delta) > 1e-4:
        expr += f"+({delta:.3f})*max(0\\,{var}-{times[-1]:.3f})"
    return f"max(0\\,min({clamp_max:.1f}\\,{expr}))"


def crop_expressions(path: Dict[str, Any]) -> Tuple[str, str]:
    """Возвращает (x_expr, y_expr) для фильтра crop."""
    times = path["times"]
    max_x = max(0.0, path["source"][0] - path["crop_w"])
    max_y = max(0.0, path["source"][1] - path["crop_h"])
    x_expr = _piecewise(times, path["x"], max_x, "t")
    y_expr = _piecewise(times, path["y"], max_y, "t")
    return x_expr, y_expr


def remap_crop_path(path: Optional[Dict[str, Any]], segments: List[Dict[str, float]],
                    clip_start: float) -> Optional[Dict[str, Any]]:
    """Пересчитывает кривую кадрирования на время склеенного клипа.

    `times` в кривой — секунды от начала окна клипа, а план склейки задан
    абсолютными временами исходника. Точки, попавшие в вырезанные паузы,
    выбрасываются, остальные сдвигаются на новое место. Без этого окно
    кадрирования «опаздывало» бы ровно на длину вырезанных пауз.
    """
    if not path or not segments:
        return path
    times = [float(t) for t in path.get("times") or []]
    xs = list(path.get("x") or [])
    ys = list(path.get("y") or [])
    if not times or len(xs) != len(times) or len(ys) != len(times):
        return None

    offsets: List[float] = []
    total = 0.0
    for seg in segments:
        offsets.append(total)
        total += seg["end"] - seg["start"]

    out_t: List[float] = []
    out_x: List[float] = []
    out_y: List[float] = []
    for t, x, y in zip(times, xs, ys):
        absolute = clip_start + t
        for seg, offset in zip(segments, offsets):
            if seg["start"] - 1e-6 <= absolute <= seg["end"] + 1e-6:
                new_t = offset + max(0.0, absolute - seg["start"])
                if out_t and new_t - out_t[-1] < 1e-3:
                    # две точки на одном месте: оставляем последнюю (она свежее)
                    out_x[-1], out_y[-1] = x, y
                    out_t[-1] = new_t
                else:
                    out_t.append(new_t)
                    out_x.append(x)
                    out_y.append(y)
                break

    if len(out_t) < 2:
        return None
    if out_t[0] > 1e-3:
        out_t.insert(0, 0.0)
        out_x.insert(0, out_x[0])
        out_y.insert(0, out_y[0])
    if total - out_t[-1] > 0.05:
        out_t.append(total)
        out_x.append(out_x[-1])
        out_y.append(out_y[-1])

    out = dict(path)
    out["times"] = [round(v, 3) for v in out_t]
    out["x"] = list(out_x)
    out["y"] = list(out_y)
    return out
