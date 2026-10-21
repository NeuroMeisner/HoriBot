from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import time


def format_time_display(start_str: str, end_str: str, editing_start: bool) -> str:
    """Форматирует текст сообщения с выделением редактируемого времени."""
    arrow = " 👈"
    if editing_start:
        start_line = f"<b>Начало записи:</b>  <code>{start_str}</code>{arrow}"
        end_line = f"Окончание записи:   <code>{end_str}</code>"
    else:
        start_line = f"Начало записи:  <code>{start_str}</code>"
        end_line = f"<b>Окончание записи:</b>  <code>{end_str}</code>{arrow}"
    return f"{start_line}\n{end_line}"


def get_time_adjustment_buttons(editing_start: bool) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками +/- времени."""
    prefix = "start" if editing_start else "end"
    
    buttons = [
        [
            InlineKeyboardButton(text="-1 ч", callback_data=f"time:{prefix}:-60"),
            InlineKeyboardButton(text="+1 ч", callback_data=f"time:{prefix}:+60")
        ],
        # ✅ ИСПРАВЛЕНИЕ: Добавлены кнопки +/-30 мин
        [
            InlineKeyboardButton(text="-30 мин", callback_data=f"time:{prefix}:-30"),
            InlineKeyboardButton(text="+30 мин", callback_data=f"time:{prefix}:+30")
        ],
        [
            InlineKeyboardButton(text="-10 мин", callback_data=f"time:{prefix}:-10"),
            InlineKeyboardButton(text="+10 мин", callback_data=f"time:{prefix}:+10")
        ],
        [
            InlineKeyboardButton(text="-5 мин", callback_data=f"time:{prefix}:-5"),
            InlineKeyboardButton(text="+5 мин", callback_data=f"time:{prefix}:+5")
        ],
        [
            InlineKeyboardButton(text="-1 мин", callback_data=f"time:{prefix}:-1"),
            InlineKeyboardButton(text="+1 мин", callback_data=f"time:{prefix}:+1")
        ]
    ]
    
    if editing_start:
        buttons.append([InlineKeyboardButton(text="✅ Подтвердить начало", callback_data="time:confirm:start")])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ Подтвердить конец", callback_data="time:confirm:end")])
        buttons.append([InlineKeyboardButton(text="🔙 Назад к началу", callback_data="time:back_to_start")])
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)