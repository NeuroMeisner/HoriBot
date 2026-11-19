"""
Ночной/выходной/праздничный режим питания и статус главного меню.

Праздники: библиотека holidays (RU) + custom_holidays.json (доп. выходные)
+ custom_working_days.json (рабочие субботы/переносы).

Логика (самовосстанавливающийся цикл, каждые CHECK_SEC секунд):
- рабочий день, 08:00–23:00            -> online
- рабочий день, 23:00–08:00            -> night  (systemctl suspend),
  если следующий день рабочий
- день перед выходным/праздником, 23:00 -> weekend (systemctl poweroff)
  + RTC-будильник (rtcwake) на 07:50 следующего рабочего дня
- выходной/праздник                     -> weekend весь день
- рабочий день до 08:00 после выходных  -> weekend (машина ещё выключена)

Если машина проснулась/включилась не вовремя: меню приводится к офлайну
и через GRACE_SEC секунд машина снова гасится (кроме окна 15 минут
перед плановым включением).
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, date as ddate

import config

try:
    import holidays as _holidays_lib
except ImportError:
    _holidays_lib = None
    logging.getLogger(__name__).warning(
        "Библиотека holidays не установлена — учитываются только "
        "custom_holidays.json и выходные сб/вс."
    )

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(PROJECT_DIR, "server_state")
POWER_STATE_FILE = os.path.join(STATE_DIR, "power_state.json")
MENU_MESSAGES_FILE = os.path.join(PROJECT_DIR, "menu_messages.json")

CUSTOM_HOLIDAYS_FILE = os.path.join(PROJECT_DIR, "custom_holidays.json")
CUSTOM_WORKDAYS_FILE = os.path.join(PROJECT_DIR, "custom_working_days.json")

CHECK_SEC = 30
DELIVERY_WAIT_SEC = 5
GRACE_SEC = 120
PRE_ONLINE_SEC = 15 * 60

NIGHT_TEXT = "😴 Мой рабочий день закончился. Записывайтесь завтра с 08:00."


# ============================================
# ПРАЗДНИКИ И ТИПЫ ДНЕЙ
# ============================================

_ru_cache = {}


def _ru_holidays(year: int):
    if _holidays_lib is None:
        return {}
    if year not in _ru_cache:
        _ru_cache[year] = _holidays_lib.country_holidays("RU", years=year)
    return _ru_cache[year]


def _load_date_list(path: str):
    """Читает список дат (строки или объекты {date, name})."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        for item in data:
            if isinstance(item, dict):
                result[ddate.fromisoformat(item["date"])] = item.get("name")
            else:
                result[ddate.fromisoformat(item)] = None
        return result
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Не удалось прочитать {path}: {e}")
        return {}


def holiday_name(d: ddate):
    """Название праздника/корпоративного выходного, если день такой."""
    custom = _load_date_list(CUSTOM_HOLIDAYS_FILE)
    if d in custom:
        return custom[d] or "Корпоративный выходной"
    ru = _ru_holidays(d.year)
    if d in ru:
        return ru.get(d)
    return None


def is_off_day(d: ddate) -> bool:
    """Выходной: сб/вс или праздник, если день не объявлен рабочим вручную."""
    workdays = _load_date_list(CUSTOM_WORKDAYS_FILE)
    if d in workdays:
        return False
    if holiday_name(d) is not None:
        return True
    return d.weekday() >= 5


def next_workday(d: ddate) -> ddate:
    x = d + timedelta(days=1)
    for _ in range(400):
        if not is_off_day(x):
            return x
        x += timedelta(days=1)
    return x


# ============================================
# СОСТОЯНИЕ И ТРЕКЕР МЕНЮ
# ============================================

def load_power_state() -> dict:
    try:
        with open(POWER_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "online", "since": None}


def save_power_state(mode: str):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = POWER_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": mode,
                   "since": datetime.now().isoformat()}, f, ensure_ascii=False)
    os.replace(tmp, POWER_STATE_FILE)


def _load_menu_messages() -> dict:
    try:
        with open(MENU_MESSAGES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_menu_messages(data: dict):
    tmp = MENU_MESSAGES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, MENU_MESSAGES_FILE)


