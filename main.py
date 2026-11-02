import asyncio
import ctypes
import hashlib
import json
import logging
import os
import sys
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramServerError,
    TelegramConflictError,
)

import config
import tgwsapiproxy
from config import BOT_TOKEN, ADMIN_IDS
from middlewares.auth_middleware import AuthMiddleware
from handlers import booking, status, admin, time_picker, stats, admin_panel, update, server_info
import auth_manager
import logging.handlers
import update_checker
import power_schedule
from reports.stats_report import generate_test_report_html

# ============================================
# КОДИРОВКА КОНСОЛИ И ЛОГОВ
# ============================================

# В скрытом режиме запуска (wscript) кодовая страница консоли Windows
# может не быть UTF-8, и print() с эмодзи падал с UnicodeEncodeError
# ('charmap' codec can't encode character). Принудительно переключаем
# потоки вывода в UTF-8 с заменой не кодируемых символов.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Настройка логирования: вывод в консоль с временной меткой и уровнем
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================
# ЛОГИ: папка logs/, один файл на сутки, хранение 30 дней
# ============================================

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_RETENTION_DAYS = 30  # сколько дней хранить логи (старые удаляются сами)


def _log_namer(default_name: str) -> str:
    """Переименовывает ротированный файл из bot.log.2026-10-22 в bot_2026-10-22.log."""
    head, tail = os.path.split(default_name)
    base, sep, datepart = tail.partition(".log.")
    if sep:
        return os.path.join(head, f"{base}_{datepart}.log")
    return default_name


def _setup_file_logging():
    """Создаёт ротатор логов. При недоступности папки не роняет бота."""
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(LOGS_DIR, "bot.log"),
            when="midnight",       # ротация в полночь
            interval=1,            # каждые сутки
            backupCount=LOG_RETENTION_DAYS,
            encoding="utf-8",
        )
        handler.suffix = "%Y-%m-%d"
        handler.namer = _log_namer
        handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        return handler
    except OSError as e:
        logging.getLogger(__name__).warning(
            f"Не удалось создать лог-файл в {LOGS_DIR}: {e}. "
            f"Остаюсь только на консоли."
        )
        return None


_file_handler = _setup_file_logging()
if _file_handler is not None:
    logging.getLogger().addHandler(_file_handler)
else:
    logging.getLogger(__name__).warning(
        "Файловое логирование отключено: работаем только с консолью."
    )

# ============================================
# ЗАЩИТА ОТ НЕСКОЛЬКИХ КОПИЙ БОТА
# ============================================

# Код завершения, означающий "работает другая копия бота".
# start_bot.bat по нему останавливает цикл перезапуска.
EXIT_CODE_ANOTHER_INSTANCE = 42

# Глобальная ссылка на mutex — должна жить всё время работы процесса,
# иначе сборщик мусора освободит блокировку.
_INSTANCE_MUTEX = None


def acquire_single_instance_mutex():
    """Пытается занять именованный mutex операционной системы.

    Возвращает дескриптор mutex, если экземпляр бота единственный,
    или None, если копия бота уже запущена на этой машине.
    Работает только на Windows; на других ОС просто возвращает заглушку.
    """
    if not hasattr(ctypes, "windll"):
        return object()  # не Windows — блокировку не делаем

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]

    mutex = kernel32.CreateMutexW(None, False, "HoriBot_SingleInstance_Mutex")
    already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

    if already_exists:
        if mutex:
            kernel32.CloseHandle(mutex)
        return None
    return mutex


# ============================================
# КОМАНДЫ БОТА
# ============================================

# Базовые команды, доступные всем пользователям
BOT_COMMANDS = [
    BotCommand(command="start", description="Приветствие"),
    BotCommand(command="book", description="Записаться"),
    BotCommand(command="my", description="Мои записи"),
    BotCommand(command="schedule", description="Расписание"),
    BotCommand(command="usage", description="Статистика за месяц"),
    BotCommand(command="cancel", description="Отмена"),
]

# Расширенные команды, доступные только администраторам
ADMIN_COMMANDS = BOT_COMMANDS + [
    BotCommand(command="update", description="Обновить бота"),
    BotCommand(command="changelog", description="Что нового в текущей версии"),
    BotCommand(command="stats", description="Отчёт за прошлый месяц"),
    BotCommand(command="refresh", description="Обновить список"),
    BotCommand(command="unban", description="Разбанить"),
    BotCommand(command="banlist", description="Список банов"),
]


# ============================================
# УСТАНОВКА КОМАНД (С ЗАЩИТОЙ ОТ FLOOD CONTROL)
# ============================================


# Файл с хэшами последних успешно установленных команд (отдельно по каждому скоупу).
# Лежит рядом с main.py, в репозиторий не попадает (добавлен в .gitignore).
COMMANDS_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".commands_state.json")


