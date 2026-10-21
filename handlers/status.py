from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from status_manager import get_status, set_status, get_systems_order, add_checked_system
from keyboards.status_kb import status_message_text, status_kb
import config

router = Router()


@router.callback_query(F.data == "menu:status")
async def show_status(callback: CallbackQuery):
    """Показать статус прибора."""
    text = status_message_text()
    kb = status_kb()
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("status:check:"))
async def check_system(callback: CallbackQuery):
    """Отметить систему как проверенную."""
    try:
        idx = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверная система", show_alert=True)
        return
    
    order = get_systems_order()
    
    if idx < 0 or idx >= len(order):
        await callback.answer("❌ Неверная система", show_alert=True)
        return
    
    system = order[idx]
    add_checked_system(system)
    
    # Обновляем сообщение
    text = status_message_text()
    kb = status_kb()
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("status:confirm:"))
async def confirm_status_change(callback: CallbackQuery, bot: Bot):
    """Подтвердить изменение статуса прибора."""
    new_status = callback.data.split(":")[2]
    user = callback.from_user
    username = user.username or user.first_name
    
    # Меняем статус
    set_status(new_status)
    
    # Уведомление в группу
    if new_status == "on":
        notification = (
            f"🟢 <b>Прибор Horiba ВКЛЮЧЁН</b>\n\n"
            f"👤 @{username}\n"
            f"📆 <a href=\"{config.CALENDAR_LINK}\">Открыть календарь</a>"
        )
        success_text = (
            "✅ <b>Статус прибора изменён!</b>\n\n"
            "Прибор Horiba теперь <b>ВКЛЮЧЁН</b>.\n"
            "Уведомление отправлено в групповой чат."
        )
    else:
        notification = (
            f"🔴 <b>Прибор Horiba ВЫКЛЮЧЕН</b>\n\n"
            f"👤 @{username}\n"
            f"📆 <a href=\"{config.CALENDAR_LINK}\">Открыть календарь</a>"
        )
        success_text = (
            "✅ <b>Статус прибора изменён!</b>\n\n"
            "Прибор Horiba теперь <b>ВЫКЛЮЧЕН</b>.\n"
            "Уведомление отправлено в групповой чат."
        )
    
    await bot.send_message(config.GROUP_CHAT_ID, notification, parse_mode="HTML")
    
    # ✅ Показываем подтверждение с кнопкой возврата в меню
    await callback.message.edit_text(
        success_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()