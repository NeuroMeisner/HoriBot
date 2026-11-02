from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberUpdated
import auth_manager
import config

router = Router()

import logging; logger = logging.getLogger(__name__)

# ==================== АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ НОВЫХ УЧАСТНИКОВ ====================

@router.chat_member()
async def on_chat_member_update(update: ChatMemberUpdated, bot: Bot):
    """Реагирует на изменения состава группы.

    Защиты:
    1. События обрабатываются ТОЛЬКО из боевой группы (GROUP_CHAT_ID):
       без этого любой, добавивший бота в свой чат, мог бы выдать доступ
       к боту кому угодно (уязвимость повышения прав).
    2. Операция с кэшем выполняется ПЕРВОЙ и не зависит от отправки сообщений.
    3. Каждая отправка в своём try/except: сбой сети не приводит к потере события.
    4. Покрыты все статусы входа/выхода.
    """
    # 0. Отсекаем события из чужих чатов
    if update.chat.id != config.GROUP_CHAT_ID:
        logger.warning(
            f"[chat_member] ПРОИГНОРИРОВАНО событие из чужого чата "
            f"{update.chat.id} (пользователь {update.new_chat_member.user.id}, "
            f"{update.old_chat_member.status} -> {update.new_chat_member.status}). "
            f"Ожидалась группа {config.GROUP_CHAT_ID}."
        )
        return

    user = update.new_chat_member.user
    new_status = update.new_chat_member.status
    old_status = update.old_chat_member.status

    logger.info(f"[chat_member] {user.id} (@{user.username}): "
                f"{old_status} -> {new_status}")

    # 1. СНАЧАЛА кэш — независимо от исхода отправки сообщений
    if new_status in ("member", "administrator", "creator", "restricted"):
        await auth_manager.add_to_cache(
            bot, user.id, reason=f"chat_member:{old_status}->{new_status}"
        )
    elif new_status in ("left", "kicked"):
        await auth_manager.remove_from_cache(
            user.id, reason=f"chat_member:{new_status}"
        )

    # 2. ПОТОМ сообщения, каждое в своём try/except
    if new_status in ("member", "administrator", "creator", "restricted") \
            and old_status in ("left", "kicked"):
        try:
            await bot.send_message(
                update.chat.id,
                f"👋 Добро пожаловать, {user.first_name}! Доступ к боту выдан."
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить приветствие: {e}")
    elif new_status in ("left", "kicked"):
        try:
            await bot.send_message(
                update.chat.id,
                f"👋 {user.first_name} покинул(а) группу. Доступ к боту отозван."
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить прощание: {e}")

# ==================== АДМИНСКИЕ КОМАНДЫ ====================

@router.message(Command("refresh"), F.from_user.id.in_(config.ADMIN_IDS))
async def refresh_cache(msg: Message, bot: Bot):
    """Пересобрать кэш по группе и вычистить посторонних."""
    await auth_manager.update_cache_from_group(bot)
    removed = await auth_manager.prune_stale_allowed_users(bot)
    if removed:
        await msg.answer(
            "✅ Кэш обновлён.\n"
            f"🧹 Удалены не-участники группы: {', '.join(map(str, removed))}"
        )
    else:
        await msg.answer("✅ Кэш обновлён. Посторонних записей не найдено.")

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