def _load_commands_state() -> dict:
    """Читает файл состояния команд. Возвращает пустой словарь, если файла нет или он битый."""
    try:
        with open(COMMANDS_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_commands_state(state: dict):
    """Сохраняет файл состояния команд."""
    try:
        with open(COMMANDS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Не удалось сохранить состояние команд: {e}")


def _commands_hash(commands) -> str:
    """Хэш списка команд — для определения, изменился ли набор."""
    payload = [(c.command, c.description) for c in commands]
    return hashlib.md5(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def set_commands(bot: Bot):
    """Устанавливает команды бота в меню с раздельным кэшем по скоупам.

    - Базовые команды кэшируются одним ключом "base".
    - Каждый админ кэшируется отдельным ключом "admin_<id>".
    Благодаря этому файл создаётся сразу после успеха базовых команд,
    а админ, который ещё не написал /start, не заставляет бот
    повторять уже установленные вызовы (защита от flood control).
    """
    state = _load_commands_state()

    # 1. Базовые команды для всех
    base_hash = _commands_hash(BOT_COMMANDS)
    if state.get("base") == base_hash:
        logger.info("Базовые команды уже установлены — пропускаю (защита от flood control).")
    else:
        try:
            await bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeDefault())
            state["base"] = base_hash
            _save_commands_state(state)
            logger.info("Базовые команды установлены")
        except Exception as e:
            logger.warning(f"Не удалось установить базовые команды: {e}")

    # 2. Расширенные команды для каждого админа — независимо друг от друга
    admin_hash = _commands_hash(ADMIN_COMMANDS)
    for admin_id in ADMIN_IDS:
        key = f"admin_{admin_id}"
        if state.get(key) == admin_hash:
            continue  # этому админу команды уже ставили и набор не менялся
        try:
            await bot.set_my_commands(
                commands=ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
            state[key] = admin_hash
            _save_commands_state(state)
            logger.info(f"Команды установлены для админа {admin_id}")
        except Exception as e:
            logger.warning(
                f"Не удалось установить команды для админа {admin_id}: {e}. "
                f"Админ должен сначала написать боту /start; "
                f"повторная попытка будет при следующем запуске."
            )

# ============================================
# БЕЗОПАСНЫЙ POLLING (С ОБРАБОТКОЙ КОНФЛИКТА)
# ============================================

async def safe_polling(dp: Dispatcher, bot: Bot):
    """Запуск polling с автоматическим переподключением.

    Обрабатывает три класса проблем:
    - 409 Conflict: с этим токеном уже опрашивает Telegram другая копия бота.
      Ждём фиксированные 30 секунд и пробуем снова (вдруг вторую копию закрыли).
    - Сетевые ошибки и сбои серверов Telegram: экспоненциальная задержка 5→60 сек.
    - Прочие непредвиденные ошибки: та же экспоненциальная задержка.
    """
    retry_delay = 5
    max_retry_delay = 60
    conflict_delay = 30

    while True:
        try:
            logger.info("Запуск polling...")
            await dp.start_polling(bot)
            break  # Штатное завершение — выходим
        except TelegramConflictError:
            logger.error(
                "Конфликт getUpdates: с этим токеном уже запущена ДРУГАЯ копия бота! "
                "Закройте лишние экземпляры: stop_bot.bat, Диспетчер задач -> python.exe, "
                "или проверьте, не запущен ли бот на другой машине."
            )
            logger.info(f"Повторная попытка через {conflict_delay} секунд...")
            await asyncio.sleep(conflict_delay)
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.error(f"Ошибка сети: {e}")
            logger.info(f"Переподключение через {retry_delay} секунд...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            logger.info(f"Перезапуск через {retry_delay} секунд...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# ============================================
# ЕЖЕМЕСЯЧНАЯ РАССЫЛКА ОТЧЁТА
# ============================================

async def monthly_report_task(bot: Bot):
    """Фоновая задача: 1-го числа каждого месяца в 13:00 отправлять отчёт.

    Проверяет время каждый час. Флаг last_sent_month защищает
    от повторной отправки в течение одного месяца.
    """
    last_sent_month = None

    while True:
        try:
            now = datetime.now()

            if now.day == 1 and now.hour >= 13:
                month_key = (now.year, now.month)

                if last_sent_month != month_key:
                    logger.info("Запуск автоматической рассылки месячного отчёта...")
                    await stats.send_monthly_report(bot)
                    last_sent_month = month_key

            await asyncio.sleep(3600)

        except Exception as e:
            logger.error(f"Ошибка в задаче monthly_report_task: {e}")
            await asyncio.sleep(3600)


# ============================================
# ЕЖЕДНЕВНАЯ ПРОВЕРКА ОБНОВЛЕНИЙ
# ============================================

async def daily_update_check_task(bot: Bot):
    """Фоновая задача: проверка обновлений раз в сутки.

    Первая проверка — через 60 секунд после запуска (фактически при каждом
    старте бота), далее каждые 24 часа. При AUTO_UPDATE=true устанавливает
    обновление и перезапускает бота, иначе уведомляет админов с changelog.
    """
    if not config.UPDATE_CHECK_ENABLED:
        return

    await asyncio.sleep(60)

    while True:
        try:
            update_info = await update_checker.check_for_updates()
            if update_info:
                if config.AUTO_UPDATE:
                    logger.info(f"Найдено обновление {update_info['tag']} — автоустановка...")
                    if await update_checker.download_and_install(update_info):
                        update_checker.restart_bot()
                        return
                else:
                    await update_checker.notify_admins(bot, update_info)
        except Exception as e:
            logger.error(f"Ошибка в задаче проверки обновлений: {e}")

        await asyncio.sleep(24 * 3600)
        
        # Плановая выверка кэша разрешённых по составу группы
        try:
            removed = await auth_manager.prune_stale_allowed_users(bot)
            if removed:
                logger.warning(f"Плановая выверка кэша: удалены {removed}")
        except Exception as e:
            logger.error(f"Ошибка плановой выверки кэша: {e}")


# ============================================
# ТЕСТОВЫЙ ОТЧЁТ
# ============================================

def ensure_test_report_exists():
    """Создать тестовый отчёт, если его ещё нет."""
    reports_dir = "reports"
    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)

    test_report_path = os.path.join(reports_dir, "test_report.html")
    if not os.path.exists(test_report_path):
        try:
            html_content = generate_test_report_html()
            with open(test_report_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info("Тестовый отчёт создан: reports/test_report.html")
        except Exception as e:
            logger.warning(f"Не удалось создать тестовый отчёт: {e}")


# ============================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================

async def main():
    """Инициализация и запуск бота."""
    global _INSTANCE_MUTEX

    _INSTANCE_MUTEX = acquire_single_instance_mutex()
    if _INSTANCE_MUTEX is None:
        logger.error("Обнаружена уже запущенная копия бота на этой машине.")
        sys.exit(EXIT_CODE_ANOTHER_INSTANCE)

    # ============================================
    # ВСТРОЕННЫЙ ПРОКСИ (явный запуск/остановка)
    # ============================================

    WORKER_HOST = os.getenv("PROXY_WORKER")
    proxy_started = False

    if WORKER_HOST:
        try:
            # Ключевое изменение: используем start_proxy вместо run_proxy
            await tgwsapiproxy.start_proxy(WORKER_HOST)
            
            session = AiohttpSession(proxy=tgwsapiproxy.proxy_url())
            proxy_started = True
            logger.info(f"Встроенный прокси запущен: {tgwsapiproxy.proxy_url()}")
        except Exception as e:
            logger.warning(f"Не удалось запустить прокси: {e}. Прямое подключение.")
            session = AiohttpSession()
    else:
        logger.info("PROXY_WORKER не задан — прямое подключение")
        session = AiohttpSession()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    dp.include_router(admin.router)
    dp.include_router(time_picker.router)
    dp.include_router(status.router)
    dp.include_router(booking.router)
    dp.include_router(stats.router)
    dp.include_router(admin_panel.router)
    dp.include_router(update.router)
    dp.include_router(server_info.router)

    ensure_test_report_exists()

    try:
        await auth_manager.update_cache_from_group(bot)
        removed = await auth_manager.prune_stale_allowed_users(bot)
        if removed:
            logger.warning(f"Выверка кэша: удалены {removed}")
        await set_commands(bot)
    except Exception as e:
        logger.warning(f"Ошибка инициализации: {e}")

    if config.UPDATE_CHECK_ENABLED:
        try:
            if await update_checker.maybe_auto_update():
                update_checker.restart_bot()
                return
        except Exception as e:
            logger.warning(f"Ошибка проверки обновлений: {e}")

    logger.info("HoriBot запущен!")

    monthly_task = asyncio.create_task(monthly_report_task(bot))
    update_task = asyncio.create_task(daily_update_check_task(bot))
    schedule_task_handle = asyncio.create_task(power_schedule.schedule_task(bot))

    try:
        await safe_polling(dp, bot)
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    finally:
        monthly_task.cancel()
        update_task.cancel()
        schedule_task_handle.cancel()
        await session.close()
        if proxy_started:
            try:
                tgwsapiproxy.stop_proxy()
                logger.info("Прокси остановлен")
            except Exception as e:
                logger.warning(f"Ошибка остановки прокси: {e}")

if __name__ == "__main__":
    asyncio.run(main())