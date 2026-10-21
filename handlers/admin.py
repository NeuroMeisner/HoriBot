from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberUpdated
import auth_manager
import config

router = Router()


# ==================== АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ НОВЫХ УЧАСТНИКОВ ====================

@router.chat_member()  # ✅ Правильное событие для отслеживания участников
async def on_chat_member_updated(event: ChatMemberUpdated, bot: Bot):
    """Обработчик изменения статуса участника в группе."""
    user_id = event.new_chat_member.user.id
    new_status = event.new_chat_member.status
    
    # Если пользователь добавлен в группу
    if new_status in ["member", "administrator", "creator"]:
        cache = await auth_manager.load_allowed_users()
        if user_id not in cache:
            cache.add(user_id)
            await auth_manager.save_allowed_users(cache)
            
            username = event.new_chat_member.user.username or event.new_chat_member.user.first_name
            print(f"✅ Новый участник добавлен в кэш: @{username} (ID: {user_id})")
    
    # Если пользователь удалён из группы
    elif new_status in ["left", "kicked"]:
        cache = await auth_manager.load_allowed_users()
        if user_id in cache:
            cache.remove(user_id)
            await auth_manager.save_allowed_users(cache)
            
            username = event.new_chat_member.user.username or event.new_chat_member.user.first_name
            print(f"🚫 Участник удалён из кэша: @{username} (ID: {user_id})")


# ==================== АДМИНСКИЕ КОМАНДЫ ====================

@router.message(Command("refresh"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_refresh(msg: Message, bot: Bot):
    """Обновить кэш администраторов."""
    updated = await auth_manager.update_cache_from_group(bot)
    if updated:
        await msg.answer("✅ Кэш администраторов обновлён.")
    else:
        await msg.answer("ℹ️ Список не изменился или ошибка загрузки.")


@router.message(Command("unban"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_unban(msg: Message):
    """Разбанить пользователя."""
    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("Использование: /unban <user_id>")
        return
    try:
        uid = int(args[1])
        success = await auth_manager.unban_user(uid)
        if success:
            await msg.answer(f"✅ Пользователь {uid} разбанен.")
        else:
            await msg.answer(f"❌ Пользователь {uid} не найден в бане.")
    except ValueError:
        await msg.answer("❌ Неверный ID.")


@router.message(Command("banlist"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_banlist(msg: Message):
    """Показать список забаненных."""
    banned = await auth_manager.load_banned_users()
    if not banned:
        await msg.answer("📭 Нет забаненных пользователей.")
    else:
        text = "🚫 <b>Забаненные:</b>\n" + "\n".join([str(u) for u in banned])
        await msg.answer(text, parse_mode="HTML")