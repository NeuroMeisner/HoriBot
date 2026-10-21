"""Сбор метрик сервера для экрана «Параметры сервера» в админ-панели.

Источники данных:
- server_state/thermal_status.json от thermal_guard (приоритет: температуры);
- psutil (нагрузка, RAM, диск, аптайм);
- nvidia-smi (GPU, работает и на Windows).

На Windows температуры CPU могут быть недоступны — поля будут None,
панель покажет «нет данных». Это нормально для разработки.
"""

import json
import os
import shutil
import subprocess
import time

import psutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(PROJECT_DIR, "server_state", "thermal_status.json")


def _read_guard_status() -> dict | None:
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _nvidia_gpu() -> tuple | None:
    """(температура, имя) GPU через nvidia-smi, если есть."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            parts = [p.strip() for p in out.stdout.splitlines()[0].split(",", 1)]
            return float(parts[0]), (parts[1] if len(parts) > 1 else "NVIDIA")
    except Exception:
        pass
    return None


def format_bytes(num: float | None) -> str:
    if num is None:
        return "—"
    gb = num / (1024 ** 3)
    return f"{gb:.1f} ГБ"


def format_uptime(sec: float | None) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} дн {hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def get_server_metrics() -> dict:
    """Собирает все метрики для панели."""
    m = {
        "cpu_temp": None, "gpu_temp": None, "gpu_name": None,
        "disk_temps": {}, "cpu_load": None,
        "ram_used": None, "ram_total": None,
        "disk_used": None, "disk_total": None,
        "uptime_sec": None, "guard": None,
    }

    # Температуры берём из статус-файла guard (единый источник)
    guard = _read_guard_status()
    m["guard"] = guard
    if guard and guard.get("temps"):
        t = guard["temps"]
        m["cpu_temp"] = t.get("cpu")
        m["gpu_temp"] = t.get("gpu")
        m["disk_temps"] = t.get("disks") or {}
    else:
        # Фолбэк: psutil (на Linux обычно есть, на Windows — нет)
        try:
            sensors = psutil.sensors_temperatures()
            vals = [e.current for entries in sensors.values()
                    for e in entries if e.current]
            if vals:
                m["cpu_temp"] = max(vals)
        except Exception:
            pass

    gpu = _nvidia_gpu()
    if gpu:
        if m["gpu_temp"] is None:
            m["gpu_temp"] = gpu[0]
        m["gpu_name"] = gpu[1]

    try:
        m["cpu_load"] = psutil.cpu_percent(interval=0.5)
    except Exception:
        pass
    try:
        vm = psutil.virtual_memory()
        m["ram_used"], m["ram_total"] = vm.used, vm.total
    except Exception:
        pass
    try:
        du = psutil.disk_usage("/" if os.name != "nt" else "C:\\")
        m["disk_used"], m["disk_total"] = du.used, du.total
    except Exception:
        pass
    try:
        m["uptime_sec"] = time.time() - psutil.boot_time()
    except Exception:
        pass

    return m