def track_main_menu(chat_id: int, user_id: int, message_id: int):
    data = _load_menu_messages()
    data[str(user_id)] = {"chat_id": chat_id, "message_id": message_id}
    _save_menu_messages(data)


def _forget_menu_message(user_id: str):
    data = _load_menu_messages()
    data.pop(user_id, None)
    _save_menu_messages(data)


# ============================================
# РАСПИСАНИЕ
# ============================================

def target_mode(now: datetime) -> str:
    """Целевое состояние системы по расписанию и праздникам."""
    d = now.date()
    t = now.time()

    if is_off_day(d):
        return "weekend"
    if t >= config.SCHEDULE_WEEKDAY_SLEEP:          # 23:00
        return "weekend" if is_off_day(d + timedelta(days=1)) else "night"
    if t < config.SCHEDULE_WEEKDAY_WAKE:            # 08:00
        return "weekend" if is_off_day(d - timedelta(days=1)) else "night"
    return "online"


def _in_pre_online_window(now: datetime) -> bool:
    """True, если до планового включения (08:00) осталось <= 15 минут."""
    d = now.date()
    if is_off_day(d):
        return False
    delta = (datetime.combine(d, config.SCHEDULE_WEEKDAY_WAKE) - now).total_seconds()
    return 0 <= delta <= PRE_ONLINE_SEC


def offline_text(mode: str, now: datetime) -> str:
    if mode == "night":
        return NIGHT_TEXT
    nxt = next_workday(now.date())
    name = holiday_name(now.date() + timedelta(days=1))
    reason = f" Праздник: {name}." if name else ""
    return (
        "🛑 Бот отключён (выходной или праздник)."
        f"{reason}\nВернусь {nxt:%d.%m} к {config.SCHEDULE_WEEKDAY_WAKE:%H:%M}."
    )


# ============================================
# ДЕЙСТВИЯ С ПИТАНИЕМ
# ============================================

def _run(cmd: list):
    try:
        subprocess.run(cmd, timeout=30)
    except Exception as e:
        logger.error(f"Ошибка выполнения команды {cmd}: {e}")


def _set_rtc_wake():
    """Ставит RTC-будильник на 07:50 следующего рабочего дня.

    Работает из полного выключения на большинстве плат (ACPI RTC wake).
    Режим rtcwake 'no' только взводит будильник, не усыпляя машину.
    """
    if not shutil.which("rtcwake"):
        logger.warning("rtcwake не найден — RTC-будильник не установлен.")
        return
    nw = next_workday(datetime.now().date())
    wake_dt = datetime.combine(nw, config.SCHEDULE_WEEKDAY_WAKE) - timedelta(minutes=10)
    epoch = int(wake_dt.timestamp())
    _run(["sudo", "rtcwake", "-m", "no", "-t", str(epoch)])
    logger.info(f"RTC-будильник взведён на {wake_dt:%d.%m.%Y %H:%M}")


def do_suspend():
    logger.info("Отправляю машину в сон: systemctl suspend")
    _run(["sudo", "systemctl", "suspend"])


def do_poweroff():
    _set_rtc_wake()
    logger.info("Выключаю машину: systemctl poweroff")
    _run(["sudo", "systemctl", "poweroff"])


# ============================================
# РЕДАКТИРОВАНИЕ МЕНЮ
# ============================================

async def _edit_menu(bot, user_id, entry, **kwargs) -> bool:
    """Редактирует сообщение меню с повторами при сетевых сбоях."""
    from aiogram.exceptions import TelegramBadRequest
    for attempt in range(1, 4):
        try:
            await bot.edit_message_text(
                chat_id=entry["chat_id"],
                message_id=entry["message_id"],
                **kwargs
            )
            return True
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not modified" in err:
                return True
            if "not found" in err:
                _forget_menu_message(user_id)
                return False
            logger.warning(f"Меню {user_id}: Telegram отклонил правку: {e}")
            return False
        except Exception as e:
            logger.warning(f"Меню {user_id}: попытка {attempt}/3 не удалась: {e}")
            if attempt < 3:
                await asyncio.sleep(2)
    return False


