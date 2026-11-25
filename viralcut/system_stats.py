"""Текущая нагрузка системы: CPU, память, видеокарта — без внешних зависимостей.

Нужна виджету в интерфейсе, чтобы человек видел, что происходит с машиной во
время рендера. Всё считается локально и дёшево: никаких сетевых вызовов.

macOS: CPU — счётчики ядра через mach host_statistics, память — vm_stat,
видеокарта — ioreg (Device Utilization %).
Windows: CPU — GetSystemTimes, память — GlobalMemoryStatusEx, GPU — nvidia-smi,
если он есть (иначе «н/д»).
Linux: /proc/stat и /proc/meminfo, GPU — nvidia-smi.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import subprocess
import sys
import threading
import time
import sys as _sys

from typing import Any, Dict, List, Optional, Tuple

_CACHE: Dict[str, Any] = {}


def _platform() -> str:
    return sys.platform
_GPU_CACHE: Dict[str, Any] = {"value": None, "ts": 0.0}
_GPU_FAIL_TS: List[float] = [0.0]
_GPU_SOURCE: List[str] = [""]
_MEM_CACHE: Dict[str, Any] = {"value": None, "ts": 0.0}


def _spawn() -> Dict[str, Any]:
    """Флаги запуска без консольного окна (Windows)."""
    try:
        from . import resources

        return resources.spawn_kwargs()
    except Exception:  # noqa: BLE001
        return {"creationflags": 0x08000000} if _sys.platform == "win32" else {}
_LAST_CPU: List[Tuple[float, float]] = []
_LAST_VALUE: List[float] = [0.0]
_LAST_TS: List[float] = [0.0]


# --- CPU ---------------------------------------------------------------------

def _cpu_macos() -> Optional[Tuple[int, int]]:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
    except OSError:
        return None
    mach_host_self = libc.mach_host_self
    mach_host_self.restype = ctypes.c_uint32
    host_statistics = libc.host_statistics
    host_statistics.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]

    class CpuLoadInfo(ctypes.Structure):
        _fields_ = [("user", ctypes.c_uint32), ("system", ctypes.c_uint32),
                    ("idle", ctypes.c_uint32), ("nice", ctypes.c_uint32)]

    info = CpuLoadInfo()
    count = ctypes.c_uint32(4)
    ret = host_statistics(mach_host_self(), 3,  # HOST_CPU_LOAD_INFO
                          ctypes.cast(ctypes.byref(info), ctypes.POINTER(ctypes.c_uint32)),
                          ctypes.byref(count))
    if ret != 0:
        return None
    busy = info.user + info.system + info.nice
    return busy, busy + info.idle


def _cpu_windows() -> Optional[Tuple[int, int]]:
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None

        def to_int(ft: "FILETIME") -> int:
            return (ft.high << 32) | ft.low

        idle_t = to_int(idle)
        busy = to_int(kern) + to_int(user) - idle_t
        return busy, busy + idle_t
    except Exception:
        return None


def _cpu_linux() -> Optional[Tuple[int, int]]:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            parts = fh.readline().split()[1:]
        vals = [int(x) for x in parts]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return total - idle, total
    except Exception:
        return None


def _cpu_ticks() -> Optional[Tuple[int, int]]:
    if _platform() == "darwin":
        return _cpu_macos()
    if sys.platform == "win32":
        return _cpu_windows()
    return _cpu_linux()


def cpu_percent(force: bool = False) -> Optional[float]:
    """Загрузка CPU между двумя вызовами (как в top).

    Частые вызовы (< 250 мс) отдают закешированное значение: за такой интервал
    разница счётчиков может быть нулевой, и виджет мигал бы «н/д».
    """
    now = time.monotonic()
    if not force and _LAST_VALUE[0] and now - _LAST_TS[0] < 0.25:
        return _LAST_VALUE[0]
    ticks = _cpu_ticks()
    if ticks is None:
        return None
    busy, total = ticks
    if not _LAST_CPU:
        _LAST_CPU.append((busy, total))
        time.sleep(0.12)
        ticks = _cpu_ticks()
        if ticks is None:
            return None
        busy, total = ticks
    prev_busy, prev_total = _LAST_CPU[0]
    _LAST_CPU[0] = (busy, total)
    d_total = total - prev_total
    d_busy = busy - prev_busy
    if d_total <= 0:
        # дельта нулевая — оцениваем по средней нагрузке на ядро
        try:
            value = round(min(100.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1) * 100), 1)
        except Exception:
            value = _LAST_VALUE[0] or 0.0
        _LAST_TS[0] = now
        return value
    value = round(max(0.0, min(100.0, d_busy / d_total * 100)), 1)
    _LAST_VALUE[0] = value
    _LAST_TS[0] = now
    return value


# --- Память ------------------------------------------------------------------

def memory(force: bool = False) -> Dict[str, Any]:
    now = time.monotonic()
    if not force and _MEM_CACHE.get("value") and now - _MEM_CACHE.get("ts", 0.0) < 1.0:
        return dict(_MEM_CACHE["value"])
    result = _memory_uncached()
    _MEM_CACHE["value"] = result
    _MEM_CACHE["ts"] = now
    return dict(result)


def _memory_uncached() -> Dict[str, Any]:
    total = 0
    used = 0
    if _platform() == "darwin":
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=3, **_spawn()).stdout.strip() or 0)
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3, **_spawn()).stdout
            page = 16384
            m = re.search(r"page size of (\d+) bytes", out)
            if m:
                page = int(m.group(1))
            vals = {}
            for line in out.splitlines():
                m2 = re.match(r"([^:]+):\s+(\d+)", line.strip())
                if m2:
                    vals[m2.group(1).strip()] = int(m2.group(2))
            used_pages = (vals.get("Pages active", 0) + vals.get("Pages wired down", 0)
                          + vals.get("Pages occupied by compressor", 0))
            used = used_pages * page
        except Exception:
            pass
    elif _platform() == "win32":
        try:
            class MEMSTATUS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]

            st = MEMSTATUS()
            st.dwLength = ctypes.sizeof(MEMSTATUS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                total = int(st.ullTotalPhys)
                used = total - int(st.ullAvailPhys)
        except Exception:
            pass
    else:
        try:
            info = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    k, v = line.split(":", 1)
                    info[k] = int(v.strip().split()[0]) * 1024
            total = info.get("MemTotal", 0)
            used = total - info.get("MemAvailable", info.get("MemFree", 0))
        except Exception:
            pass

    total_mb = round(total / 1024 / 1024)
    used_mb = round(used / 1024 / 1024)
    percent = round(used / total * 100, 1) if total else 0.0
    return {"used_mb": used_mb, "total_mb": total_mb, "percent": percent}


# --- Видеокарта ---------------------------------------------------------------

# Счётчики PDH: раскрытие масок и чтение значений идут через pdh.dll.
# Важно: без argtypes/restype ctypes на 64-битной Windows обрезает HANDLE до
# 32 бит — PdhCloseQuery получает мусорный указатель и процесс падает прямо во
# время опроса (симптом: «нажал Пересобрать — приложение закрылось без лога»).
_ENGINE_RE = re.compile(
    r"luid_(0x[0-9A-Fa-f]+)_(0x[0-9A-Fa-f]+)_phys_(\d+)_(?:eng_(\d+)_)?engtype_([A-Za-z0-9]+)")
_PDH_LOCK = threading.Lock()
_PDH_DLL: List[Any] = [None]
_PDH_VALUE: List[Any] = [None]


def _engine_key(counter_path: str) -> Tuple[str, str]:
    """(адаптер, тип движка) из пути счётчика «GPU Engine»."""
    match = _ENGINE_RE.search(counter_path or "")
    if not match:
        return "", ""
    adapter = f"{match.group(1)}:{match.group(2)}:phys_{match.group(3)}"
    return adapter, match.group(5).lower()


def _summarize_engine_utilization(samples: List[Tuple[str, float]]) -> Dict[str, Any]:
    """Суммирует 3D-движки по адаптеру и берёт самый занятый адаптер.

    Windows отдаёт по экземпляру счётчика на каждый процесс и каждый движок.
    Складывать все движки (как было раньше) — неверно: кодировщик видео и
    копирование прибавлялись к 3D и цифра улетала в 100% на пустой карте.
    """
    per_adapter: Dict[str, float] = {}
    for path, value in samples:
        adapter, engine = _engine_key(path)
        if not adapter or not engine.startswith("3d"):
            continue
        try:
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        per_adapter[adapter] = per_adapter.get(adapter, 0.0) + amount
    if not per_adapter:
        return {"percent": None, "adapters": 0}
    busiest = max(per_adapter.values())
    return {"percent": int(round(min(100.0, busiest))), "adapters": len(per_adapter)}


def _pdh():
    """Загруженный pdh.dll с явными прототипами функций (или None)."""
    if _PDH_DLL[0] is not None:
        return _PDH_DLL[0] or None
    try:
        from ctypes import wintypes as wt

        pdh = ctypes.WinDLL("pdh.dll")  # type: ignore[attr-defined]

        class PDH_FMT_COUNTERVALUE(ctypes.Structure):
            class _Value(ctypes.Union):
                _fields_ = [("longValue", wt.LONG),
                            ("doubleValue", ctypes.c_double),
                            ("largeValue", ctypes.c_longlong)]

            _anonymous_ = ("value",)
            _fields_ = [("CStatus", wt.DWORD), ("value", _Value)]

        _PDH_VALUE[0] = PDH_FMT_COUNTERVALUE
        pdh.PdhOpenQueryW.argtypes = [wt.LPCWSTR, ctypes.c_size_t,
                                      ctypes.POINTER(wt.HANDLE)]
        pdh.PdhOpenQueryW.restype = wt.LONG
        pdh.PdhExpandWildCardPathW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPWSTR,
                                               ctypes.POINTER(wt.DWORD), wt.DWORD]
        pdh.PdhExpandWildCardPathW.restype = wt.LONG
        pdh.PdhAddCounterW.argtypes = [wt.HANDLE, wt.LPCWSTR, ctypes.c_size_t,
                                       ctypes.POINTER(wt.HANDLE)]
        pdh.PdhAddCounterW.restype = wt.LONG
        pdh.PdhCollectQueryData.argtypes = [wt.HANDLE]
        pdh.PdhCollectQueryData.restype = wt.LONG
        pdh.PdhGetFormattedCounterValue.argtypes = [
            wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(PDH_FMT_COUNTERVALUE)]
        pdh.PdhGetFormattedCounterValue.restype = wt.LONG
        pdh.PdhCloseQuery.argtypes = [wt.HANDLE]
        pdh.PdhCloseQuery.restype = wt.LONG
    except Exception:  # noqa: BLE001 — не Windows или pdh.dll недоступна
        _PDH_DLL[0] = False
        return None
    _PDH_DLL[0] = pdh
    return pdh


def _gpu_windows_pdh() -> Dict[str, Any]:
    """Загрузка GPU через счётчики PDH — без запуска nvidia-smi и окон консоли.

    Windows отдаёт производительность видеокарты в счётчиках «GPU Engine».
    Читаем их напрямую через pdh.dll: ни одного дочернего процесса. Счётчик
    работает для NVIDIA, Intel и AMD сразу — вендорных DLL не нужно.
    """
    try:
        from ctypes import wintypes as wt
    except Exception:  # noqa: BLE001
        return {"percent": None, "name": ""}
    pdh = _pdh()
    if pdh is None:
        return {"percent": None, "name": ""}

    with _PDH_LOCK:
        query = wt.HANDLE()
        if pdh.PdhOpenQueryW(None, ctypes.c_size_t(0), ctypes.byref(query)) != 0:
            return {"percent": None, "name": ""}
        try:
            path = "\\GPU Engine(*)\\Utilization Percentage"
            size = wt.DWORD(0)
            pdh.PdhExpandWildCardPathW(None, path, None, ctypes.byref(size), wt.DWORD(0))
            if size.value <= 0:
                return {"percent": None, "name": ""}
            buf = ctypes.create_unicode_buffer(size.value + 2)
            if pdh.PdhExpandWildCardPathW(None, path, buf, ctypes.byref(size),
                                          wt.DWORD(0)) != 0:
                return {"percent": None, "name": ""}
            counters: List[Tuple[str, Any]] = []
            for counter_path in buf[:size.value].split("\0"):
                if not counter_path or "engtype_3d" not in counter_path.lower():
                    continue
                handle = wt.HANDLE()
                if pdh.PdhAddCounterW(query, counter_path, ctypes.c_size_t(0),
                                      ctypes.byref(handle)) == 0:
                    counters.append((counter_path, handle))
            if not counters:
                return {"percent": None, "name": ""}

            if pdh.PdhCollectQueryData(query) != 0:
                return {"percent": None, "name": ""}
            time.sleep(0.08)  # PDH считает процент по двум замерам
            if pdh.PdhCollectQueryData(query) != 0:
                return {"percent": None, "name": ""}

            samples: List[Tuple[str, float]] = []
            PDH_FMT_DOUBLE = 0x00000200
            for counter_path, handle in counters:
                value = _PDH_VALUE[0]()
                counter_type = wt.DWORD(0)
                status = pdh.PdhGetFormattedCounterValue(
                    handle, PDH_FMT_DOUBLE, ctypes.byref(counter_type), ctypes.byref(value))
                # PDH_CSTATUS_VALID_DATA (0) / PDH_CSTATUS_NEW_DATA (1)
                if status == 0 and value.CStatus in (0, 1):
                    samples.append((counter_path, value.doubleValue))
            summary = _summarize_engine_utilization(samples)
            if summary.get("percent") is None:
                return {"percent": None, "name": ""}
            return {"percent": summary["percent"], "name": "GPU",
                    "adapters": summary.get("adapters", 0)}
        finally:
            pdh.PdhCloseQuery(query)


def _gpu_vendor(name: str) -> str:
    """Вендор по имени адаптера — для подписи в виджете."""
    low = (name or "").lower()
    if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx " in low or "gtx " in low:
        return "nvidia"
    if "amd" in low or "radeon" in low or "ati " in low:
        return "amd"
    if "intel" in low or "arc" in low or "iris" in low or "uhd" in low:
        return "intel"
    return ""


def _gpu_windows_registry_name() -> str:
    """Имя видеокарты из реестра — тоже без запуска процессов."""
    try:
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\WinSAT")
        value, _ = winreg.QueryValueEx(key, "PrimaryAdapterString")
        return str(value).strip() or "GPU"
    except Exception:  # noqa: BLE001
        return "GPU"


def _gpu_windows_adl() -> Dict[str, Any]:
    """AMD (ADL): активность GPU в процентах через atiadlxx.dll.

    Нужна как последний запасной путь: PDH-счётчики иногда отключены групповой
    политикой, а nvidia-smi есть только у NVIDIA. Любая ошибка загрузки/вызова
    означает «нет данных» — мониторинг не должен ронять приложение.
    """
    try:
        from ctypes import wintypes as wt
    except Exception:  # noqa: BLE001
        return {"percent": None, "name": "", "vendor": "amd"}
    adl = None
    for dll_name in ("atiadlxx.dll", "atiadlxy.dll"):
        try:
            adl = ctypes.WinDLL(dll_name)  # type: ignore[attr-defined]
            break
        except Exception:  # noqa: BLE001
            continue
    if adl is None:
        return {"percent": None, "name": "", "vendor": "amd"}

    buffers: List[Any] = []
    CALLBACK = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)

    def _alloc(size: int) -> int:
        buf = ctypes.create_string_buffer(max(1, int(size)))
        buffers.append(buf)          # держим ссылку: ADL не копирует буфер
        if len(buffers) > 64:
            buffers.pop(0)
        return ctypes.addressof(buf)

    callback = CALLBACK(_alloc)
    context = ctypes.c_void_p()
    try:
        if hasattr(adl, "ADL2_Main_Control_Create"):
            create = adl.ADL2_Main_Control_Create
            create.argtypes = [CALLBACK, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
            create.restype = ctypes.c_int
            if create(callback, 1, ctypes.byref(context)) != 0:
                return {"percent": None, "name": "", "vendor": "amd"}
            activity = getattr(adl, "ADL2_Overdrive6_Activity_Get", None)
            destroy = getattr(adl, "ADL2_Main_Control_Destroy", None)
            ctx_arg = context
        else:
            create = getattr(adl, "ADL_Main_Control_Create", None)
            activity = getattr(adl, "ADL_Overdrive6_Activity_Get", None)
            destroy = getattr(adl, "ADL_Main_Control_Destroy", None)
            ctx_arg = None
            if create is None:
                return {"percent": None, "name": "", "vendor": "amd"}
            create.argtypes = [CALLBACK, ctypes.c_int]
            create.restype = ctypes.c_int
            if create(callback, 1) != 0:
                return {"percent": None, "name": "", "vendor": "amd"}
        try:
            if activity is None:
                return {"percent": None, "name": "", "vendor": "amd"}
            # явные прототипы: вызов с неправильной сигнатурой — это порча стека
            if ctx_arg is not None:
                activity.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_int)]
            else:
                activity.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_int)]
            activity.restype = ctypes.c_int
            if destroy is not None:
                destroy.argtypes = [ctypes.c_void_p] if ctx_arg is not None else []
                destroy.restype = ctypes.c_int
            count = ctypes.c_int(0)
            number_of = getattr(adl, "ADL_Adapter_NumberOfAdapters_Get", None)
            if number_of is not None:
                number_of.argtypes = [ctypes.POINTER(ctypes.c_int)]
                number_of.restype = ctypes.c_int
                if number_of(ctypes.byref(count)) != 0 or count.value <= 0:
                    return {"percent": None, "name": "", "vendor": "amd"}
            else:
                count.value = 1

            best = 0
            for index in range(min(count.value, 8)):
                percent = ctypes.c_int(0)
                level = ctypes.c_int(0)
                if ctx_arg is not None:
                    code = activity(ctx_arg, index, ctypes.byref(percent),
                                    ctypes.byref(level))
                else:
                    code = activity(index, ctypes.byref(percent), ctypes.byref(level))
                if code == 0:
                    best = max(best, int(percent.value))
            if best <= 0:
                return {"percent": None, "name": "", "vendor": "amd"}
            return {"percent": min(100, best), "name": "AMD Radeon", "vendor": "amd"}
        finally:
            if destroy is not None:
                try:
                    destroy(ctx_arg) if ctx_arg is not None else destroy()
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001 — ADL не любит чужие контексты/версии
        return {"percent": None, "name": "", "vendor": "amd"}
    finally:
        del callback  # после Destroy колбэк больше не нужен


def _gpu_windows() -> Dict[str, Any]:
    """GPU на Windows: PDH → nvidia-smi → ADL, с кэшем и защитой от спама.

    PDH покрывает все три вендора и не запускает процессов; nvidia-smi даёт
    точное имя карты; ADL — путь для AMD, когда счётчики выключены политикой.
    """
    now = time.monotonic()
    if _GPU_CACHE.get("value") and now - _GPU_CACHE.get("ts", 0.0) < 2.0:
        return dict(_GPU_CACHE["value"])

    name = _gpu_windows_registry_name()
    result = _gpu_windows_pdh()
    if result.get("percent") is not None:
        result["name"] = name
        result["vendor"] = _gpu_vendor(name)
        result["source"] = "pdh"
        _GPU_SOURCE[0] = "pdh"
        _GPU_CACHE["value"] = result
        _GPU_CACHE["ts"] = now
        return dict(result)

    # PDH не отдал данные (старая Windows/счётчики отключены) — nvidia-smi,
    # но не чаще раза в 30 секунд и всегда без окна консоли
    legacy = _GPU_CACHE.get("legacy") or {"value": None, "ts": 0.0}
    if legacy["value"] and now - legacy["ts"] < 30.0:
        return dict(legacy["value"])
    if now - _GPU_FAIL_TS[0] < 30.0:
        return {"percent": None, "name": name, "vendor": _gpu_vendor(name),
                "source": "none"}
    smi = _gpu_nvidia()
    if smi.get("percent") is not None:
        smi["vendor"] = "nvidia"
        smi["source"] = "nvidia-smi"
        _GPU_SOURCE[0] = "nvidia-smi"
        _GPU_CACHE["legacy"] = {"value": smi, "ts": now}
        _GPU_CACHE["value"] = smi
        _GPU_CACHE["ts"] = now
        return dict(smi)

    # AMD: ADL, если драйвер установлен и API ответил
    adl = _gpu_windows_adl()
    if adl.get("percent") is not None:
        adl["source"] = "adl"
        _GPU_SOURCE[0] = "adl"
        _GPU_CACHE["legacy"] = {"value": adl, "ts": now}
        _GPU_CACHE["value"] = adl
        _GPU_CACHE["ts"] = now
        return dict(adl)

    _GPU_FAIL_TS[0] = now
    return {"percent": None, "name": name, "vendor": _gpu_vendor(name), "source": "none"}


def _gpu_macos() -> Dict[str, Any]:
    try:
        out = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "AGXAccelerator"],
                             capture_output=True, text=True, timeout=4, **_spawn()).stdout
        if not out:
            out = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                                 capture_output=True, text=True, timeout=4, **_spawn()).stdout
        m = re.search(r'"Device Utilization %"=(\d+)', out)
        if not m:
            return {"percent": None, "name": "Apple GPU"}
        model = ""
        mm = re.search(r'"model"\s*=\s*"([^"]+)"', out)
        if mm:
            model = mm.group(1)
        return {"percent": int(m.group(1)), "name": model or "Apple GPU"}
    except Exception:
        return {"percent": None, "name": "Apple GPU"}


def _gpu_nvidia() -> Dict[str, Any]:
    exe = "nvidia-smi"
    try:
        out = subprocess.run([exe, "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=4, **_spawn())
        if out.returncode != 0 or not out.stdout.strip():
            return {"percent": None, "name": ""}
        first = out.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        return {"percent": int(float(parts[0])), "name": parts[3] if len(parts) > 3 else "GPU"}
    except Exception:
        return {"percent": None, "name": ""}


def gpu(force: bool = False) -> Dict[str, Any]:
    """Видеокарта с кэшем на 1.5 с: на Windows каждый вызов — это nvidia-smi."""
    now = time.monotonic()
    if not force and _GPU_CACHE.get("value") and now - _GPU_CACHE.get("ts", 0.0) < 1.5:
        return dict(_GPU_CACHE["value"])
    plat = _platform()
    if plat == "darwin":
        result = _gpu_macos()
    elif plat == "win32":
        return _gpu_windows()
    elif plat == "linux":
        result = _gpu_nvidia()
    else:
        result = {"percent": None, "name": ""}
    _GPU_CACHE["value"] = result
    _GPU_CACHE["ts"] = now
    return dict(result)


def active_job(projects: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Что приложение делает прямо сейчас — для строки процесса в виджете."""
    for p in projects:
        status = str(p.get("status") or "")
        if status in ("running", "preparing", "queued"):
            return {
                "id": p.get("id"),
                "title": p.get("title") or p.get("url") or "Проект",
                "status": status,
                "stage": p.get("stage") or "",
                "progress": round(float(p.get("progress") or 0), 1),
            }
    return None


def snapshot(app_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Всё сразу: CPU, память, GPU и (опционально) наши лимиты/энкодер."""
    data = {
        "cpu": cpu_percent(),
        "monitor": {"gpu_source": _GPU_SOURCE[0] or ("ioreg" if _platform() == "darwin" else "none")},
        "ram": memory(),
        "gpu": gpu(),
        "ts": round(time.time(), 3),
    }
    if app_stats:
        data["app"] = app_stats
    return data
