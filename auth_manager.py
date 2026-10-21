import json
import time
import logging
from datetime import datetime
import aiofiles
import config

logger = logging.getLogger(__name__)

ALLOWED_USERS_FILE = "allowed_users.json"
BANNED_USERS_FILE = "banned_users.json"

# Антиспам настройки
SPAM_LIMIT = 10       # Кол-во сообщений
SPAM_WINDOW = 5       # Окно в секундах
SPAM_COOLDOWN = 3     # Пауза для сброса счетчика


class SpamTracker:
    def __init__(self):
        self.history = {}  # user_id -> list of timestamps

    def check_spam(self, user_id: int) -> bool:
        """Возвращает True, если пользователь заспамил."""
        now = time.time()
        
        # Инициализация или очистка старых записей
        if user_id not in self.history:
            self.history[user_id] = []
        else:
            # Удаляем сообщения старше окна + кулдаун
            cutoff = now - (SPAM_WINDOW + SPAM_COOLDOWN)
            self.history[user_id] = [t for t in self.history[user_id] if t > cutoff]

        # Добавляем текущее сообщение
        self.history[user_id].append(now)

        # Проверка лимита
        return len(self.history[user_id]) >= SPAM_LIMIT


spam_tracker = SpamTracker()


async def load_allowed_users():
    """Загрузить список разрешенных из файла (кэш)."""
    try:
        async with aiofiles.open(ALLOWED_USERS_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            return set(json.loads(content)) if content else set()
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.error(f"Ошибка загрузки allowed_users: {e}")
        return set()


async def save_allowed_users(user_ids: set):
    """Сохранить список в файл."""
    async with aiofiles.open(ALLOWED_USERS_FILE, "w", encoding="utf-8") as f:
        await f.write(json.dumps(list(user_ids)))


async def load_banned_users():
    """Загрузить список забаненных."""
    try:
        async with aiofiles.open(BANNED_USERS_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            return set(json.loads(content)) if content else set()
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.error(f"Ошибка загрузки banned_users: {e}")
        return set()


async def save_banned_users(user_ids: set):
    """Сохранить баны."""
    async with aiofiles.open(BANNED_USERS_FILE, "w", encoding="utf-8") as f:
        await f.write(json.dumps(list(user_ids)))


async def is_user_in_group(bot, user_id: int) -> bool:
    """Проверить, находится ли пользователь в группе (прямой запрос к API)."""
    try:
        member = await bot.get_chat_member(
            chat_id=config.GROUP_CHAT_ID,
            user_id=user_id
        )
        # Разрешаем все статусы активного участия, включая restricted
        allowed_statuses = ["creator", "administrator", "member", "restricted"]
        is_member = member.status in allowed_statuses
        
        if not is_member:
            logger.warning(f"Пользователь {user_id} не в группе: статус={member.status}")
        
        return is_member
    except Exception as e:
        # Логируем точную причину, чтобы было видно, почему отказ
        logger.warning(f"Ошибка проверки пользователя {user_id} в группе: {e}")
        return False


async def add_to_cache(bot, user_id: int):
    """Добавить пользователя в кэш (при первом успешном доступе)."""
    cache = await load_allowed_users()
    if user_id not in cache:
        cache.add(user_id)
        await save_allowed_users(cache)
        logger.debug(f"Пользователь {user_id} добавлен в кэш")


async def update_cache_from_group(bot):
    """Обновить кэш разрешённых пользователей через get_chat_administrators.
    
    Примечание: Полный список участников получить нельзя из-за ограничений API.
    Этот метод обновляет кэш только администраторами. Для проверки обычных
    пользователей используется is_user_in_group().
    """
    try:
        # ✅ Правильный метод: get_chat_administrators
        admins = await bot.get_chat_administrators(chat_id=config.GROUP_CHAT_ID)
        admin_ids = {a.user.id for a in admins}
        
        old_cache = await load_allowed_users()
        new_cache = old_cache | admin_ids
        
        if new_cache != old_cache:
            await save_allowed_users(new_cache)
            logger.info(f"Кэш администраторов обновлён. Всего в кэше: {len(new_cache)}")
            return True
        return False
        
    except Exception as e:
        logger.error(f"Ошибка обновления кэша админов: {e}")
        return False


async def ban_user(bot, user_id: int, username: str):
    """Забанить пользователя и уведомить админов."""
    banned = await load_banned_users()
    if user_id in banned:
        return False
    
    banned.add(user_id)
    await save_banned_users(banned)
    
    log_msg = f"[{datetime.now()}] Пользователь {user_id} (@{username}) забанен за спам."
    logger.info(log_msg)
    
    # Уведомление админам
    admin_text = f"🚨 <b>Антиспам</b>\n\nПользователь @{username} (ID: {user_id}) забанен за спам."
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось отправить админу {admin_id}: {e}")
            
    return True


async def unban_user(user_id: int) -> bool:
    """Разбанить пользователя."""
    banned = await load_banned_users()
    if user_id in banned:
        banned.remove(user_id)
        await save_banned_users(banned)
        return True
    return False


async def get_users_with_names(bot) -> list:
    """Получить список разрешённых пользователей с их именами.
    
    Берёт ID из allowed_users.json и запрашивает актуальные имена через API.
    Боты (включая самого бота) из списка исключаются.
    
    Returns:
        list of dict: [{'id': 123, 'username': 'ivan_petrov', 'display_name': 'Иван'}, ...]
    """
    allowed = await load_allowed_users()
    users = []
    
    for user_id in allowed:
        # ✅ Пропускаем самого бота (быстрая проверка без лишнего запроса)
        if user_id == bot.id:
            continue
        
        try:
            member = await bot.get_chat_member(
                chat_id=config.GROUP_CHAT_ID,
                user_id=user_id
            )
            user = member.user
            
            # ✅ Пропускаем любых ботов в группе
            if user.is_bot:
                continue
            
            # Используем username, если есть, иначе first_name
            display_name = user.username or user.first_name or f"ID:{user_id}"
            users.append({
                'id': user_id,
                'username': user.username,
                'first_name': user.first_name,
                'display_name': display_name
            })
        except Exception as e:
            logger.warning(f"Не удалось получить данные пользователя {user_id}: {e}")
            # Если не удалось получить данные, используем ID
            users.append({
                'id': user_id,
                'username': None,
                'first_name': f"User{user_id}",
                'display_name': f"ID:{user_id}"
            })
    
    # Сортируем по имени
    users.sort(key=lambda x: x['display_name'].lower())
    return users