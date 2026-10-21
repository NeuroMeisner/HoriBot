"""
Модуль проверки и установки обновлений из GitHub.

Логика работы:
1. Сравнивает локальную версию (файл VERSION) с последним релизом на GitHub.
2. Если есть новая версия — возвращает информацию о ней (тег, changelog, ссылку).
3. По команде устанавливает обновление: скачивает zipball релиза, распаковывает
   и заменяет файлы, НЕ трогая защищённые (.env, данные, venv и т.д.).
4. После установки перезапускает процесс.

ВАЖНО: надёжный перезапуск гарантирован, если бот запущен через start_bot.bat
(там цикл автоперезапуска). При ручном `python main.py` может понадобиться
ручной перезапуск.
"""

import os
import sys
import html
import zipfile
import shutil
import logging

import aiohttp

import config

logger = logging.getLogger(__name__)

# Репозиторий на GitHub (владелец/имя)
GITHUB_REPO = "NeuroMeisner/HoriBot"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Корневая папка проекта (где лежит этот файл)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Файл с текущей версией
VERSION_FILE = os.path.join(PROJECT_DIR, "VERSION")

# Файл с историей изменений
CHANGELOG_FILE = os.path.join(PROJECT_DIR, "CHANGELOG.md")

# Файлы и папки, которые НЕЛЬЗЯ заменять при обновлении:
# локальные настройки, данные пользователей, окружение, сгенерированные скрипты
PROTECTED_PATHS = {
    ".env",
    "credentials.json",
    "allowed_users.json",
    "banned_users.json",
    "status.json",
    "bot_log.txt",
    "venv",
    "reports",
    "start_bot.bat",
    "start_bot_hidden.vbs",
    "stop_bot.bat",
    ".git",
    "_update_temp.zip",
    "_update_temp_dir",
}


# ============================================
# ВЕРСИИ
# ============================================

def get_local_version() -> str:
    """Читает текущую версию из файла VERSION."""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip().lstrip("v")
    except FileNotFoundError:
        return "0.0.0"


def parse_version(version_str: str) -> tuple:
    """Разбирает строку версии '1.2.0' или 'v1.2.0' в кортеж (1, 2, 0).

    Невалидные части заменяются нулями, чтобы сравнение не падало.
    """
    version_str = version_str.strip().lstrip("v")
    parts = []
    for part in version_str.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


# ============================================
# ПРОВЕРКА ОБНОВЛЕНИЙ
# ============================================

