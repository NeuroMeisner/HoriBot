from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import os

from keyboards.admin_kb import admin_panel_kb, reports_list_kb, get_available_reports
from stats_manager import get_period_stats, format_minutes, get_current_month_period
import config
import auth_manager

router = Router()


def report_sent_kb() -> InlineKeyboardMarkup:
    """Клавиатура после отправки отчёта."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📁 Ещё отчёт", callback_data="admin:reports_list")
        ],
        [
            InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:back")
        ]
    ])


@router.callback_query(F.data == "admin:panel", F.from_user.id.in_(config.ADMIN_IDS))
async def show_admin_panel(callback: CallbackQuery):
    """Показать админ-панель."""
    await callback.message.edit_text(
        "🛡️ <b>Админ-панель</b>\n\nВыберите действие:",
        reply_markup=admin_panel_kb(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:stats_current", F.from_user.id.in_(config.ADMIN_IDS))
async def show_current_month_stats(callback: CallbackQuery):
    """Показать статистику за текущий месяц."""
    start_date, end_date = get_current_month_period()
    stats = get_period_stats(start_date, end_date)
    
    if stats['total_count'] == 0:
        await callback.message.edit_text(
            "📭 За текущий месяц пока нет записей.",
            reply_markup=admin_panel_kb(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    month_names = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    
    month_name = month_names[start_date.month]
    text = f"📊 <b>Статистика за {month_name} {start_date.year}</b>\n"
    text += f"<i>(с {start_date.strftime('%d.%m')} по {end_date.strftime('%d.%m')})</i>\n\n"
    
    for username, data in stats['all_users_sorted']:
        text += f"👤 @{username}: {data['count']} записей, {format_minutes(data['total_minutes'])}\n"
    
    text += f"\n<b>Всего:</b> {stats['total_count']} записей, {format_minutes(stats['total_minutes'])}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin:panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin:reports_list", F.from_user.id.in_(config.ADMIN_IDS))
async def show_reports_list(callback: CallbackQuery):
    """Показать список доступных отчётов."""
    reports = get_available_reports()
    
    if not reports:
        await callback.message.edit_text(
            "📭 Отчёты пока не сформированы.\n\n"
            "Первый отчёт будет отправлен 1-го числа следующего месяца.",
            reply_markup=reports_list_kb(),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "📁 <b>Доступные отчёты</b>\n\nВыберите период:",
            reply_markup=reports_list_kb(),
            parse_mode="HTML"
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("admin:report:"), F.from_user.id.in_(config.ADMIN_IDS))
async def send_report(callback: CallbackQuery, bot: Bot):
    """Отправить HTML-отчёт за выбранный месяц."""
    parts = callback.data.split(":")[2]
    year, month = map(int, parts.split("_"))
    
    filename = f"{year}_{month:02d}_report.html"
    filepath = os.path.join("reports", filename)
    
    if not os.path.exists(filepath):
        await callback.answer("❌ Отчёт не найден", show_alert=True)
        return
    
    try:
        with open(filepath, 'rb') as f:
            html_content = f.read()
        
        document = BufferedInputFile(html_content, filename=filename)
        
        month_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        caption = f"📊 Отчёт Horiba — {month_names[month]} {year}"
        
        # ✅ РЕДАКТИРУЕМ СООБЩЕНИЕ: добавляем документ к существующему
        await callback.message.answer_document(
            document=document,
            caption=caption
        )
        
        # ✅ РЕДАКТИРУЕМ исходное сообщение на "Отчёт отправлен!"
        await callback.message.edit_text(
            "✅ <b>Отчёт отправлен!</b>\n\n"
            "Файл отчёта отправлен вам в отдельном сообщении.",
            reply_markup=report_sent_kb(),
            parse_mode="HTML"
        )
        
        await callback.answer()
        
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin:test_report", F.from_user.id.in_(config.ADMIN_IDS))
async def send_test_report(callback: CallbackQuery, bot: Bot):
    """Отправить тестовый отчёт."""
    filepath = os.path.join("reports", "test_report.html")
    
    if not os.path.exists(filepath):
        await callback.answer("❌ Тестовый отчёт не найден", show_alert=True)
        return
    
    try:
        with open(filepath, 'rb') as f:
            html_content = f.read()
        
        document = BufferedInputFile(html_content, filename="test_report.html")
        caption = "🧪 <b>Тестовый отчёт</b>\n\nЭто пример формата отчёта с выдуманными данными."
        
        # ✅ Отправляем документ
        await callback.message.answer_document(
            document=document,
            caption=caption,
            parse_mode="HTML"
        )
        
        # ✅ РЕДАКТИРУЕМ исходное сообщение
        await callback.message.edit_text(
            "✅ <b>Тестовый отчёт отправлен!</b>\n\n"
            "Это пример формата. Настоящие отчёты будут формироваться автоматически 1-го числа каждого месяца.",
            reply_markup=report_sent_kb(),
            parse_mode="HTML"
        )
        
        await callback.answer()
        
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin:banned_list", F.from_user.id.in_(config.ADMIN_IDS))
async def show_banned_list(callback: CallbackQuery):
    """Показать список забаненных пользователей."""
    banned = await auth_manager.load_banned_users()
    
    if not banned:
        text = "📭 Нет забаненных пользователей."
    else:
        text = "🚫 <b>Забаненные пользователи:</b>\n\n"
        for user_id in banned:
            text += f"• ID: {user_id}\n"
        text += f"\nВсего: {len(banned)}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin:panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()