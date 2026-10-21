from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import auth_manager
import config

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        
        # 1. Проверка бана
        banned = await auth_manager.load_banned_users()
        if user_id in banned:
            if isinstance(event, Message):
                # Баненные получают только одно сообщение при попытке писать
                # Но так как они игнорируются дальше, можно просто вернуть None
                pass 
            return

        # 2. Проверка авторизации
        allowed = await auth_manager.load_allowed_users()
        
        # Админы всегда имеют доступ
        is_admin = user_id in config.ADMIN_IDS
        
        if not is_admin and user_id not in allowed:
            text = "🚫 У вас нет доступа к этому боту.\n\nОбратитесь к коллегам за доступом."
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return

        # 3. Антиспам (только для сообщений)
        if isinstance(event, Message):
            if auth_manager.spam_tracker.check_spam(user_id):
                await auth_manager.ban_user(data['bot'], user_id, event.from_user.username or event.from_user.first_name)
                await event.answer("⛔ Вы забанены за спам.")
                return

        return await handler(event, data)