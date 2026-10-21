from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import time


def date_choice_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора даты."""
    from datetime import date, timedelta
    
    today = date.today()
    tomorrow = today + timedelta(days=1)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"📅 Сегодня ({today.strftime('%d.%m')})",
                callback_data=f"date:{today.isoformat()}"
            ),
            InlineKeyboardButton(
                text=f"📅 Завтра ({tomorrow.strftime('%d.%m')})",
                callback_data=f"date:{tomorrow.isoformat()}"
            )
        ],
        [
            InlineKeyboardButton(text="📆 Выбрать другую дату", callback_data="date:pick")
        ]
    ])


def time_slots_kb(
    min_time: time = None, 
    busy_intervals: list = None,
    max_end_time: time = None,
    is_end_time: bool = False,
    start_time_for_end: time = None,
    allow_past: bool = False
) -> InlineKeyboardMarkup:
    """Клавиатура с временными слотами.
    
    Args:
        min_time: минимальное доступное время
        busy_intervals: список занятых интервалов [(start, end), ...]
        max_end_time: максимальное время окончания (ограничение следующей записью)
        is_end_time: True если слоты для выбора ВРЕМЕНИ ОКОНЧАНИЯ
        start_time_for_end: время начала записи (для генерации слота 23:59)
        allow_past: если True, разрешает выбор времени в прошлом
    """
    if busy_intervals is None:
        busy_intervals = []
    
    buttons = []
    row = []
    
    # Если начало >= 23:00, добавляем слот 23:59 для окончания
    add_2359_slot = False
    if is_end_time and start_time_for_end and start_time_for_end >= time(23, 0):
        add_2359_slot = True
    
    # Генерируем слоты с 07:00 до 23:30 с шагом 30 минут
    current_hour = 7
    current_minute = 0
    
    while current_hour < 24:
        slot_time = time(current_hour, current_minute)
        slot_str = slot_time.strftime("%H:%M")
        
        # Проверяем, доступен ли слот
        is_available = True
        
        # ✅ ИСПРАВЛЕНИЕ: Разделяем две проверки
        if min_time:
            if is_end_time:
                # Для ОКОНЧАНИЯ: слот должен быть строго больше времени начала
                # Эта проверка выполняется ВСЕГДА, независимо от allow_past
                if slot_time <= min_time:
                    is_available = False
            else:
                # Для НАЧАЛА: проверка на прошлое время
                # Выполняется только если НЕ разрешено прошлое
                if not allow_past and slot_time < min_time:
                    is_available = False
        
        # Проверка максимального времени окончания
        if max_end_time and slot_time > max_end_time:
            is_available = False
        
        # Проверка пересечений с занятыми интервалами
        if is_available:
            if is_end_time:
                # ДЛЯ ОКОНЧАНИЯ: слот — это ТОЧКА
                # Слот скрыт только если он СТРОГО внутри существующей записи
                # Касания (slot_time == busy_start или slot_time == busy_end) разрешены
                for busy_start, busy_end in busy_intervals:
                    if slot_time > busy_start and slot_time < busy_end:
                        is_available = False
                        break
            else:
                # ДЛЯ НАЧАЛА: слот — это начало виртуального интервала [slot, slot+30мин]
                # Проверяем, что новая запись не пересечётся с существующими
                slot_end_minute = current_minute + 30
                slot_end_hour = current_hour
                if slot_end_minute >= 60:
                    slot_end_minute -= 60
                    slot_end_hour += 1
                
                if slot_end_hour < 24:
                    slot_end = time(slot_end_hour, slot_end_minute)
                    
                    for busy_start, busy_end in busy_intervals:
                        # Пересечение: slot_start < busy_end AND slot_end > busy_start
                        if slot_time < busy_end and slot_end > busy_start:
                            is_available = False
                            break
        
        if is_available:
            row.append(InlineKeyboardButton(
                text=slot_str,
                callback_data=f"time:{slot_str}"
            ))
        else:
            # Пустая кнопка для сохранения структуры сетки
            row.append(InlineKeyboardButton(text=" ", callback_data="no_slots"))
        
        # Переход к следующему слоту
        current_minute += 30
        if current_minute >= 60:
            current_minute = 0
            current_hour += 1
        
        # Новая строка каждые 4 кнопки
        if len(row) == 4:
            buttons.append(row)
            row = []
    
    # Добавляем последнюю строку, если она не пустая
    if row:
        buttons.append(row)
    
    # Добавляем слот 23:59, если начало >= 23:00
    if add_2359_slot:
        slot_2359 = time(23, 59)
        is_available = True
        
        # ✅ ИСПРАВЛЕНИЕ: Проверка "окончание > начала" выполняется ВСЕГДА
        # Слот 23:59 используется только для окончания, поэтому проверяем всегда
        if min_time and slot_2359 <= min_time:
            is_available = False
        
        # Проверяем пересечения
        if is_available:
            for busy_start, busy_end in busy_intervals:
                if slot_2359 > busy_start and slot_2359 < busy_end:
                    is_available = False
                    break
        
        if is_available:
            buttons.append([
                InlineKeyboardButton(text="23:59", callback_data="time:23:59")
            ])
    
    # Кнопка "Другое время"
    buttons.append([
        InlineKeyboardButton(text="⏱ Другое время", callback_data="time:other")
    ])
    
    # Кнопка "Отмена" для слотов окончания
    if is_end_time:
        buttons.append([
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def my_bookings_kb(events: list) -> InlineKeyboardMarkup:
    """Клавиатура со списком записей пользователя."""
    buttons = []
    
    for event in events:
        from datetime import datetime
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        
        event_id = event['id']
        text = f"📅 {start.strftime('%d.%m.%Y')} ⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
        
        buttons.append([
            InlineKeyboardButton(text=text, callback_data=f"event_info:{event_id}")
        ])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def event_actions_kb(event_id: str) -> InlineKeyboardMarkup:
    """Клавиатура действий с событием."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 Удалить запись", callback_data=f"delete_event:{event_id}")
        ],
        [
            InlineKeyboardButton(text="📋 Мои записи", callback_data="my_bookings"),
            InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")
        ]
    ])


def confirm_delete_kb(event_id: str) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{event_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="my_bookings")
        ]
    ])