async def fetch_latest_release() -> dict | None:
    """Запрашивает информацию о последнем релизе на GitHub.

    Возвращает словарь с данными релиза или None при ошибке/отсутствии.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                API_URL,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 404:
                    logger.warning("Релизы в репозитории не найдены (404).")
                    return None
                if resp.status != 200:
                    logger.warning(f"GitHub API вернул статус {resp.status}.")
                    return None
                return await resp.json()
    except Exception as e:
        logger.warning(f"Не удалось проверить обновления: {e}")
        return None


async def check_for_updates() -> dict | None:
    """Проверяет, есть ли новая версия.

    Возвращает словарь с информацией об обновлении:
        {"tag", "name", "body", "zipball_url", "url"}
    Или None, если обновления нет или произошла ошибка.
    """
    release = await fetch_latest_release()
    if not release:
        return None

    latest_tag = (release.get("tag_name") or "").lstrip("v")
    local_version = get_local_version()

    if not latest_tag:
        return None

    if parse_version(latest_tag) > parse_version(local_version):
        return {
            "tag": latest_tag,
            "name": release.get("name") or latest_tag,
            "body": release.get("body") or "",
            "zipball_url": release.get("zipball_url"),
            "url": release.get("html_url"),
        }
    return None


# ============================================
# CHANGELOG
# ============================================

def get_current_version_changelog() -> str | None:
    """Читает CHANGELOG.md и возвращает секцию текущей установленной версии.

    Ищет заголовок вида '## [1.3.0]' и возвращает его содержимое
    до следующего заголовка '## ' или конца файла.
    """
    if not os.path.exists(CHANGELOG_FILE):
        return None

    current = get_local_version()

    try:
        with open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Не удалось прочитать CHANGELOG.md: {e}")
        return None

    lines = content.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("##") and current in stripped:
            start_idx = i
            break

    if start_idx is None:
        return None

    section = []
    for line in lines[start_idx + 1:]:
        if line.strip().startswith("## "):
            break
        section.append(line)

    return "\n".join(section).strip() or None


def format_changelog_for_telegram(changelog: str, max_length: int = 3500) -> str:
    """Подготавливает текст changelog для отправки в Telegram.

    Обрезает до лимита сообщения и экранирует HTML-спецсимволы,
    чтобы текст не ломал разметку сообщения.
    """
    if not changelog:
        return "Описание изменений отсутствует."

    if len(changelog) > max_length:
        changelog = changelog[:max_length] + "\n\n… (полный список в CHANGELOG.md)"

    return html.escape(changelog)


# ============================================
# УСТАНОВКА ОБНОВЛЕНИЯ
# ============================================

async def download_and_install(update_info: dict) -> bool:
    """Скачивает архив релиза и устанавливает обновление.

    Защищённые файлы (см. PROTECTED_PATHS) не трогаются.
    Возвращает True при успехе.
    """
    zipball_url = update_info.get("zipball_url")
    if not zipball_url:
        logger.error("В релизе нет ссылки на архив (zipball_url).")
        return False

    temp_zip = os.path.join(PROJECT_DIR, "_update_temp.zip")
    temp_extract = os.path.join(PROJECT_DIR, "_update_temp_dir")

    try:
        # --- Скачивание архива ---
        logger.info(f"Скачивание обновления {update_info['tag']}...")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                zipball_url,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка скачивания: статус {resp.status}")
                    return False
                with open(temp_zip, "wb") as f:
                    f.write(await resp.read())

        # --- Распаковка ---
        logger.info("Распаковка обновления...")
        with zipfile.ZipFile(temp_zip, "r") as zf:
            zf.extractall(temp_extract)

        # Внутри архива одна корневая папка вида Owner-Repo-<hash>/
        inner = os.listdir(temp_extract)
        if len(inner) != 1:
            logger.error(f"Неожиданная структура архива: {inner}")
            return False
        source_root = os.path.join(temp_extract, inner[0])

        # --- Замена файлов с защитой данных ---
        logger.info("Замена файлов проекта...")
        _replace_tree(source_root, PROJECT_DIR)

        logger.info(f"Обновление до версии {update_info['tag']} установлено.")
        return True

    except Exception as e:
        logger.error(f"Ошибка при установке обновления: {e}")
        return False
    finally:
        # --- Уборка временных файлов ---
        try:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Не удалось удалить временные файлы: {e}")


def _replace_tree(src: str, dst: str):
    """Рекурсивно копирует файлы из src в dst, пропуская защищённые пути.

    Защищённые файлы и папки верхнего уровня не трогаются вообще.
    Файлы заменяются атомарно: сначала во временный, затем переименование.
    """
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)

        # Защищённые пути верхнего уровня пропускаем
        if os.path.abspath(dst) == os.path.abspath(PROJECT_DIR) and item in PROTECTED_PATHS:
            logger.info(f"Пропускаю защищённый путь: {item}")
            continue

        if os.path.isdir(s):
            os.makedirs(d, exist_ok=True)
            _replace_tree(s, d)
        else:
            tmp = d + ".tmp"
            shutil.copy2(s, tmp)
            try:
                os.replace(tmp, d)
            except PermissionError:
                logger.warning(f"Файл занят, пропускаю: {item}")
                if os.path.exists(tmp):
                    os.remove(tmp)


# ============================================
# ПЕРЕЗАПУСК И УВЕДОМЛЕНИЯ
# ============================================

def restart_bot():
    """Перезапускает бота после обновления.

    Если бот запущен через start_bot.bat — батник сам поднимет его заново
    по своему циклу. Иначе пробуем заменить процесс через os.execv.
    """
    logger.info("Перезапуск бота после обновления...")
    try:
        os.execv(sys.executable, [sys.executable, os.path.join(PROJECT_DIR, "main.py")])
    except Exception as e:
        logger.error(f"os.execv не сработал: {e}. Завершаю процесс — "
                     "перезапуск выполнит внешний скрипт (start_bot.bat).")
        sys.exit(0)


async def notify_admins(bot, update_info: dict):
    """Отправляет админам уведомление о новой версии с changelog."""
    changelog = format_changelog_for_telegram(update_info["body"])

    text = (
        f"🔄 <b>Доступна новая версия бота!</b>\n\n"
        f"Текущая версия: <code>{get_local_version()}</code>\n"
        f"Новая версия: <code>{update_info['tag']}</code>\n\n"
        f"📋 <b>Что нового:</b>\n"
        f"<pre>{changelog}</pre>\n\n"
        f"Для установки отправьте /update"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа {admin_id}: {e}")


async def maybe_auto_update() -> bool:
    """Проверяет обновления и, если включён AUTO_UPDATE, устанавливает их.

    Возвращает True, если обновление было установлено (нужен перезапуск).
    При AUTO_UPDATE=false только логирует наличие новой версии.
    """
    update_info = await check_for_updates()
    if not update_info:
        return False

    logger.info(
        f"Доступна новая версия: {update_info['tag']} "
        f"(текущая: {get_local_version()})"
    )

    if getattr(config, "AUTO_UPDATE", False):
        logger.info("AUTO_UPDATE включён — устанавливаю автоматически.")
        return await download_and_install(update_info)

    return False