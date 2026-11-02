"""
Главный роутер бота: меню, запись, мои записи, админ-запись за другого,
перенос записи, удаление, расписание и отмены действий.
"""

import re
from datetime import datetime, time, date, timedelta

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from states import BookingStates
from google_calendar import (
    create_event, check_availability,
    get_user_events, get_today_events,
    delete_event, get_event_by_id,
    get_busy_intervals
)
from keyboards.booking_kb import (
    date_choice_kb, time_slots_kb,
    my_bookings_kb, event_actions_kb, confirm_delete_kb
)
from keyboards.calendar_kb import calendar_kb
from keyboards.common_kb import cancel_kb, confirm_cancel_kb
from keyboards.time_picker_kb import (
    format_time_display, get_time_adjustment_buttons
)
from handlers.time_picker import (
    calculate_default_times, round_up_to_next_hour,
    time_to_minutes, minutes_to_time
)
from auth_manager import get_users_with_names
import power_schedule
import config

router = Router()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def format_busy_intervals(busy_intervals: list) -> str:
    """Форматировать список занятых интервалов в читаемый вид."""
    if not busy_intervals:
        return "<i>На эту дату нет занятых интервалов.</i>"

    sorted_intervals = sorted(busy_intervals, key=lambda x: x[0])
    lines = []
    for busy_start, busy_end in sorted_intervals:
        lines.append(f"• {busy_start.strftime('%H:%M')} – {busy_end.strftime('%H:%M')}")

    return "\n".join(lines)


def main_menu_kb(user_id: int = None) -> InlineKeyboardMarkup:
    """Клавиатура главного меню."""
    buttons = [
        [
            InlineKeyboardButton(text="📅 Записаться", callback_data="menu:book"),
            InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:my")
        ],
        [
            InlineKeyboardButton(text="📆 Расписание на сегодня", callback_data="menu:schedule")
        ]
    ]

    is_admin = user_id and user_id in config.ADMIN_IDS

    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="🛡️ Админ-панель", callback_data="admin:panel")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_text() -> str:
    """Текст главного меню (используется и при восстановлении после офлайна)."""
    return (
        "👋 <b>Привет!</b>\n\n"
        "Я бот для записи на прибор <b>Horiba</b>.\n\n"
        "📌 <b>Важно:</b> чтобы просматривать расписание по ссылкам из уведомлений, "
        "добавьте календарь себе:\n"
        f"👉 <a href=\"{config.CALENDAR_LINK}\">Добавить календарь</a>\n\n"
        "После этого вы сможете кликать на ссылки «Посмотреть расписание» "
        "и видеть все записи в своём Google Calendar.\n\n"
        "Выберите действие:"
    )


def _track(cb: CallbackQuery):
    """Отметить сообщение главного меню в трекере расписания питания."""
    power_schedule.track_main_menu(
        cb.message.chat.id, cb.from_user.id, cb.message.message_id
    )


def parse_event_owner(event) -> tuple:
    """Возвращает (owner_user_id, owner_username) из описания/названия события."""
    desc = event.get("description", "") or ""
    m_id = re.search(r"User ID:\s*(\d+)", desc)
    m_user = re.search(r"Telegram:\s*@(\S+)", desc)
    owner_id = int(m_id.group(1)) if m_id else None
    owner_username = m_user.group(1) if m_user else None
    if owner_username is None:
        m_sum = re.search(r"@(\S+)", event.get("summary", "") or "")
        owner_username = m_sum.group(1) if m_sum else "unknown"
    return owner_id, owner_username


def format_confirmation_preview(data, start_time, end_time) -> str:
    """Текст подтверждения: обычный или с блоком «было → станет» при переносе."""
    if data.get("reschedule_event_id"):
        old = data["reschedule_old"]
        text = (
            "📋 <b>Подтверждение переноса</b>\n\n"
            f"👤 Запись: <b>@{data['owner_username']}</b>\n"
            f"📅 Было: <b>{old['date']} {old['start']} – {old['end']}</b>\n"
            f"📅 Станет: <b>{data['date'].strftime('%d.%m.%Y')} "
            f"{start_time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}</b>\n\n"
        )
        if data.get("admin_mode"):
            if datetime.combine(data["date"], start_time) < datetime.now():
                text += "⚠️ <i>Новое время в прошлом. Уведомление в группу не будет отправлено.</i>\n\n"
            else:
                text += "✅ <i>Уведомление о переносе будет отправлено в группу.</i>\n\n"
        text += "Подтвердить перенос?"
        return text

    # Обычный режим записи
    preview = "📋 <b>Проверьте данные:</b>\n\n"
    if data.get("admin_mode"):
        preview += f"👤 <b>Запись за:</b> @{data['target_username']}\n"
    preview += (
        f"📅 Дата: <b>{data['date'].strftime('%d.%m.%Y')}</b>\n"
        f"⏰ Время: <b>{start_time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}</b>\n\n"
    )
    if data.get("admin_mode"):
        if datetime.combine(data["date"], start_time) < datetime.now():
            preview += "⚠️ <i>Запись в прошлом. Уведомление в группу не будет отправлено.</i>\n\n"
        else:
            preview += "✅ <i>Запись в будущем. Уведомление будет отправлено в группу.</i>\n\n"
    preview += "Подтвердить запись?"
    return preview


