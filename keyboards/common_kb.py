from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def confirm_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения/отмены."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_booking"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_booking")
        ]
    ])
