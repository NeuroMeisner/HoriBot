"""
Middleware авторизации, бана и антиспама.

Подключается в main.py к двум типам апдейтов:
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

Порядок проверок для каждого сообщения/нажатия:
1. Бан           — молчаливая блокировка (без ответа).
2. Антиспам      — только для сообщений: лимит сообщений в окне,
                   предупреждение, при повторных нарушениях — автобан.
3. Кэш разрешённых — быстрый путь без запросов к Telegram API.
4. Администраторы  — добавляются в кэш с причиной message:admin_ids.
5. Участие в группе — проверка через API, при успехе добавление в кэш
                   с причиной message:group_member.
6. Иначе         — отказ с пояснением (текстом или alert-окном).
"""

import logging
import time

from aiogram.types import Message, CallbackQuery

import config
import auth_manager

logger = logging.getLogger(__name__)


class AuthMiddleware:
    """Проверка доступа, бана и флуда для сообщений и callback-запросов."""

    # ===== Настройки антиспама =====
    SPAM_WINDOW_SEC = 10        # окно измерения флуда, секунд
    SPAM_LIMIT = 6              # максимум сообщений в окне
    SPAM_WARN_COOLDOWN_SEC = 60 # пауза между повторными предупреждениями
    SPAM_BAN_THRESHOLD = 3      # нарушений подряд до автобана

    # Состояние антиспама (в памяти процесса)
    _history = {}      # user_id -> [метки времени сообщений в окне]
    _violations = {}   # user_id -> счётчик нарушений
    _warned_at = {}    # user_id -> метка последнего предупреждения

    async def __call__(self, handler, event, data):
        # Определяем пользователя и тип события
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        else:
            # Неподдерживаемый тип события — пропускаем без проверок
            return await handler(event, data)

        # Служебные аккаунты (другие боты) не проверяем
        if user is None or user.is_bot:
            return await handler(event, data)

        user_id = user.id
        bot = data["bot"]

        # ============================================
        # 1. БАН — молчаливая блокировка
        # ============================================
        banned = await auth_manager.load_banned_users()
        if user_id in banned:
            logger.debug(f"[auth] пользователь {user_id} забанен — событие отброшено")
            return

        # ============================================
        # 2. АНТИСПАМ (только для текстовых сообщений)
        # ============================================
        if isinstance(event, Message):
            if await self._check_spam(user_id, event):
                return

        # ============================================
        # 3. КЭШ РАЗРЕШЁННЫХ — быстрый путь
        # ============================================
        allowed = await auth_manager.load_allowed_users()
        if user_id in allowed:
            return await handler(event, data)

        # ============================================
        # 4. АДМИНИСТРАТОРЫ — в кэш без проверки группы
        # ============================================
        if user_id in config.ADMIN_IDS:
            await auth_manager.add_to_cache(bot, user_id, reason="message:admin_ids")
            return await handler(event, data)

        # ============================================
        # 5. ПРОВЕРКА УЧАСТИЯ В ГРУППЕ ЧЕРЕЗ API
        # ============================================
        is_member = await auth_manager.is_user_in_group(bot, user_id)
        if is_member:
            await auth_manager.add_to_cache(bot, user_id, reason="message:group_member")
            return await handler(event, data)

        # ============================================
        # 6. ОТКАЗ В ДОСТУПЕ
        # ============================================
        logger.warning(f"[auth] отказ в доступе пользователю {user_id} "
                       f"(@{user.username}): не состоит в группе")
        if isinstance(event, Message):
            try:
                await event.answer(
                    "🚫 <b>У вас нет доступа к этому боту.</b>\n\n"
                    "Бот доступен только участникам рабочего чата Horiba.\n"
                    "Если вы считаете, что это ошибка, — обратитесь к администратору.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"[auth] не удалось отправить отказ: {e}")
        else:
            try:
                await event.answer(
                    "🚫 Нет доступа: вы не состоите в рабочем чате.",
                    show_alert=True,
                )
            except Exception as e:
                logger.warning(f"[auth] не удалось показать alert: {e}")
        return

    # ============================================
    # АНТИСПАМ: ВНУТРЕННЯЯ ЛОГИКА
    # ============================================

    async def _check_spam(self, user_id: int, event: Message) -> bool:
        """Возвращает True, если сообщение нужно отбросить как флуд."""
        now = time.time()

        hist = self._history.setdefault(user_id, [])
        hist.append(now)

        # Держим в списке только метки внутри окна
        while hist and (now - hist[0]) > self.SPAM_WINDOW_SEC:
            hist.pop(0)

        if len(hist) <= self.SPAM_LIMIT:
            # Сообщение в пределах лимита — сбрасываем счётчик нарушений
            self._violations[user_id] = 0
            return False

        # Зафиксировано нарушение
        self._violations[user_id] = self._violations.get(user_id, 0) + 1
        logger.warning(
            f"[antispam] флуд: пользователь {user_id}, "
            f"{len(hist)} сообщений за {self.SPAM_WINDOW_SEC} с, "
            f"нарушение №{self._violations[user_id]}"
        )

        # Предупреждаем не чаще раза в SPAM_WARN_COOLDOWN_SEC
        last_warn = self._warned_at.get(user_id, 0.0)
        if (now - last_warn) > self.SPAM_WARN_COOLDOWN_SEC:
            self._warned_at[user_id] = now
            try:
                await event.answer(
                    "⚠️ <b>Слишком много сообщений.</b>\n\n"
                    "Прекратите флуд: при повторных нарушениях доступ "
                    "к боту будет заблокирован автоматически.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"[antispam] не удалось отправить предупреждение: {e}")

        # Автобан при систематическом флуде
        if self._violations[user_id] >= self.SPAM_BAN_THRESHOLD:
            banned = await auth_manager.load_banned_users()
            if user_id not in banned:
                banned.append(user_id)
                await auth_manager.save_banned_users(banned)
                logger.warning(f"[antispam] пользователь {user_id} "
                               f"забанен автоматически за флуд")
            self._violations[user_id] = 0

        return True