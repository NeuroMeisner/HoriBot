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

# Папка проекта = родитель папки server/
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(PROJECT_DIR, "server_state")
STATUS_FILE = os.path.join(STATE_DIR, "thermal_status.json")
LOG_FILE = os.path.join(STATE_DIR, "thermal_guard.log")
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

    Улучшения по сравнению с первой версией:
    - при HTTP-ошибке в лог пишется ТОЧНОЕ описание от Telegram
      (например, "chat not found" или "bot was blocked by the user");
    - сетевые ошибки и таймауты повторяются до retries раз с растущей паузой;
    - ошибки 4xx (неверные данные) не повторяются — это бессмысленно.
    """
    if not token or not chat_ids:
        logger.error("Нет BOT_TOKEN или ADMIN_IDS — уведомление не отправлено.")
        return False

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
                break  # доставлено — переходим к следующему адресату
            except urllib.error.HTTPError as e:
                # Читаем тело ответа: там точная причина от Telegram
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    body = "<тело ответа недоступно>"
                logger.error(
                    f"Не удалось отправить уведомление {chat_id} "
                    f"(попытка {attempt}/{retries}): HTTP {e.code}: {body}"
                )
                # 4xx — ошибка в данных (чат не найден, блокировка, битый HTML):
                # повторы бессмысленны
                if 400 <= e.code < 500:
                    break
            except Exception as e:
                logger.error(
                    f"Не удалось отправить уведомление {chat_id} "
                    f"(попытка {attempt}/{retries}): {e}"
                )

            # Пауза перед следующей попыткой (только для сетевых ошибок)
            if attempt < retries:
                time.sleep(3 * attempt)

    return ok


# ============================================
# ЧТЕНИЕ ТЕМПЕРАТУР
# ============================================

def read_cpu_temps() -> list:
    """Температуры CPU из sysfs (thermal_zone + hwmon coretemp/k10temp)."""
    vals = []
    tz_root = "/sys/class/thermal"
    if os.path.isdir(tz_root):
        for zone in sorted(os.listdir(tz_root)):
            if not zone.startswith("thermal_zone"):
                continue
            try:
                with open(os.path.join(tz_root, zone, "type")) as f:
                    ztype = f.read().strip()
                with open(os.path.join(tz_root, zone, "temp")) as f:
                    vals.append(int(f.read().strip()) / 1000.0)
            except (OSError, ValueError):
                continue
    hw_root = "/sys/class/hwmon"
    if os.path.isdir(hw_root):
        for hw in sorted(os.listdir(hw_root)):
            base = os.path.join(hw_root, hw)
            try:
                with open(os.path.join(base, "name")) as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name in ("coretemp", "k10temp", "cpu_thermal", "soc_thermal"):
                for fn in os.listdir(base):
                    if fn.startswith("temp") and fn.endswith("_input"):
                        try:
                            with open(os.path.join(base, fn)) as f:
                                vals.append(int(f.read().strip()) / 1000.0)
                        except (OSError, ValueError):
                            pass
    return vals


def read_gpu_temps() -> list:
    """Температуры GPU: nvidia-smi (NVIDIA) и hwmon через drm (AMD)."""
    vals = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        vals.append(float(line))
        except Exception:
            pass
    drm = "/sys/class/drm"
    if os.path.isdir(drm):
        for card in sorted(os.listdir(drm)):
            hwmon_dir = os.path.join(drm, card, "device", "hwmon")
            if not os.path.isdir(hwmon_dir):
                continue
            for hw in os.listdir(hwmon_dir):
                p = os.path.join(hwmon_dir, hw, "temp1_input")
                if os.path.exists(p):
                    try:
                        with open(p) as f:
                            vals.append(int(f.read().strip()) / 1000.0)
                    except (OSError, ValueError):
                        pass
    return vals


def read_disk_temps() -> dict:
    """Температуры NVMe-накопителей из hwmon."""
    temps = {}
    hw_root = "/sys/class/hwmon"
    if os.path.isdir(hw_root):
        for hw in sorted(os.listdir(hw_root)):
            base = os.path.join(hw_root, hw)
            try:
                with open(os.path.join(base, "name")) as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name.startswith("nvme"):
                for fn in os.listdir(base):
                    if fn.startswith("temp") and fn.endswith("_input"):
                        try:
                            with open(os.path.join(base, fn)) as f:
                                temps[name] = int(f.read().strip()) / 1000.0
                        except (OSError, ValueError):
                            pass
    return temps


def collect_temps(simulate: float | None):
    """Собирает температуры по группам и возвращает (temps, max_temp)."""
    if simulate is not None:
        return {"cpu": simulate, "gpu": None, "disks": {}}, simulate

    cpu_vals = read_cpu_temps()
    gpu_vals = read_gpu_temps()
    disks = read_disk_temps()

    temps = {
        "cpu": max(cpu_vals) if cpu_vals else None,
        "gpu": max(gpu_vals) if gpu_vals else None,
        "disks": disks,
    }
    all_vals = [v for v in (temps["cpu"], temps["gpu"]) if v is not None]
    all_vals += list(disks.values())
    max_temp = max(all_vals) if all_vals else None
    return temps, max_temp


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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
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
            # Прошлый сеанс закончился аварийным выключением
            le = prev.get("last_event") or {}
            send_telegram(token, admins, (
                "🟠 <b>Сервер включён после АВАРИЙНОГО отключения по перегреву.</b>\n\n"
                f"Инцидент: {le.get('time', '?')}, {le.get('temp', '?')}°C.\n"
                "Прежде чем нагружать сервер, проверьте причину перегрева: "
                "пыль, вентиляторы, температуру в помещении."
            ))
            state = "NORMAL"
        elif prev_state == "WARNED":
            # Инцидент перегрева ещё не закрыт сообщением о норме
            state = "WARNED"
            logger.info("Восстановлено состояние из статус-файла: WARNED "
                        "(инцидент перегрева не закрыт)")

    last_event = (prev or {}).get("last_event")
    last_warn_sent = 0.0

    while True:
        temps, max_temp = collect_temps(args.simulate)
        ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        if max_temp is None:
            logger.warning("Датчики температуры не найдены — пропускаю цикл.")
            write_status({
                "updated": ts, "state": state, "temps": temps,
                "max_temp": None, "last_event": last_event,
                "dry_run": args.dry_run, "pid": os.getpid(),
            })
            if args.once:
                return
            time.sleep(interval)
            continue

        logger.info(f"Температуры: {format_temps(temps)} (max {max_temp:.0f}°C)")
        now = time.time()

        # --- КРИТИЧЕСКИЙ ПЕРЕГРЕВ ---
        if max_temp >= crit:
            msg = (
                "🔥 <b>КРИТИЧЕСКИЙ ПЕРЕГРЕВ СЕРВЕРА!</b>\n\n"
                f"Максимальная температура: <b>{max_temp:.0f}°C</b> "
                f"(порог выключения {crit:.0f}°C)\n"
                f"Датчики: {format_temps(temps)}\n\n"
                "Сработала система пожарной безопасности: "
                "сервер будет ВЫКЛЮЧЕН через несколько секунд.\n"
                "Включение — вручную после устранения причины."
            )
            send_telegram(token, admins, msg)
            last_event = {"type": "CRIT", "temp": round(max_temp, 1), "time": ts}
            write_status({
                "updated": ts, "state": "CRITICAL", "temps": temps,
                "max_temp": round(max_temp, 1), "last_event": last_event,
                "dry_run": args.dry_run, "pid": os.getpid(),
            })
            logger.critical(f"ПЕРЕГРЕВ {max_temp:.0f}°C >= {crit:.0f}°C")
            if args.once:
                return
            time.sleep(3)  # даём Telegram доставить сообщение
            poweroff(args.dry_run)
            if args.dry_run:
                state = "WARNED"  # продолжаем наблюдение в тесте
            else:
                return

        # --- ПРЕДУПРЕЖДЕНИЕ ---
        elif max_temp >= warn:
            if state == "NORMAL":
                if now - last_warn_sent >= cooldown:
                    send_telegram(token, admins, (
                        "⚠️ <b>ВНИМАНИЕ: перегрев сервера!</b>\n\n"
                        f"Максимальная температура: <b>{max_temp:.0f}°C</b> "
                        f"(порог предупреждения {warn:.0f}°C)\n"
                        f"Датчики: {format_temps(temps)}\n\n"
                        "Сервер продолжает работу, ведётся наблюдение. "
                        f"Аварийное выключение при {crit:.0f}°C."
                    ))
                    last_warn_sent = now
                    last_event = {"type": "WARN", "temp": round(max_temp, 1),
                                  "time": ts}
                state = "WARNED"

        # --- ВОЗВРАТ В НОРМУ ---
        else:
            if state == "WARNED" and max_temp < norm:
                send_telegram(token, admins, (
                    "✅ <b>Температура сервера пришла в норму.</b>\n\n"
                    f"Текущая максимальная: <b>{max_temp:.0f}°C</b> "
                    f"(порог нормы ниже {norm:.0f}°C)\n"
                    f"Датчики: {format_temps(temps)}\n\n"
                    "Инцидент перегрева исчерпан."
                ))
                logger.info(f"Температура вернулась к норме: "
                            f"{max_temp:.0f}°C < {norm:.0f}°C, отправлено уведомление")
                last_event = {"type": "NORM", "temp": round(max_temp, 1),
                              "time": ts}
                state = "NORMAL"

        write_status({
            "updated": ts, "state": state, "temps": temps,
            "max_temp": round(max_temp, 1), "last_event": last_event,
            "dry_run": args.dry_run, "pid": os.getpid(),
        })

        if args.once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()