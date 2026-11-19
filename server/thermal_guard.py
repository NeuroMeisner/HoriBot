#!/usr/bin/env python3
"""
HoriBot Thermal Guard — независимая защита сервера от перегрева.

Работает ОТДЕЛЬНО от бота (системный python3, только stdlib), чтобы защита
и уведомление срабатывали даже если бот завис или упал.

Логика:
- Опрашивает датчики (CPU sysfs, GPU nvidia-smi/amdgpu, NVMe hwmon) каждые N сек.
- TEMP_WARN_C  : первое превышение -> уведомление админам, состояние WARNED.
- TEMP_NORM_C  : опускание ниже из WARNED -> уведомление "температура в норме".
- TEMP_CRIT_C  : превышение -> уведомление, ожидание доставки, systemctl poweroff.
- Статус и последний инцидент пишутся в server_state/thermal_status.json
  (из этого файла бот рисует "Параметры сервера").
- При старте, если прошлый сеанс закончился CRITICAL, шлёт напоминание
  проверить причину перегрева.

Режимы отладки:
  --dry-run     не выполнять реальное выключение
  --simulate NN подменить максимальную температуру значением NN
  --once        одна проверка и выход (подразумевает dry-run)
"""

import urllib.error
import urllib.request
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
import glob

# Папка проекта = родитель папки server/
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(PROJECT_DIR, "server_state")
STATUS_FILE = os.path.join(STATE_DIR, "thermal_status.json")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

logger = logging.getLogger("thermal_guard")


# ============================================
# КОНФИГУРАЦИЯ И СЛУЖЕБНЫЕ ФУНКЦИИ
# ============================================

def load_env() -> dict:
    """Простой парсер .env (без сторонних библиотек)."""
    env = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError as e:
        logger.warning(f"Не удалось прочитать .env: {e}")
    return env


