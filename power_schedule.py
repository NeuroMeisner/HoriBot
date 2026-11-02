"""
Ночной/выходной режим питания и статус главного меню.

Самовосстанавливающийся цикл: каждые CHECK_SEC секунд сверяет
«целевое состояние по расписанию» с «фактическим» и приводит систему к цели:
- online   : Пн–Пт 08:00–23:00 — бот работает, главное меню восстановлено;
- night    : Пн–Чт 23:00–08:00 — меню правится на «рабочий день закончился»,
             машина уходит в сон (systemctl suspend);
- weekend  : Пт 23:00 – Пн 08:00 — меню правится на «отключён на выходные»,
             машина выключается (systemctl poweroff); перед выключением
             best-effort ставится RTC-будильник на понедельник (rtcwake).

Если машина проснулась/включилась не вовремя — меню приводится к офлайну
и через GRACE_SEC секунд машина снова усыпляется/выключается
(кроме окна за 15 минут до планового включения, чтобы не гасить её
сразу после раннего пробуждения будильником).

Состояние: server_state/power_state.json.
Трекер сообщений меню: menu_messages.json.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime

import config

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(PROJECT_DIR, "server_state")
POWER_STATE_FILE = os.path.join(STATE_DIR, "power_state.json")
MENU_MESSAGES_FILE = os.path.join(PROJECT_DIR, "menu_messages.json")

CHECK_SEC = 30            # частота сверки расписания
DELIVERY_WAIT_SEC = 5     # пауза на доставку сообщений в Telegram
GRACE_SEC = 120           # сколько даём «проснувшейся не вовремя» машине
PRE_ONLINE_SEC = 15 * 60  # окно перед плановым включением, когда не гасим

NIGHT_TEXT = "😴 Мой рабочий день закончился. Записывайтесь завтра с 08:00."
WEEKEND_TEXT = "🛑 Бот отключён на выходные. Вернусь в понедельник к 08:00."


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
    """Запоминает последнее главное меню пользователя (синхронно)."""
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
    """Целевое состояние системы по расписанию."""
    wd = now.weekday()  # 0 = понедельник
    t = now.time()

    if wd in (5, 6):                      # суббота, воскресенье
        return "weekend"
    if wd == 0:                           # понедельник
        return "online" if t >= config.SCHEDULE_MONDAY_ON else "weekend"
    if wd == 4:                           # пятница
        if t >= config.SCHEDULE_FRIDAY_OFF:
            return "weekend"
        return "online" if t >= config.SCHEDULE_WEEKDAY_WAKE else "night"
    # Пн–Чт
    if t >= config.SCHEDULE_WEEKDAY_SLEEP:
        return "night"
    return "online" if t >= config.SCHEDULE_WEEKDAY_WAKE else "night"


def _in_pre_online_window(now: datetime) -> bool:
    """True, если до планового включения осталось <= PRE_ONLINE_SEC."""
    wd = now.weekday()
    if wd == 0:
        on = config.SCHEDULE_MONDAY_ON
    elif wd <= 4:
        on = config.SCHEDULE_WEEKDAY_WAKE
    else:
        return False
    delta = (datetime.combine(now.date(), on) - now).total_seconds()
    return 0 <= delta <= PRE_ONLINE_SEC


# ============================================
# ДЕЙСТВИЯ С ПИТАНИЕМ (только Linux/systemd)
# ============================================

def _run(cmd: list):
    try:
        subprocess.run(cmd, timeout=30)
    except Exception as e:
        logger.error(f"Ошибка выполнения команды {cmd}: {e}")


def _set_rtc_wake_monday():
    """Best-effort RTC-будильник на понедельник 07:50 (работает не на всех
    платах из полного выключения; если не сработает — BIOS/розетка/вручную)."""
    if not shutil.which("rtcwake"):
        return
    now = datetime.now()
    days_to_monday = (7 - now.weekday()) % 7 or 7
    monday = datetime.combine(
        (now + __import__("datetime").timedelta(days=days_to_monday)).date(),
        config.SCHEDULE_MONDAY_ON
    ) - __import__("datetime").timedelta(minutes=10)
    epoch = int(monday.timestamp())
    _run(["sudo", "rtcwake", "-m", "off", "-t", str(epoch)])
    logger.info(f"RTC-будильник на включение установлен: {monday}")


def do_suspend():
    logger.info("Отправляю машину в сон: systemctl suspend")
    _run(["sudo", "systemctl", "suspend"])


def do_poweroff():
    _set_rtc_wake_monday()
    logger.info("Выключаю машину: systemctl poweroff")
    _run(["sudo", "systemctl", "poweroff"])


# ============================================
# РЕДАКТИРОВАНИЕ МЕНЮ
# ============================================

async def set_offline(bot, mode: str):
    """Правит все отслеживаемые главные меню на офлайн-текст, снимает клавиатуру."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import InlineKeyboardMarkup

    text = NIGHT_TEXT if mode == "night" else WEEKEND_TEXT
    messages = _load_menu_messages()

    for user_id, entry in list(messages.items()):
        try:
            await bot.edit_message_text(
                chat_id=entry["chat_id"],
                message_id=entry["message_id"],
                text=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not modified" in err:
                pass
            elif "not found" in err:
                _forget_menu_message(user_id)
            else:
                logger.warning(f"Не удалось отредактировать меню {user_id}: {e}")
        except Exception as e:
            logger.warning(f"Не удалось отредактировать меню {user_id}: {e}")

    logger.info(f"Меню переведены в офлайн-режим: {mode}")


async def set_online(bot):
    """Возвращает отслеживаемые сообщения к главному меню с клавиатурой."""
    from aiogram.exceptions import TelegramBadRequest
    from handlers.booking import main_menu_kb, main_menu_text

    messages = _load_menu_messages()

    for user_id, entry in list(messages.items()):
        try:
            await bot.edit_message_text(
                chat_id=entry["chat_id"],
                message_id=entry["message_id"],
                text=main_menu_text(),
                reply_markup=main_menu_kb(user_id=int(user_id)),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not modified" in err:
                pass
            elif "not found" in err:
                _forget_menu_message(user_id)
            else:
                logger.warning(f"Не удалось восстановить меню {user_id}: {e}")
        except Exception as e:
            logger.warning(f"Не удалось восстановить меню {user_id}: {e}")

    logger.info("Меню восстановлены в онлайн-режим")


# ============================================
# ФОНОВАЯ ЗАДАЧА
# ============================================

async def schedule_task(bot):
    """Самовосстанавливающийся цикл приведения системы к целевому состоянию."""
    if not config.SCHEDULE_ENABLED:
        logger.info("Расписание питания выключено (SCHEDULE_ENABLED=false).")
        return

    logger.info("Задача расписания питания запущена.")
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
                    # Машина работает, хотя должна быть выключена/асleep:
                    # проснулась не вовремя. Даём grace и гасим обратно,
                    # кроме окна непосредственно перед плановым включением.
                    since = state.get("since")
                    if since:
                        try:
                            since_dt = datetime.fromisoformat(since)
                            awake = (now - since_dt).total_seconds()
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