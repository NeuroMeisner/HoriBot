"""Хендлеры проверки обновлений и просмотра changelog (только для админов)."""

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
import update_checker

router = Router()


@router.message(Command("update"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_update(msg: Message, bot: Bot):
    """Проверить наличие обновления и предложить установку."""
    status = await msg.answer("🔄 Проверяю обновления...")

    update_info = await update_checker.check_for_updates()

    if not update_info:
        await status.edit_text(
            f"✅ У вас актуальная версия: <code>{update_checker.get_local_version()}</code>",
            parse_mode="HTML"
        )
        return

    changelog = update_checker.format_changelog_for_telegram(update_info["body"])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Установить обновление", callback_data="update:install")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="update:cancel")]
    ])

    await status.edit_text(
        f"🆕 Доступна новая версия: <b>{update_info['tag']}</b>\n"
        f"Текущая версия: <code>{update_checker.get_local_version()}</code>\n\n"
        f"📋 <b>Что нового:</b>\n"
        f"<pre>{changelog}</pre>\n\n"
        f"Установить обновление сейчас?",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.message(Command("changelog"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_changelog(msg: Message):
    """Показать изменения текущей установленной версии (из CHANGELOG.md)."""
    current = update_checker.get_local_version()
    changelog = update_checker.get_current_version_changelog()

    if not changelog:
        await msg.answer(
            f"📋 Текущая версия: <code>{current}</code>\n\n"
            f"Описание изменений для этой версии не найдено в CHANGELOG.md.",
            parse_mode="HTML"
        )
        return

    changelog = update_checker.format_changelog_for_telegram(changelog)

    await msg.answer(
        f"📋 <b>Изменения в версии {current}:</b>\n\n"
        f"<pre>{changelog}</pre>",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "update:install", F.from_user.id.in_(config.ADMIN_IDS))
async def install_update(callback: CallbackQuery, bot: Bot):
    """Установка обновления по нажатию кнопки."""
    await callback.answer()

    status = await callback.message.edit_text("⬇️ Скачиваю и устанавливаю обновление...")

    update_info = await update_checker.check_for_updates()
    if not update_info:
        await status.edit_text("❌ Обновление не найдено (возможно, уже установлено).")
        return

    # Запоминаем changelog до установки (после — версия сменится)
    changelog = update_checker.format_changelog_for_telegram(update_info["body"])

    success = await update_checker.download_and_install(update_info)

    if success:
        await status.edit_text(
            "✅ <b>Обновление установлено!</b>\n\n"
            f"Установлена версия: <code>{update_info['tag']}</code>\n\n"
            "Бот перезапустится автоматически. "
            "Если этого не произошло — запустите его заново.",
            parse_mode="HTML"
        )

        # Рассылаем админам, что изменилось
        change_text = (
            f"🎉 <b>Бот обновлён до версии {update_info['tag']}</b>\n\n"
            f"📋 <b>Что изменилось:</b>\n"
            f"<pre>{changelog}</pre>"
        )
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, change_text, parse_mode="HTML")
            except Exception:
                pass

        update_checker.restart_bot()
    else:
        await status.edit_text(
            "❌ Не удалось установить обновление. "
            "Проверьте логи (bot_log.txt) и попробуйте снова."
        )


@router.callback_query(F.data == "update:cancel", F.from_user.id.in_(config.ADMIN_IDS))
async def cancel_update(callback: CallbackQuery):
    """Отмена установки обновления."""
    await callback.message.edit_text("❌ Установка обновления отменена.")
    await callback.answer()