def read_status() -> dict | None:
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_status(data: dict):
    """Атомарная запись статус-файла (tmp + rename)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATUS_FILE)


def send_telegram(token: str, chat_ids: list, text: str, retries: int = 3) -> bool:
    """Отправка сообщения админам напрямую через Telegram API (stdlib).

    - при HTTP-ошибке в лог пишется ТОЧНОЕ описание от Telegram;
    - сетевые ошибки и таймауты повторяются до retries раз с растущей паузой;
    - ошибки 4xx не повторяются (бессмысленно).
    """
    if not token or not chat_ids:
        logger.error("Нет BOT_TOKEN или ADMIN_IDS — уведомление не отправлено.")
        return False

    import urllib.error
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = False

    for chat_id in chat_ids:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode("utf-8")

        for attempt in range(1, retries + 1):
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    resp.read()
                ok = True
                break
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    body = "<тело ответа недоступно>"
                logger.error(
                    f"Не удалось отправить уведомление {chat_id} "
                    f"(попытка {attempt}/{retries}): HTTP {e.code}: {body}"
                )
                if 400 <= e.code < 500:
                    break
            except Exception as e:
                logger.error(
                    f"Не удалось отправить уведомление {chat_id} "
                    f"(попытка {attempt}/{retries}): {e}"
                )
            if attempt < retries:
                time.sleep(3 * attempt)

    return ok


import glob

# ============================================
# ЧТЕНИЕ ВСЕХ ДАТЧИКОВ И ОПРЕДЕЛЕНИЕ КОМПОНЕНТОВ
# ============================================

def _classify_component(hw_name: str, path_hint: str = "") -> str:
    """Определяет тип компонента по имени hwmon-драйвера или пути."""
    name_lower = hw_name.lower()
    
    # CPU
    if name_lower in ("coretemp", "k10temp", "zenpower", "x86_pkg_temp", 
                      "cpu_thermal", "soc_thermal"):
        return "CPU"
    
    # GPU
    if name_lower in ("nvidia", "nouveau", "amdgpu", "radeon", "i915", "xe"):
        return "GPU"
        
    # Диски (NVMe/SATA)
    if name_lower.startswith("nvme") or name_lower in ("ata_piix", "ahci"):
        return "Disk"
        
    # Системные/Материнские платы
    if name_lower in ("it8728", "it8721", "w83627dhg", "nct6775", 
                      "asus_atk0110", "acpitz", "pch_skylake", "fam17h_nb_tsc"):
        return "System"
        
    # По умолчанию — неизвестный, но попробуем угадать по пути
    if "gpu" in path_hint.lower():
        return "GPU"
    if "nvme" in path_hint.lower() or "disk" in path_hint.lower():
        return "Disk"
        
    return "Other"


def read_all_sensors() -> dict:
    """
    Читает температуры со всех доступных датчиков sysfs/hwmon.
    
    Возвращает словарь вида:
    {
        "sensors": [
            {"component": "CPU", "label": "Package id 0", "value": 45.0},
            {"component": "GPU", "label": "GeForce RTX 3080", "value": 60.0},
            ...
        ],
        "max_temp": 60.0,
        "max_source": "GPU GeForce RTX 3080"
    }
    """
    sensors_list = []
    
    # 1. Сканируем /sys/class/hwmon
    hw_root = "/sys/class/hwmon"
    if os.path.isdir(hw_root):
        for hw_dir in sorted(os.listdir(hw_root)):
            base = os.path.join(hw_root, hw_dir)
            
            # Получаем имя драйвера
            try:
                with open(os.path.join(base, "name")) as f:
                    driver_name = f.read().strip()
            except OSError:
                continue
                
            comp_type = _classify_component(driver_name, base)
            
            # Если это Other и не похоже на полезное, пропускаем (опционально)
            # Но лучше читать всё, чтобы не пропустить экзотические чипы
            
            # Ищем файлы temp*_input и соответствующие label
            input_files = glob.glob(os.path.join(base, "temp*_input"))
            
            for inp_path in sorted(input_files):
                try:
                    raw_val = int(open(inp_path).read().strip())
                    temp_c = raw_val / 1000.0
                    
                    # Пытаемся получить красивую метку (Label)
                    label_file = inp_path.replace("_input", "_label")
                    label = ""
                    if os.path.exists(label_file):
                        try:
                            label = open(label_file).read().strip()
                        except Exception:
                            pass
                    
                    # Если нет label, используем имя файла как подсказку
                    if not label:
                        fname = os.path.basename(inp_path)
                        # Например: temp1_input -> Core 1? Нет, просто Temp 1
                        label = f"{driver_name} ({fname})"
                        
                    # Для NVIDIA через nvidia-smi мы обработаем отдельно ниже, 
                    # здесь только sysfs данные (часто есть для AMD/NVIDIA nouveau)
                    
                    sensors_list.append({
                        "component": comp_type,
                        "label": label,
                        "value": round(temp_c, 1),
                        "source_path": inp_path
                    })
                except (OSError, ValueError):
                    continue

    # 2. Специальная обработка NVIDIA GPU через nvidia-smi (если есть)
    # Sysfs для NVIDIA часто пуст или дает только базовые данные, 
    # поэтому добавляем точные данные из утилиты
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                for line in out.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        try:
                            idx = parts[0]
                            gpu_name = parts[1]
                            temp_val = float(parts[2])
                            
                            # Проверяем, нет ли уже этого датчика в списке (дублирование)
                            # Обычно hwmon для NVIDIA называет себя 'nvidia'
                            exists = any(s["component"] == "GPU" and s["label"] == gpu_name 
                                         for s in sensors_list)
                            if not exists:
                                sensors_list.append({
                                    "component": "GPU",
                                    "label": gpu_name,
                                    "value": round(temp_val, 1),
                                    "source_path": f"nvidia-smi:{idx}"
                                })
                        except ValueError:
                            continue
        except Exception:
            pass

    # 3. Вычисляем максимум
    max_temp = None
    max_source = None
    
    if sensors_list:
        # Сортируем по убыванию температуры
        sensors_sorted = sorted(sensors_list, key=lambda x: x["value"], reverse=True)
        hottest = sensors_sorted[0]
        max_temp = hottest["value"]
        max_source = f"{hottest['component']} {hottest['label']}"
        
    return {
        "sensors": sensors_list,
        "max_temp": max_temp,
        "max_source": max_source
    }


def format_temps(temps: dict) -> str:
    parts = []
    if temps.get("cpu") is not None:
        parts.append(f"CPU {temps['cpu']:.0f}°C")
    if temps.get("gpu") is not None:
        parts.append(f"GPU {temps['gpu']:.0f}°C")
    for name, val in (temps.get("disks") or {}).items():
        parts.append(f"{name} {val:.0f}°C")
    return ", ".join(parts) if parts else "нет данных"


# ============================================
# ОСНОВНОЙ ЦИКЛ
# ============================================

def poweroff(dry_run: bool):
    """Аварийное выключение машины."""
    if dry_run:
        logger.warning("DRY-RUN: выключение НЕ выполняется.")
        return
    logger.critical("Выполняю аварийное выключение: systemctl poweroff")
    try:
        subprocess.run(["sudo", "systemctl", "poweroff"], timeout=30)
    except Exception as e:
        logger.error(f"Первая попытка poweroff не удалась: {e}")
    time.sleep(15)
    # Если всё ещё живы — повторяем и перестаём молотить цикл
    try:
        subprocess.run(["sudo", "systemctl", "poweroff"], timeout=30)
    except Exception as e:
        logger.error(f"Вторая попытка poweroff не удалась: {e}")
    while True:
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="HoriBot Thermal Guard")
    parser.add_argument("--dry-run", action="store_true",
                        help="не выполнять реальное выключение")
    parser.add_argument("--simulate", type=float, default=None,
                        help="подменить максимальную температуру (для теста)")
    parser.add_argument("--once", action="store_true",
                        help="одна проверка и выход (подразумевает dry-run)")
    args = parser.parse_args()
    if args.once:
        args.dry_run = True

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

    from logging.handlers import TimedRotatingFileHandler

    def _log_namer(default_name: str) -> str:
        head, tail = os.path.split(default_name)
        base, sep, datepart = tail.partition(".log.")
        if sep:
            return os.path.join(head, f"{base}_{datepart}.log")
        return default_name

    _guard_handler = TimedRotatingFileHandler(
        os.path.join(LOGS_DIR, "thermal_guard.log"),
        when="midnight", interval=1, backupCount=30, encoding="utf-8",
    )
    _guard_handler.suffix = "%Y-%m-%d"
    _guard_handler.namer = _log_namer
    _guard_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[_guard_handler, logging.StreamHandler(sys.stdout)],
    )

    env = load_env()
    token = env.get("BOT_TOKEN", "")
    admins = [int(x) for x in env.get("ADMIN_IDS", "").split(",")
              if x.strip().lstrip("-").isdigit()]
    warn = float(env.get("TEMP_WARN_C", "75"))
    crit = float(env.get("TEMP_CRIT_C", "85"))
    norm = float(env.get("TEMP_NORM_C", "65"))
    interval = max(1, int(env.get("TEMP_CHECK_SEC", "5")))
    cooldown = int(env.get("TEMP_COOLDOWN_MIN", "10")) * 60

    logger.info(
        f"Thermal Guard запущен. Пороги: warn={warn}, crit={crit}, "
        f"norm={norm}, интервал={interval}с, dry_run={args.dry_run}"
    )

    # Восстановление состояния из статус-файла: цепочка WARN -> NORM
    # не должна рваться при перезапуске guard, рестарте systemd,
    # перезагрузке сервера или тестах через --once
    prev = read_status()
    state = "NORMAL"
    if prev:
        prev_state = prev.get("state")
        if prev_state == "CRITICAL":
            le = prev.get("last_event") or {}
            send_telegram(token, admins, (
                "🟠 <b>Сервер включён после АВАРИЙНОГО отключения по перегреву.</b>\n\n"
                f"Инцидент: {le.get('time', '?')}, {le.get('temp', '?')}°C.\n"
                "Прежде чем нагружать сервер, проверьте причину перегрева: "
                "пыль, вентиляторы, температуру в помещении."
            ))
            state = "NORMAL"
        elif prev_state == "WARNED":
            state = "WARNED"
            logger.info("Восстановлено состояние из статус-файла: WARNED "
                        "(инцидент перегрева не закрыт)")

    last_event = (prev or {}).get("last_event")
    last_warn_sent = 0.0

    while True:
        try:
            now = datetime.now()
            ts = now.strftime("%d.%m.%Y %H:%M:%S")
            
            # 1. Сбор данных (или симуляция)
            if args.simulate is not None:
                data = {
                    "sensors": [{"component": "SIMULATED", "label": "Test Sensor", "value": args.simulate}],
                    "max_temp": args.simulate,
                    "max_source": "Simulated Test"
                }
            else:
                data = read_all_sensors()
                
            max_temp = data.get("max_temp")
            sensors = data.get("sensors", [])
            
            # Логируем текущее состояние кратко
            if max_temp is not None:
                logger.info(f"Max Temp: {max_temp:.1f}°C from {data.get('max_source')} | Total sensors: {len(sensors)}")
            else:
                logger.warning("No temperature data available.")

            # 2. Сохранение полного статуса для админ-панели
            write_status({
                "updated": ts,
                "state": state, # Текущее состояние защиты (NORMAL/WARNED/CRITICAL)
                "max_temp": max_temp,
                "max_source": data.get("max_source"),
                "sensors": sensors, # Список всех датчиков!
                "last_event": last_event,
                "dry_run": args.dry_run,
                "pid": os.getpid(),
            })

            if max_temp is None:
                await asyncio.sleep(interval)
                continue

            # 3. Логика защиты (сравнение с порогами)
            
            # --- КРИТИЧЕСКИЙ ПЕРЕГРЕВ ---
            if max_temp >= crit:
                msg_parts = [f"🔥 <b>КРИТИЧЕСКИЙ ПЕРЕГРЕВ!</b>",
                             f"Максимум: <b>{max_temp:.1f}°C</b> ({data.get('max_source')})",
                             f"Порог выключения: {crit:.0f}°C\n"]
                # Добавляем топ-3 самых горячих датчика в сообщение
                top_hot = sorted(sensors, key=lambda x: x["value"], reverse=True)[:3]
                for h in top_hot:
                    msg_parts.append(f"   • {h['component']} {h['label']}: {h['value']:.1f}°C")
                msg_parts.append("\nСервер будет ВЫКЛЮЧЕН через несколько секунд.")
                
                send_telegram(token, admins, "\n".join(msg_parts))
                last_event = {"type": "CRIT", "temp": max_temp, "time": ts, "source": data.get("max_source")}
                state = "CRITICAL" # Флаг для лога
                
                logger.critical(f"PANIC SHUTDOWN TRIGGERED: {max_temp}°C")
                
                if args.once:
                    break
                
                time.sleep(3) # Пауза на доставку
                poweroff(args.dry_run)
                if not args.dry_run:
                    break # Машина выключилась, выходим из цикла
                else:
                    state = "WARNED" # Продолжаем наблюдение в dry-run

            # --- ПРЕДУПРЕЖДЕНИЕ ---
            elif max_temp >= warn:
                if state != "WARNED":
                    msg_parts = [f"⚠️ <b>ВНИМАНИЕ: Перегрев!</b>",
                                 f"Максимум: <b>{max_temp:.1f}°C</b> ({data.get('max_source')})",
                                 f"Порог предупреждения: {warn:.0f}°C\n"]
                    top_hot = sorted(sensors, key=lambda x: x["value"], reverse=True)[:3]
                    for h in top_hot:
                        msg_parts.append(f"   • {h['component']} {h['label']}: {h['value']:.1f}°C")
                    msg_parts.append(f"\nАварийное отключение при {crit:.0f}°C.")
                    
                    send_telegram(token, admins, "\n".join(msg_parts))
                    last_event = {"type": "WARN", "temp": max_temp, "time": ts, "source": data.get("max_source")}
                    state = "WARNED"
                    logger.warning(f"WARN triggered: {max_temp}°C")

            # --- ВОЗВРАТ В НОРМУ ---
            else:
                if state == "WARNED" and max_temp < norm:
                    send_telegram(token, admins, (
                        f"✅ <b>Температура в норме.</b>\n"
                        f"Текущий макс: {max_temp:.1f}°C (< {norm:.0f}°C)\n"
                        f"Источник: {data.get('max_source')}"
                    ))
                    last_event = {"type": "NORM", "temp": max_temp, "time": ts}
                    state = "NORMAL"
                    logger.info(f"Temperature normalized: {max_temp}°C")

        except Exception as e:
            logger.error(f"Error in thermal guard loop: {e}", exc_info=True)
            
        await asyncio.sleep(interval)


if __name__ == "__main__":
    main()