async def set_offline(bot, mode: str):
    from aiogram.types import InlineKeyboardMarkup
    text = offline_text(mode, datetime.now())
    failed = 0
    for user_id, entry in list(_load_menu_messages().items()):
        ok = await _edit_menu(
            bot, user_id, entry,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        if not ok:
            failed += 1
    if failed:
        logger.error(f"Не отредактировано меню у {failed} пользователей — "
                     f"текст обновится при ближайшем переходе режима")
    logger.info(f"Меню переведены в офлайн-режим: {mode}")


async def set_online(bot):
    from handlers.booking import main_menu_kb, main_menu_text
    failed = 0
    for user_id, entry in list(_load_menu_messages().items()):
        ok = await _edit_menu(
            bot, user_id, entry,
            text=main_menu_text(),
            reply_markup=main_menu_kb(user_id=int(user_id)),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if not ok:
            failed += 1
    if failed:
        logger.error(f"Не восстановлено меню у {failed} пользователей — "
                     f"повторю при следующей проверке цикла")
    logger.info("Меню восстановлены в онлайн-режим")

# ============================================
# ФОНОВАЯ ЗАДАЧА
# ============================================

async def schedule_task(bot):
    if not config.SCHEDULE_ENABLED:
        logger.info("Расписание питания выключено (SCHEDULE_ENABLED=false).")
        return

    logger.info("Задача расписания питания запущена.")
        # Создаём файл состояния сразу, чтобы диагностика не видела "No such file"
    if not os.path.exists(POWER_STATE_FILE):
        save_power_state(target_mode(datetime.now()))
    while True:
        try:
            now = datetime.now()
            target = target_mode(now)
            state = load_power_state()
            mode = state.get("mode", "online")

            if target == "online":
                if mode != "online":
                    logger.info(f"Расписание: переход в online (было {mode})")
                    await set_online(bot)
                    save_power_state("online")
            else:
                if mode == "online" or mode != target:
                    logger.info(f"Расписание: переход в {target} (было {mode})")
                    await set_offline(bot, target)
                    save_power_state(target)
                    await asyncio.sleep(DELIVERY_WAIT_SEC)
                    do_suspend() if target == "night" else do_poweroff()
                else:
                    since = state.get("since")
                    if since:
                        try:
                            awake = (now - datetime.fromisoformat(since)).total_seconds()
                        except ValueError:
                            awake = GRACE_SEC + 1
                    else:
                        awake = GRACE_SEC + 1

                    if awake > GRACE_SEC and not _in_pre_online_window(now):
                        logger.warning(
                            f"Машина работает вне расписания (целевое {target}). "
                            f"Повторно применяю режим."
                        )
                        await set_offline(bot, target)
                        do_suspend() if target == "night" else do_poweroff()
        except Exception as e:
            logger.error(f"Ошибка в задаче расписания питания: {e}")

        await asyncio.sleep(CHECK_SEC)


# ============================================
# CLI: резервное выключение и печать расписания
# ============================================

def _backup_off():
    """Вызывается systemd-таймером в 23:15: гасит машину, если бот не смог."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    now = datetime.now()
    target = target_mode(now)
    state = load_power_state()
    if target != "online" and state.get("mode") == "online":
        logger.warning(f"Резервное срабатывание: бот не переключил режим, "
                       f"применяю {target}")
        if target == "night":
            do_suspend()
        else:
            do_poweroff()
    else:
        logger.info(f"Резервная проверка: всё в порядке (target={target}, "
                    f"state={state.get('mode')})")


def _print_schedule(days: int):
    d = datetime.now().date()
    for i in range(days):
        dd = d + timedelta(days=i)
        off = is_off_day(dd)
        name = holiday_name(dd)
        mark = "🛑 выходной" if off else "🟢 рабочий"
        if name:
            mark += f" — {name}"
        print(f"{dd:%d.%m.%Y} ({dd:%a}) — {mark}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HoriBot power schedule utils")
    parser.add_argument("--backup-off", action="store_true",
                        help="резервная проверка и выключение (для systemd)")
    parser.add_argument("--print-schedule", type=int, default=0, metavar="DAYS",
                        help="распечатать режим на N дней")
    args = parser.parse_args()

    if args.backup_off:
        _backup_off()
    elif args.print_schedule:
        _print_schedule(args.print_schedule)