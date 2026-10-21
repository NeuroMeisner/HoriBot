from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import date
import calendar

MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# ==================== UNICODE-УТИЛИТЫ ====================

def to_bold(text: str) -> str:
    """Преобразовать цифры в жирные Unicode-символы (Mathematical Sans-Serif Bold)."""
    bold_map = {
        '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰',
        '5': '𝟱', '6': '𝟲', '7': '𝟳', '8': '𝟴', '9': '𝟵'
    }
    return ''.join(bold_map.get(c, c) for c in text)


def to_strikethrough(text: str) -> str:
    """Зачеркнуть текст через Unicode combining character (U+0336)."""
    combining_char = '\u0336'  # COMBINING LONG STROKE OVERLAY
    return combining_char.join(text) + combining_char


# ==================== ГЕНЕРАЦИЯ КАЛЕНДАРЯ ====================

def calendar_kb(year: int, month: int, allow_past: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура inline-календаря.
    
    Args:
        year: год
        month: месяц
        allow_past: если True, разрешает выбор прошлых месяцев (но не дальше года назад)
    """
    from datetime import date
    import calendar
    
    today = date.today()
    
    # Название месяца
    month_names = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    
    buttons = []
    
    # Заголовок с навигацией
    nav_row = []
    
    # Кнопка "Назад" (предыдущий месяц)
    if allow_past:
        # Для админской записи — разрешаем навигацию назад (но не дальше года)
        prev_month = month - 1
        prev_year = year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        
        min_date = date(today.year - 1, 1, 1)
        if date(prev_year, prev_month, 1) >= min_date:
            nav_row.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"cal:prev:{prev_year}-{prev_month}"
            ))
        else:
            nav_row.append(InlineKeyboardButton(text=" ", callback_data="cal:noop"))
    else:
        # Для обычной записи — только если не текущий месяц
        if date(year, month, 1) > date(today.year, today.month, 1):
            prev_month = month - 1
            prev_year = year
            if prev_month == 0:
                prev_month = 12
                prev_year -= 1
            nav_row.append(InlineKeyboardButton(
                text="◀",
                callback_data=f"cal:prev:{prev_year}-{prev_month}"
            ))
        else:
            nav_row.append(InlineKeyboardButton(text=" ", callback_data="cal:noop"))
    
    nav_row.append(InlineKeyboardButton(
        text=f"{month_names[month-1]} {year}",
        callback_data="cal:noop"
    ))
    
    # Кнопка "Вперёд" (следующий месяц)
    next_month = month + 1
    next_year = year
    if next_month == 13:
        next_month = 1
        next_year += 1
    
    max_date = date(today.year + 1, today.month, 1)
    if date(next_year, next_month, 1) <= max_date:
        nav_row.append(InlineKeyboardButton(
            text="▶",
            callback_data=f"cal:next:{next_year}-{next_month}"
        ))
    else:
        nav_row.append(InlineKeyboardButton(text=" ", callback_data="cal:noop"))
    
    buttons.append(nav_row)
    
    # Дни недели
    buttons.append([
        InlineKeyboardButton(text="Пн", callback_data="cal:noop"),
        InlineKeyboardButton(text="Вт", callback_data="cal:noop"),
        InlineKeyboardButton(text="Ср", callback_data="cal:noop"),
        InlineKeyboardButton(text="Чт", callback_data="cal:noop"),
        InlineKeyboardButton(text="Пт", callback_data="cal:noop"),
        InlineKeyboardButton(text="Сб", callback_data="cal:noop"),
        InlineKeyboardButton(text="Вс", callback_data="cal:noop"),
    ])
    
    # Дни месяца
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:noop"))
            else:
                current_date = date(year, month, day)
                
                # Проверка на прошедшую дату (только если не allow_past)
                if not allow_past and current_date < today:
                    row.append(InlineKeyboardButton(
                        text=f"·{day}·",
                        callback_data="cal:noop"
                    ))
                else:
                    row.append(InlineKeyboardButton(
                        text=str(day),
                        callback_data=f"cal:day:{current_date.isoformat()}"
                    ))
        buttons.append(row)
    
    # Кнопка отмены
    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cal:cancel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)