def _time_header(data) -> str:
    """Заголовок сообщений выбора времени: перенос или обычная запись."""
    if data.get("reschedule_event_id"):
        return f"📝 <b>Перенос записи @{data['owner_username']}</b>\n"
    if data.get("admin_mode"):
        return f"📝 <b>Внесение записи за @{data['target_username']}</b>\n"
    return ""


# ==================== /START И МЕНЮ ====================

@router.message(Command("start"))
async def cmd_start(msg: Message):
    """Приветственное сообщение с меню и ссылкой на календарь."""
    sent = await msg.answer(
        main_menu_text(),
        reply_markup=main_menu_kb(user_id=msg.from_user.id),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    power_schedule.track_main_menu(sent.chat.id, msg.from_user.id, sent.message_id)


@router.callback_query(F.data == "menu:book")
async def menu_book(callback: CallbackQuery, state: FSMContext):
    """Запуск обычной записи из меню."""
    await state.set_data({"admin_mode": False})
    await state.set_state(BookingStates.choosing_date)
    await callback.message.edit_text(
        "📅 <b>Запись на Horiba</b>\n\nВыберите дату:",
        reply_markup=date_choice_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu:my")
async def menu_my(callback: CallbackQuery):
    """Показать записи из меню."""
    user = callback.from_user
    events = get_user_events(user.id)

    if not events:
        await callback.message.edit_text(
            "📭 У вас нет активных записей",
            reply_markup=main_menu_kb(user_id=user.id)
        )
        _track(callback)
        await callback.answer()
        return

    text = "📋 <b>Ваши записи:</b>\n\n"
    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        text += f"📅 {start.strftime('%d.%m.%Y')} ⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"

    text += "\nНажмите на запись для управления"

    await callback.message.edit_text(
        text,
        reply_markup=my_bookings_kb(events),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu:schedule")
async def menu_schedule(callback: CallbackQuery):
    """Показать расписание из меню."""
    events = get_today_events()

    if not events:
        await callback.message.edit_text(
            "📭 На сегодня записей нет",
            reply_markup=main_menu_kb(user_id=callback.from_user.id)
        )
        _track(callback)
        await callback.answer()
        return

    text = f"📅 <b>Расписание на {datetime.now().strftime('%d.%m.%Y')}:</b>\n\n"

    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        summary = event.get('summary', 'Horiba')

        text += f"⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"
        text += f"   {summary}\n\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu:back")
async def menu_back(callback: CallbackQuery):
    """Возврат в главное меню."""
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu_kb(user_id=callback.from_user.id),
        parse_mode="HTML"
    )
    _track(callback)
    await callback.answer()


@router.callback_query(F.data == "no_slots")
async def no_slots_handler(callback: CallbackQuery):
    """Обработка кнопки 'Нет доступных слотов'."""
    await callback.answer(
        "⚠️ На выбранную дату нет свободных слотов.\n"
        "Пожалуйста, выберите другой день или время.",
        show_alert=True
    )


# ==================== АДМИНСКАЯ ЗАПИСЬ ЗА ДРУГОГО ====================

@router.callback_query(F.data == "admin:booking:start",
                       F.from_user.id.in_(config.ADMIN_IDS))
async def start_admin_booking(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Начать процесс админской записи — показать список пользователей."""
    users = await get_users_with_names(bot)

    if not users:
        await callback.answer("❌ Нет доступных пользователей", show_alert=True)
        return

    await state.set_data({"users_list": users, "admin_mode": True})
    await state.set_state(BookingStates.choosing_user)

    buttons = []
    for user in users:
        buttons.append([
            InlineKeyboardButton(
                text=f"@{user['display_name']}",
                callback_data=f"admin:booking:user:{user['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:panel")
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "📝 <b>Внесение записи за другого сотрудника</b>\n\n"
        "Выберите пользователя:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(BookingStates.choosing_user,
                       F.data.startswith("admin:booking:user:"))
async def select_user(callback: CallbackQuery, state: FSMContext):
    """Выбрать пользователя для админской записи."""
    user_id = int(callback.data.split(":")[3])
    data = await state.get_data()
    users = data.get("users_list", [])

    target_user = None
    for user in users:
        if user['id'] == user_id:
            target_user = user
            break

    if not target_user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    await state.update_data(
        target_user_id=user_id,
        target_username=target_user['display_name']
    )

    await state.set_state(BookingStates.choosing_date)

    today = date.today()

    await callback.message.edit_text(
        f"📝 <b>Внесение записи за @{target_user['display_name']}</b>\n\n"
        "📅 Выберите дату (можно выбрать любую дату, включая прошлую):",
        reply_markup=calendar_kb(today.year, today.month, allow_past=True),
        parse_mode="HTML"
    )
    await callback.answer()


# ==================== СОЗДАНИЕ ЗАПИСИ (КОМАНДА) ====================

@router.message(Command("book"))
async def start_booking(msg: Message, state: FSMContext):
    """Начать процесс записи (по команде)."""
    await state.set_data({"admin_mode": False})
    await state.set_state(BookingStates.choosing_date)
    await msg.answer(
        "📅 <b>Запись на Horiba</b>\n\nВыберите дату:",
        reply_markup=date_choice_kb(),
        parse_mode="HTML"
    )


# ==================== ВЫБОР ДАТЫ ====================

@router.callback_query(BookingStates.choosing_date, F.data.startswith("date:"))
async def process_date(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора даты (сегодня/завтра/другая)."""
    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    exclude = data.get("reschedule_event_id")

    date_str = callback.data.split(":", 1)[1]

    if date_str == "pick":
        today = date.today()
        await callback.message.edit_text(
            "📅 <b>Выберите дату в календаре:</b>",
            reply_markup=calendar_kb(today.year, today.month, allow_past=admin_mode),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    try:
        chosen_date = date.fromisoformat(date_str)
    except ValueError:
        await callback.answer("❌ Неверная дата", show_alert=True)
        return

    await state.update_data(date=chosen_date)

    busy_intervals = get_busy_intervals(chosen_date, exclude_event_id=exclude)

    if chosen_date == date.today() and not admin_mode:
        min_time = (datetime.now() + timedelta(minutes=1)).time()
    else:
        min_time = None

    await callback.message.edit_text(
        f"{_time_header(data)}"
        f"✅ Выбрана дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
        "⏰ Выберите время начала:",
        reply_markup=time_slots_kb(
            min_time=min_time, busy_intervals=busy_intervals, allow_past=admin_mode
        ),
        parse_mode="HTML"
    )
    await state.set_state(BookingStates.choosing_start_time)
    await callback.answer()


@router.message(BookingStates.choosing_date, F.text)
async def process_date_text(msg: Message, state: FSMContext):
    """Обработка ввода даты текстом."""
    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    exclude = data.get("reschedule_event_id")

    try:
        chosen_date = datetime.strptime(msg.text, "%d.%m.%Y").date()
    except ValueError:
        await msg.answer(
            "❌ Неверный формат. Используйте ДД.ММ.ГГГГ",
            reply_markup=cancel_kb()
        )
        return

    if chosen_date < date.today() and not admin_mode:
        await msg.answer(
            "❌ Дата не может быть в прошлом",
            reply_markup=cancel_kb()
        )
        return

    await state.update_data(date=chosen_date)

    busy_intervals = get_busy_intervals(chosen_date, exclude_event_id=exclude)

    if chosen_date == date.today() and not admin_mode:
        min_time = (datetime.now() + timedelta(minutes=1)).time()
    else:
        min_time = None

    await msg.answer(
        f"{_time_header(data)}"
        f"✅ Выбрана дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
        "⏰ Выберите время начала:",
        reply_markup=time_slots_kb(
            min_time=min_time, busy_intervals=busy_intervals, allow_past=admin_mode
        ),
        parse_mode="HTML"
    )
    await state.set_state(BookingStates.choosing_start_time)


# ==================== INLINE-КАЛЕНДАРЬ ====================

@router.callback_query(BookingStates.choosing_date, F.data.startswith("cal:"))
async def process_calendar(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатий в inline-календаре."""
    parts = callback.data.split(":")
    action = parts[1]

    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    exclude = data.get("reschedule_event_id")

    if action == "noop":
        await callback.answer()
        return

    if action == "cancel":
        await callback.message.edit_text(
            "👋 <b>Главное меню</b>\n\nВыберите действие:",
            reply_markup=main_menu_kb(user_id=callback.from_user.id),
            parse_mode="HTML"
        )
        _track(callback)
        await state.clear()
        await callback.answer()
        return

    if action == "day":
        date_str = parts[2]
        try:
            chosen_date = date.fromisoformat(date_str)
        except ValueError:
            await callback.answer("❌ Неверная дата", show_alert=True)
            return

        if chosen_date < date.today() and not admin_mode:
            await callback.answer("❌ Нельзя выбрать прошедшую дату", show_alert=True)
            return

        await state.update_data(date=chosen_date)

        busy_intervals = get_busy_intervals(chosen_date, exclude_event_id=exclude)

        if chosen_date == date.today() and not admin_mode:
            min_time = (datetime.now() + timedelta(minutes=1)).time()
        else:
            min_time = None

        await callback.message.edit_text(
            f"{_time_header(data)}"
            f"✅ Выбрана дата: <b>{chosen_date.strftime('%d.%m.%Y')}</b>\n\n"
            "⏰ Выберите время начала:",
            reply_markup=time_slots_kb(
                min_time=min_time, busy_intervals=busy_intervals, allow_past=admin_mode
            ),
            parse_mode="HTML"
        )
        await state.set_state(BookingStates.choosing_start_time)
        await callback.answer()
        return

    if action in ("prev", "next"):
        year, month = map(int, parts[2].split("-"))
        current = date(year, month, 1)

        if action == "prev":
            new_month = current.month - 1
            new_year = current.year
            if new_month == 0:
                new_month = 12
                new_year -= 1
        else:
            new_month = current.month + 1
            new_year = current.year
            if new_month == 13:
                new_month = 1
                new_year += 1

        today = date.today()

        if admin_mode:
            min_date = date(today.year - 1, 1, 1)
            max_date = date(today.year + 1, 12, 31)
        else:
            min_date = date(today.year, today.month, 1)
            max_date = date(today.year + 1, today.month, 1)

        if date(new_year, new_month, 1) < min_date or date(new_year, new_month, 1) > max_date:
            await callback.answer("Нельзя выбрать этот месяц", show_alert=True)
            return

        await callback.message.edit_reply_markup(
            reply_markup=calendar_kb(new_year, new_month, allow_past=admin_mode)
        )
        await callback.answer()
        return


# ==================== ВЫБОР ВРЕМЕНИ НАЧАЛА (СЛОТЫ) ====================

@router.callback_query(BookingStates.choosing_start_time, F.data.startswith("time:"))
async def process_start_time(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени начала через слоты или 'Другое время'."""
    time_str = callback.data.split(":", 1)[1]

    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    exclude = data.get("reschedule_event_id")

    if time_str == "other":
        chosen_date = data["date"]

        if admin_mode:
            start_t, end_t = time(9, 0), time(10, 0)
        else:
            now_dt = datetime.now()
            if chosen_date == date.today():
                start_t, end_t = calculate_default_times(now_dt, admin_mode=False)
            else:
                start_t = time(7, 0)
                end_t = time(8, 0)

        await state.update_data(temp_start=start_t, temp_end=end_t)
        await state.set_state(BookingStates.picking_time_start)

        busy_intervals = get_busy_intervals(chosen_date, exclude_event_id=exclude)
        busy_text = format_busy_intervals(busy_intervals)

        late_warning = ""
        if start_t >= time(23, 0) or end_t >= time(23, 59):
            late_warning = "⚠️ <i>А чё так поздно?</i>\n\n"

        msg_text = (
            f"{_time_header(data)}"
            f"📅 Дата: {chosen_date.strftime('%d.%m.%Y')}\n\n"
            f"⏱ <b>Выберите временной интервал</b>\n\n"
            f"{late_warning}"
            f"{format_time_display(start_t.strftime('%H:%M'), end_t.strftime('%H:%M'), True)}\n\n"
            f"🔒 Занятые интервалы:\n{busy_text}"
        )

        kb = get_time_adjustment_buttons(True)
        await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return

    h, m = map(int, time_str.split(":"))
    start_time = time(h, m)

    await state.update_data(start_time=start_time)

    busy_intervals = get_busy_intervals(data["date"], exclude_event_id=exclude)

    max_end_time = None
    start_minutes = start_time.hour * 60 + start_time.minute

    for busy_start, busy_end in busy_intervals:
        busy_start_minutes = busy_start.hour * 60 + busy_start.minute

        if busy_start_minutes > start_minutes:
            if max_end_time is None or busy_start < max_end_time:
                max_end_time = busy_start

    await callback.message.edit_text(
        f"{_time_header(data)}"
        f"📅 Дата: {data['date'].strftime('%d.%m.%Y')}\n"
        f"✅ Время начала: <b>{time_str}</b>\n\n"
        "⏰ Выберите время окончания:",
        reply_markup=time_slots_kb(
            min_time=start_time,
            busy_intervals=busy_intervals,
            max_end_time=max_end_time,
            is_end_time=True,
            start_time_for_end=start_time,
            allow_past=admin_mode
        ),
        parse_mode="HTML"
    )
    await state.set_state(BookingStates.choosing_end_time)
    await callback.answer()


# ==================== ВЫБОР ВРЕМЕНИ ОКОНЧАНИЯ (СЛОТЫ) ====================

@router.callback_query(BookingStates.choosing_end_time, F.data.startswith("time:"))
async def process_end_time(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени окончания через слоты или 'Другое время'."""
    time_str = callback.data.split(":", 1)[1]

    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    exclude = data.get("reschedule_event_id")

    if time_str == "other":
        start_time = data["start_time"]
        chosen_date = data["date"]

        start_minutes = start_time.hour * 60 + start_time.minute
        end_minutes = start_minutes + 60

        if end_minutes % 60 == 0:
            end_t = time(end_minutes // 60, 0)
        else:
            end_t = time((end_minutes // 60) + 1, 0)

        if end_t.hour >= 24:
            end_t = time(23, 59)

        end_minutes_final = end_t.hour * 60 + end_t.minute
        if end_minutes_final <= start_minutes:
            end_t = time(23, 59)

        await state.update_data(temp_start=start_time, temp_end=end_t)
        await state.set_state(BookingStates.picking_time_end)

        busy_intervals = get_busy_intervals(chosen_date, exclude_event_id=exclude)
        busy_text = format_busy_intervals(busy_intervals)

        late_warning = ""
        if end_t >= time(23, 59):
            late_warning = "⚠️ <i>А чё так поздно?</i>\n\n"

        msg_text = (
            f"{_time_header(data)}"
            f"📅 Дата: {chosen_date.strftime('%d.%m.%Y')}\n\n"
            f"⏱ <b>Выберите временной интервал</b>\n\n"
            f"{late_warning}"
            f"{format_time_display(start_time.strftime('%H:%M'), end_t.strftime('%H:%M'), False)}\n\n"
            f"🔒 Занятые интервалы:\n{busy_text}"
        )

        kb = get_time_adjustment_buttons(False)
        await callback.message.edit_text(msg_text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return

    h, m = map(int, time_str.split(":"))
    end_time = time(h, m)

    start_time = data["start_time"]

    if end_time <= start_time:
        await callback.answer("❌ Время окончания должно быть позже начала", show_alert=True)
        return

    if not check_availability(data["date"], start_time, end_time,
                              exclude_event_id=exclude):
        await callback.answer("❌ Это время уже занято! Выберите другое.", show_alert=True)
        return

    await state.update_data(end_time=end_time)
    await state.set_state(BookingStates.confirming)

    preview = format_confirmation_preview(data, start_time, end_time)

    await callback.message.edit_text(
        preview,
        reply_markup=confirm_cancel_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


# ==================== ПЕРЕНОС ЗАПИСИ ====================

@router.callback_query(F.data == "reschedule:retry")
async def reschedule_retry(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору даты после конфликта времени при переносе."""
    data = await state.get_data()
    if not data.get("reschedule_event_id"):
        await callback.answer("❌ Сессия переноса устарела", show_alert=True)
        return
    await state.set_state(BookingStates.choosing_date)
    today = date.today()
    await callback.message.edit_text(
        f"📝 <b>Перенос записи @{data['owner_username']}</b>\n\n"
        "📅 Выберите новую дату:",
        reply_markup=calendar_kb(today.year, today.month,
                                 allow_past=data.get("admin_mode", False)),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reschedule:"))
async def start_reschedule(callback: CallbackQuery, state: FSMContext):
    """Начать перенос записи: владелец или админ."""
    event_id = callback.data.split(":", 1)[1]
    event = get_event_by_id(event_id)
    if not event:
        await callback.answer("❌ Запись не найдена", show_alert=True)
        return

    start_dt = datetime.fromisoformat(event['start']['dateTime'])
    end_dt = datetime.fromisoformat(event['end']['dateTime'])
    user = callback.from_user

    owner_id, owner_username = parse_event_owner(event)
    is_owner = (owner_id == user.id)
    is_admin = user.id in config.ADMIN_IDS

    if not (is_owner or is_admin):
        await callback.answer("❌ Переносить можно только свои записи", show_alert=True)
        return

    # Корректное сравнение дат
    if start_dt.tzinfo is not None:
        now = datetime.now(start_dt.tzinfo)
    else:
        now = datetime.now()

    if start_dt < now:
        await callback.answer("❌ Прошедшие записи можно только удалить", show_alert=True)
        return

    admin_mode = is_admin and not is_owner

    await state.set_data({
        "reschedule_event_id": event_id,
        "reschedule_old": {
            "date": start_dt.strftime('%d.%m.%Y'),
            "start": start_dt.strftime('%H:%M'),
            "end": end_dt.strftime('%H:%M'),
        },
        "owner_user_id": owner_id if owner_id is not None else user.id,
        "owner_username": owner_username,
        "admin_mode": admin_mode,
    })
    await state.set_state(BookingStates.choosing_date)

    today = date.today()
    await callback.message.edit_text(
        f"📝 <b>Перенос записи @{owner_username}</b>\n\n"
        f"Было: {start_dt.strftime('%d.%m.%Y %H:%M')} – {end_dt.strftime('%H:%M')}\n\n"
        "📅 Выберите новую дату:",
        reply_markup=calendar_kb(today.year, today.month, allow_past=admin_mode),
        parse_mode="HTML"
    )
    await callback.answer()


# ==================== МОИ ЗАПИСИ: ИНФО, УДАЛЕНИЕ ====================

@router.message(Command("my"))
async def show_my_bookings(msg: Message):
    """Показать мои записи (по команде)."""
    user = msg.from_user
    events = get_user_events(user.id)

    if not events:
        await msg.answer("📭 У вас нет активных записей")
        return

    text = "📋 <b>Ваши записи:</b>\n\n"
    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        text += f"📅 {start.strftime('%d.%m.%Y')} ⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"

    text += "\nНажмите на запись для управления"

    await msg.answer(text, reply_markup=my_bookings_kb(events), parse_mode="HTML")


@router.callback_query(F.data == "my_bookings")
async def callback_my_bookings(callback: CallbackQuery):
    """Обработка кнопки 'Назад к списку'."""
    user = callback.from_user
    events = get_user_events(user.id)

    if not events:
        await callback.message.edit_text(
            "📭 У вас нет активных записей",
            reply_markup=main_menu_kb(user_id=user.id)
        )
        _track(callback)
        await callback.answer()
        return

    text = "📋 <b>Ваши записи:</b>\n\n"
    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        text += f"📅 {start.strftime('%d.%m.%Y')} ⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"

    text += "\nНажмите на запись для управления"

    await callback.message.edit_text(text, reply_markup=my_bookings_kb(events), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("event_info:"))
async def show_event_info(callback: CallbackQuery):
    """Показать информацию о событии."""
    event_id = callback.data.split(":", 1)[1]
    event = get_event_by_id(event_id)

    if not event:
        await callback.answer("❌ Событие не найдено", show_alert=True)
        return

    start = datetime.fromisoformat(event['start']['dateTime'])
    end = datetime.fromisoformat(event['end']['dateTime'])

    # Корректное сравнение: если start aware, сравниваем с aware now
    if start.tzinfo is not None:
        now = datetime.now(start.tzinfo)
    else:
        now = datetime.now()

    allow_reschedule = start >= now

    text = (
        f"📋 <b>Информация о записи:</b>\n\n"
        f"📅 Дата: <b>{start.strftime('%d.%m.%Y')}</b>\n"
        f"⏰ Время: <b>{start.strftime('%H:%M')} – {end.strftime('%H:%M')}</b>\n"
    )

    await callback.message.edit_text(
        text,
        reply_markup=event_actions_kb(event_id, allow_reschedule=allow_reschedule),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_event:"))
async def ask_delete_confirmation(callback: CallbackQuery):
    """Запросить подтверждение удаления."""
    event_id = callback.data.split(":", 1)[1]
    event = get_event_by_id(event_id)

    if not event:
        await callback.answer("❌ Событие не найдено", show_alert=True)
        return

    start = datetime.fromisoformat(event['start']['dateTime'])
    end = datetime.fromisoformat(event['end']['dateTime'])

    text = (
        f"⚠️ <b>Подтвердите удаление:</b>\n\n"
        f"📅 {start.strftime('%d.%m.%Y')}\n"
        f"⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n\n"
        "Вы уверены, что хотите удалить эту запись?"
    )

    await callback.message.edit_text(text, reply_markup=confirm_delete_kb(event_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete:"))
async def confirm_delete_event(callback: CallbackQuery, bot: Bot):
    """Подтверждение и удаление события."""
    event_id = callback.data.split(":", 1)[1]
    event = get_event_by_id(event_id)

    if not event:
        await callback.answer("❌ Событие не найдено", show_alert=True)
        return

    user = callback.from_user
    search_marker = f'User ID: {user.id}'

    description = event.get('description', '')

    if search_marker not in description:
        await callback.answer("❌ Вы можете удалять только свои записи", show_alert=True)
        return

    success = delete_event(event_id)

    if success:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])

        calendar_url = (
            f"https://calendar.google.com/calendar/u/0/r/day/"
            f"{start.year}/{start.month}/{start.day}"
        )

        notification = (
            f"🔴 <b>Запись отменена</b>\n\n"
            f"📆 <a href=\"{calendar_url}\">Посмотреть расписание</a>\n"
            f"👤 @{user.username or user.first_name}\n"
            f"📅 {start.strftime('%d.%m.%Y')}\n"
            f"⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
        )

        await bot.send_message(config.GROUP_CHAT_ID, notification, parse_mode="HTML")

        await callback.message.edit_text(
            "✅ <b>Запись успешно удалена</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:my")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
            ]),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка при удалении записи")

    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """Вернуться в главное меню."""
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu_kb(user_id=callback.from_user.id),
        parse_mode="HTML"
    )
    _track(callback)
    await callback.answer()


# ==================== РАСПИСАНИЕ (КОМАНДА) ====================

@router.message(Command("schedule"))
async def show_today_schedule(msg: Message):
    """Показать расписание на сегодня (по команде)."""
    events = get_today_events()

    if not events:
        await msg.answer("📭 На сегодня записей нет")
        return

    text = f"📅 <b>Расписание на {datetime.now().strftime('%d.%m.%Y')}:</b>\n\n"

    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime'])
        end = datetime.fromisoformat(event['end']['dateTime'])
        summary = event.get('summary', 'Horiba')

        text += f"⏰ {start.strftime('%H:%M')} – {end.strftime('%H:%M')}\n"
        text += f"   {summary}\n\n"

    await msg.answer(text, parse_mode="HTML")


# ==================== ПОДТВЕРЖДЕНИЕ ЗАПИСИ / ПЕРЕНОСА ====================

@router.callback_query(BookingStates.confirming, F.data == "confirm_booking")
async def confirm_booking(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Подтверждение: создание записи или перенос существующей."""
    data = await state.get_data()
    admin_mode = data.get("admin_mode", False)
    user = callback.from_user

    # ---------- РЕЖИМ ПЕРЕНОСА ----------
    resched_id = data.get("reschedule_event_id")
    if resched_id:
        old_event = get_event_by_id(resched_id)
        if not old_event:
            await callback.message.edit_text(
                "❌ Переносимая запись не найдена (возможно, уже удалена).",
                reply_markup=main_menu_kb(user_id=user.id), parse_mode="HTML"
            )
            _track(callback)
            await state.clear()
            await callback.answer()
            return

        # Повторная проверка с исключением старой записи
        if not check_availability(data["date"], data["start_time"], data["end_time"],
                                  exclude_event_id=resched_id):
            await callback.message.edit_text(
                "❌ <b>Новое время уже занято!</b>\n\n"
                "Пока вы выбирали, слот заняли. Выберите другое время.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📅 Выбрать другое время",
                                          callback_data="reschedule:retry")],
                    [InlineKeyboardButton(text="❌ Отмена",
                                          callback_data="cancel_booking")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        old = data["reschedule_old"]
        admin_username = (user.username or user.first_name) if admin_mode else None

        try:
            delete_event(resched_id)
            create_event(
                date=data["date"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                username=data["owner_username"],
                user_id=data["owner_user_id"],
                admin_username=admin_username,
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ Ошибка при переносе: {str(e)}\n\n"
                "Проверьте список записей: старое событие удалено, "
                "новое могло не создаться.",
                reply_markup=main_menu_kb(user_id=user.id), parse_mode="HTML"
            )
            _track(callback)
            await state.clear()
            await callback.answer()
            return

        # Уведомление в группу — только если новое время в будущем
        new_dt = datetime.combine(data["date"], data["start_time"])
        if new_dt >= datetime.now():
            date_obj = data['date']
            calendar_url = (
                f"https://calendar.google.com/calendar/u/0/r/day/"
                f"{date_obj.year}/{date_obj.month}/{date_obj.day}"
            )
            notification = (
                f"✏️ <b>Запись перенесена</b>\n\n"
                f"📆 <a href=\"{calendar_url}\">Посмотреть расписание</a>\n"
                f"👤 @{data['owner_username']}\n"
            )
            if admin_mode:
                notification += "⠀<i>перенесена администратором</i>\n"
            notification += (
                f"📅 Было: {old['date']} {old['start']} – {old['end']}\n"
                f"📅 Стало: {date_obj.strftime('%d.%m.%Y')} "
                f"{data['start_time'].strftime('%H:%M')} – {data['end_time'].strftime('%H:%M')}"
            )
            await bot.send_message(config.GROUP_CHAT_ID, notification, parse_mode="HTML")

        await callback.message.edit_text(
            "✅ <b>Запись перенесена!</b>\n\n"
            f"👤 @{data['owner_username']}\n"
            f"📅 Было: {old['date']} {old['start']} – {old['end']}\n"
            f"📅 Стало: {data['date'].strftime('%d.%m.%Y')} "
            f"{data['start_time'].strftime('%H:%M')} – {data['end_time'].strftime('%H:%M')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:my")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
            ]),
            parse_mode="HTML"
        )
        await state.clear()
        await callback.answer()
        return

    # ---------- ОБЫЧНОЕ СОЗДАНИЕ ----------
    if admin_mode:
        username = data["target_username"]
        user_id = data["target_user_id"]
        admin_username = user.username or user.first_name
    else:
        username = user.username or user.first_name
        user_id = user.id
        admin_username = None

    if not check_availability(data["date"], data["start_time"], data["end_time"]):
        await callback.message.edit_text(
            "❌ <b>Ошибка!</b>\n\n"
            "Выбранное время пересекается с существующей записью.\n"
            "Пожалуйста, выберите другое время.",
            reply_markup=main_menu_kb(user_id=user.id),
            parse_mode="HTML"
        )
        _track(callback)
        await state.clear()
        await callback.answer()
        return

    try:
        create_event(
            date=data["date"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            username=username,
            user_id=user_id,
            admin_username=admin_username
        )

        now = datetime.now()
        record_datetime = datetime.combine(data["date"], data["start_time"])
        is_past = record_datetime < now

        if not admin_mode or (admin_mode and not is_past):
            date_obj = data['date']
            calendar_url = (
                f"https://calendar.google.com/calendar/u/0/r/day/"
                f"{date_obj.year}/{date_obj.month}/{date_obj.day}"
            )

            notification = (
                f"🟢 <b>Новая запись на Horiba</b>\n\n"
                f"📆 <a href=\"{calendar_url}\">Посмотреть расписание</a>\n"
            )

            if admin_mode:
                notification += (
                    f"👤 Запись за: @{username}\n"
                    f"⠀<i>внесена администратором</i>\n"
                )
            else:
                notification += f"👤 @{username}\n"

            notification += (
                f"📅 {date_obj.strftime('%d.%m.%Y')}\n"
                f"⏰ {data['start_time'].strftime('%H:%M')} – {data['end_time'].strftime('%H:%M')}"
            )

            await bot.send_message(config.GROUP_CHAT_ID, notification, parse_mode="HTML")

        success_text = "✅ <b>Запись успешно создана!</b>\n\n"

        if admin_mode:
            success_text += f"👤 Запись за: @{username}\n"

        success_text += (
            f"📅 {data['date'].strftime('%d.%m.%Y')}\n"
            f"⏰ {data['start_time'].strftime('%H:%M')} – {data['end_time'].strftime('%H:%M')}\n\n"
        )

        if admin_mode:
            if is_past:
                success_text += "<i>Запись в прошлом — уведомление в группу не отправлено.</i>"
            else:
                success_text += "<i>Уведомление отправлено в групповой чат.</i>"
        else:
            success_text += "Событие добавлено в Google Calendar."

        await callback.message.edit_text(
            success_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
            ]),
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при создании записи:\n{str(e)}")

    await state.clear()
    await callback.answer()


@router.callback_query(BookingStates.confirming, F.data == "cancel_booking")
async def cancel_booking(callback: CallbackQuery, state: FSMContext):
    """Отмена записи или переноса."""
    data = await state.get_data()

    if data.get("reschedule_event_id"):
        await callback.message.edit_text(
            "❌ Перенос отменён. Запись осталась без изменений.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:my")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
            ]),
            parse_mode="HTML"
        )
        await state.clear()
        await callback.answer()
        return

    admin_mode = data.get("admin_mode", False)

    if admin_mode:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin:panel")]
        ])
    else:
        kb = main_menu_kb(user_id=callback.from_user.id)

    await callback.message.edit_text("❌ Запись отменена", reply_markup=kb)
    if not admin_mode:
        _track(callback)
    await state.clear()
    await callback.answer()


# ==================== ОТМЕНА ДЕЙСТВИЯ ====================

@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена действия через inline-кнопку."""
    data = await state.get_data()

    if data.get("reschedule_event_id"):
        await callback.message.edit_text(
            "❌ Перенос отменён. Запись осталась без изменений.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои записи", callback_data="menu:my")],
                [InlineKeyboardButton(text="🔙 В меню", callback_data="menu:back")]
            ]),
            parse_mode="HTML"
        )
        await state.clear()
        await callback.answer()
        return

    admin_mode = data.get("admin_mode", False)

    await state.clear()

    if admin_mode:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin:panel")]
        ])
    else:
        kb = main_menu_kb(user_id=callback.from_user.id)

    await callback.message.edit_text("❌ Действие отменено", reply_markup=kb)
    if not admin_mode:
        _track(callback)
    await callback.answer()


@router.message(Command("cancel"))
async def cancel_command(msg: Message, state: FSMContext):
    """Отмена действия через команду."""
    await state.clear()
    await msg.answer("❌ Действие отменено")