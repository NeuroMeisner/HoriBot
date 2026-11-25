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
import logging
from datetime import date as _date

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
    """Собирает все метрики для панели, используя данные термозащиты."""
    m = {
        "cpu_temp": None, "gpu_temp": None, "ram_temp": None, "disk_temp": None,
        "system_temp": None,
        "all_sensors": [], # Полный список для вывода
        "max_temp": None, "max_source": None,
        "cpu_load": None,
        "ram_used": None, "ram_total": None,
        "disk_used": None, "disk_total": None,
        "uptime_sec": None,
        "guard_state": None,
        "guard_updated": None,
    }

    # 1. Читаем статус термозащиты
    guard_data = _read_guard_status()
    
    if guard_data:
        m["guard_state"] = guard_data.get("state")
        m["guard_updated"] = guard_data.get("updated")
        m["max_temp"] = guard_data.get("max_temp")
        m["max_source"] = guard_data.get("max_source")
        
        sensors = guard_data.get("sensors", [])
        m["all_sensors"] = sensors
        
        # Группируем по компонентам для быстрых проверок (если нужно где-то еще)
        # Но для панели мы покажем весь список
        
    # 2. Fallback: если защитник не пишет данные (например, Windows dev), 
    # пробуем psutil напрямую (упрощенно, только CPU/GPU)
    if not m["all_sensors"]:
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            for chip, entries in temps.items():
                for entry in entries:
                    if entry.current:
                        comp = "CPU" if "core" in chip.lower() or "pkg" in chip.lower() else \
                               "GPU" if "gpu" in chip.lower() or "nvidia" in chip.lower() else "System"
                        m["all_sensors"].append({
                            "component": comp,
                            "label": entry.label or chip,
                            "value": entry.current
                        })
            if m["all_sensors"]:
                hottest = max(m["all_sensors"], key=lambda x: x["value"])
                m["max_temp"] = hottest["value"]
                m["max_source"] = f"{hottest['component']} {hottest['label']}"
        except Exception:
            pass

    # 3. Остальные метрики (Load, RAM, Disk, Uptime) - без изменений
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





CONNECTION_STATE_FILE = os.path.join(
    PROJECT_DIR, "server_state", "connection_status.json"
)

_last_ok_write = 0.0  # троттлинг для записи "восстановления"


def read_connection_status() -> dict:
    """Читает журнал связей. Если файла нет — возвращает пустую структуру."""
    try:
        with open(CONNECTION_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            # Миграция со старых версий или инициализация полей
            if "today_breaks" not in data or data.get("break_date") != str(_date.today()):
                data["break_date"] = str(_date.today())
                data["today_breaks"] = 0
            return data
    except Exception:
        return {
            "last_fail_time": None,
            "last_fail_reason": None,
            "last_recover_time": None,
            "break_date": str(_date.today()),
            "today_breaks": 0,
        }


def _save_conn_data(data: dict):
    try:
        os.makedirs(os.path.dirname(CONNECTION_STATE_FILE), exist_ok=True)
        tmp = CONNECTION_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONNECTION_STATE_FILE)
    except OSError as e:
        logger.warning(f"Не удалось сохранить статус связи: {e}")


def record_connection_failure(reason: str):
    """Фиксирует факт потери связи. Вызывается из Handler'а логов."""
    data = read_connection_status()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    # Сброс счетчика, если наступил новый день
    today = str(_date.today())
    if data.get("break_date") != today:
        data["break_date"] = today
        data["today_breaks"] = 0
    
    data["today_breaks"] += 1
    data["last_fail_time"] = now_str
    data["last_fail_reason"] = reason[:200]
    # При новом обрыве сбрасываем время восстановления, 
    # чтобы оно не висело от прошлого эпизода
    data["last_recover_time"] = None 
    
    _save_conn_data(data)
    logger.info(f"[conn] Зафиксирован обрыв связи #{data['today_breaks']} за сегодня: {reason}")


def record_connection_recovery():
    """Фиксирует восстановление связи. Троттлируется (не чаще раза в 60 сек)."""
    global _last_ok_write
    now_ts = time.time()
    
    # Если с момента последней записи прошло меньше минуты — пропускаем
    if now_ts - _last_ok_write < 60:
        return
        
    data = read_connection_status()
    
    # Обновляем только если есть незакрытый инцидент (fail без recover)
    if data.get("last_fail_time") and not data.get("last_recover_time"):
        data["last_recover_time"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        _save_conn_data(data)
        logger.info("[conn] Связь восстановлена после обрыва")
        
    _last_ok_write = now_ts


# Маркеры ошибок подключения
CONN_ERROR_MARKERS = (
    "ошибка сети",
    "network error",
    "telegramnetworkerror",
    "telegramservererror",
    "cannot connect",
    "connect call failed",
    "connection error",
    "getupdates",
    "превышен таймаут",
    "server disconnected",
)


class ConnectionWatchHandler(logging.Handler):
    """Ловит ERROR-записи о сети и вызывает record_connection_failure."""

    def emit(self, record):
        try:
            if record.levelno < logging.ERROR:
                return
            msg = (record.getMessage() or "").lower()
            if any(marker in msg for marker in CONN_ERROR_MARKERS):
                record_connection_failure(record.getMessage())
        except Exception:
            pass  # Не роняем логгер