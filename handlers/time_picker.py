from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from datetime import datetime, time, date, timedelta

from states import BookingStates
from keyboards.time_picker_kb import get_time_adjustment_buttons, format_time_display
from google_calendar import get_busy_intervals, check_availability
import config

router = Router()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def time_to_minutes(t: time) -> int:
    """Преобразовать время в минуты с начала дня."""
    return t.hour * 60 + t.minute


def minutes_to_time(minutes: int) -> time:
    """Преобразовать минуты с начала дня в время."""
    if minutes < 0:
        minutes = 0
    if minutes >= 24 * 60:
        minutes = 24 * 60 - 1  # 23:59
    return time(minutes // 60, minutes % 60)


def round_up_to_next_hour(t: time) -> time:
    """Округляет время вверх до следующего часа."""
    minutes = time_to_minutes(t)
    if minutes % 60 == 0:
        return t
    rounded_minutes = ((minutes // 60) + 1) * 60
    return minutes_to_time(rounded_minutes)


def calculate_default_times(now_dt: datetime, admin_mode: bool = False) -> tuple[time, time]:
    """Вычисляет начальные времена для пикера.
    
    Args:
        now_dt: текущее время
        admin_mode: если True, не ограничивает прошлым временем
    """
    if admin_mode:
        # Для админской записи — начинаем с 09:00
        start_time = time(9, 0)
        end_time = time(10, 0)
        return start_time, end_time
    
    # Для обычной записи
    target_min = now_dt.minute + 1
    rounded_min = ((target_min + 9) // 10) * 10
    if rounded_min >= 60:
        rounded_min = 0
        hour_add = 1
    else:
        hour_add = 0
        
    start_h = now_dt.hour + hour_add
    if start_h >= 24:
        start_h = 23
        rounded_min = 50
        
    start_time = time(start_h, rounded_min)
    
    # Если начало >= 23:00, окончание = 23:59
    if start_time >= time(23, 0):
        end_time = time(23, 59)
    else:
        end_minutes = time_to_minutes(start_time) + 60
        end_time = round_up_to_next_hour(minutes_to_time(end_minutes))
        
        if time_to_minutes(end_time) <= time_to_minutes(start_time):
            end_time = time(23, 59)
        
    return start_time, end_time


# ==================== ХЕНДЛЕРЫ ====================

@router.callback_query(BookingStates.picking_time_start, F.data.startswith("time:start:"))
async def adjust_start_time(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопок +/- для времени начала."""
    parts = callback.data.split(":")
    delta = int(parts[2])
    data = await state.get_data()
    current_start = data["temp_start"]
    
    # ✅ Проверяем, это админская запись или обычная
    admin_mode = data.get("admin_mode", False)
    
    # Вычисляем новое время через минуты
    new_minutes = time_to_minutes(current_start) + delta
    new_time = minutes_to_time(new_minutes)
    
    # Валидация границ
    today = date.today()
    chosen_date = data.get("date", today)
    
    if admin_mode:
        # ✅ Для админской записи — разрешаем любое время от 07:00 до 23:29
        min_allowed_minutes = time_to_minutes(time(7, 0))
        max_allowed_minutes = time_to_minutes(time(23, 29))
    else:
        # Для обычной записи — только будущее время
        if chosen_date == today:
            min_allowed_minutes = time_to_minutes((datetime.now() + timedelta(minutes=1)).time())
        else:
            min_allowed_minutes = time_to_minutes(time(7, 0))
        max_allowed_minutes = time_to_minutes(time(23, 29))
    
    if new_minutes < min_allowed_minutes or new_minutes > max_allowed_minutes:
        await callback.answer("❌ Нельзя выбрать это время", show_alert=True)
        return

    # Автокоррекция окончания
    current_end = data.get("temp_end", time(15, 0))
    current_end_minutes = time_to_minutes(current_end)
    
    # Если новое начало + 10 мин > текущего конца, сдвигаем конец
    if new_minutes + 10 > current_end_minutes:
        end_minutes = new_minutes + 60
        current_end = round_up_to_next_hour(minutes_to_time(end_minutes))
        current_end_minutes = time_to_minutes(current_end)
        
        if current_end_minutes <= new_minutes:
            current_end = time(23, 59)

    await state.update_data(temp_start=new_time, temp_end=current_end)
    
    busy_intervals = get_busy_intervals(chosen_date)
    busy_text = "\n".join([f"• {bs.strftime('%H:%M')} – {be.strftime('%H:%M')}" for bs, be in busy_intervals]) or "<i>Нет занятых интервалов</i>"
    
    # Проверка "А чё так поздно?"
    late_warning = ""
    if new_minutes >= time_to_minutes(time(23, 0)) or current_end_minutes >= time_to_minutes(time(23, 59)):
        late_warning = "⚠️ <i>А чё так поздно?</i>\n\n"
    
    msg_text = (
        f"⏱ <b>Выберите временной интервал</b>\n\n"
        f"{late_warning}"
        f"{format_time_display(new_time.strftime('%H:%M'), current_end.strftime('%H:%M'), True)}\n\n"
        f"🔒 Занятые интервалы на {chosen_date.strftime('%d.%m.%Y')}:\n{busy_text}"
    )
    
    kb = get_time_adjustment_buttons(True)
    await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(BookingStates.picking_time_start, F.data == "time:confirm:start")
async def confirm_start_time(callback: CallbackQuery, state: FSMContext):
    """Подтверждение времени начала и переход к выбору окончания."""
    data = await state.get_data()
    start_time = data["temp_start"]
    end_time = data["temp_end"]
    
    await state.set_state(BookingStates.picking_time_end)
    
    busy_intervals = get_busy_intervals(data["date"])
    busy_text = "\n".join([f"• {bs.strftime('%H:%M')} – {be.strftime('%H:%M')}" for bs, be in busy_intervals]) or "<i>Нет занятых интервалов</i>"
    
    msg_text = (
        f"⏱ <b>Выберите временной интервал</b>\n\n"
        f"{format_time_display(start_time.strftime('%H:%M'), end_time.strftime('%H:%M'), False)}\n\n"
        f"🔒 Занятые интервалы на {data['date'].strftime('%d.%m.%Y')}:\n{busy_text}"
    )
    
    kb = get_time_adjustment_buttons(False)
    await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(BookingStates.picking_time_end, F.data.startswith("time:end:"))
async def adjust_end_time(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопок +/- для времени окончания."""
    parts = callback.data.split(":")
    delta = int(parts[2])
    data = await state.get_data()
    current_end = data["temp_end"]
    start_time = data["temp_start"]
    
    # ✅ Проверяем, это админская запись или обычная
    admin_mode = data.get("admin_mode", False)
    
    # Вычисляем новое время через минуты
    new_minutes = time_to_minutes(current_end) + delta
    new_time = minutes_to_time(new_minutes)
    
    # Валидация
    if new_minutes <= time_to_minutes(start_time):
        await callback.answer("❌ Конец должен быть позже начала", show_alert=True)
        return
        
    if new_minutes >= 24 * 60:
        await callback.answer("❌ Слишком поздно", show_alert=True)
        return
    
    # ✅ Для обычной записи проверяем, что время не в прошлом
    if not admin_mode:
        today = date.today()
        chosen_date = data.get("date", today)
        if chosen_date == today:
            min_allowed_minutes = time_to_minutes((datetime.now() + timedelta(minutes=1)).time())
            if new_minutes < min_allowed_minutes:
                await callback.answer("❌ Нельзя выбрать прошедшее время", show_alert=True)
                return

    await state.update_data(temp_end=new_time)
    
    busy_intervals = get_busy_intervals(data["date"])
    busy_text = "\n".join([f"• {bs.strftime('%H:%M')} – {be.strftime('%H:%M')}" for bs, be in busy_intervals]) or "<i>Нет занятых интервалов</i>"
    
    late_warning = ""
    if new_minutes >= time_to_minutes(time(23, 59)):
        late_warning = "⚠️ <i>А чё так поздно?</i>\n\n"
    
    msg_text = (
        f"⏱ <b>Выберите временной интервал</b>\n\n"
        f"{late_warning}"
        f"{format_time_display(start_time.strftime('%H:%M'), new_time.strftime('%H:%M'), False)}\n\n"
        f"🔒 Занятые интервалы на {data['date'].strftime('%d.%m.%Y')}:\n{busy_text}"
    )
    
    kb = get_time_adjustment_buttons(False)
    await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(BookingStates.picking_time_end, F.data == "time:confirm:end")
async def confirm_end_time(callback: CallbackQuery, state: FSMContext):
    """Подтверждение времени окончания и переход к финальному подтверждению."""
    data = await state.get_data()
    start_time = data["temp_start"]
    end_time = data["temp_end"]
    chosen_date = data["date"]
    
    # Проверка пересечений перед подтверждением
    if not check_availability(chosen_date, start_time, end_time):
        await callback.answer(
            "❌ Выбранное время пересекается с существующей записью!\n"
            "Пожалуйста, выберите другое время.",
            show_alert=True
        )
        return
    
    # Сохраняем финальные значения в основной стейт
    await state.update_data(start_time=start_time, end_time=end_time)
    await state.set_state(BookingStates.confirming)
    
    preview = (
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"📅 Дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n"
        f"⏰ Время: <b>{start_time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}</b>\n\n"
        "Подтвердить запись?"
    )
    
    from keyboards.common_kb import confirm_cancel_kb
    await callback.message.edit_text(preview, reply_markup=confirm_cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(BookingStates.picking_time_end, F.data == "time:back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору времени начала."""
    await state.set_state(BookingStates.picking_time_start)
    data = await state.get_data()
    start_time = data["temp_start"]
    end_time = data["temp_end"]
    
    busy_intervals = get_busy_intervals(data["date"])
    busy_text = "\n".join([f"• {bs.strftime('%H:%M')} – {be.strftime('%H:%M')}" for bs, be in busy_intervals]) or "<i>Нет занятых интервалов</i>"
    
    late_warning = ""
    if time_to_minutes(start_time) >= time_to_minutes(time(23, 0)) or time_to_minutes(end_time) >= time_to_minutes(time(23, 59)):
        late_warning = "⚠️ <i>А чё так поздно?</i>\n\n"
    
    msg_text = (
        f"⏱ <b>Выберите временной интервал</b>\n\n"
        f"{late_warning}"
        f"{format_time_display(start_time.strftime('%H:%M'), end_time.strftime('%H:%M'), True)}\n\n"
        f"🔒 Занятые интервалы на {data['date'].strftime('%d.%m.%Y')}:\n{busy_text}"
    )
    
    kb = get_time_adjustment_buttons(True)
    await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()