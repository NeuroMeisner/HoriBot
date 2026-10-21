from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from datetime import datetime
import os

from stats_manager import (
    get_period_stats, format_minutes,
    get_current_month_period, get_last_month_period
)
from reports.stats_report import generate_html_report
import config

router = Router()


@router.message(Command("usage"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_usage(msg: Message):
    """Статистика с начала текущего месяца по сегодня (текстовое сообщение)."""
    start_date, end_date = get_current_month_period()
    stats = get_period_stats(start_date, end_date)
    
    if stats['total_count'] == 0:
        await msg.answer("📭 За текущий месяц пока нет записей.")
        return
    
    # Формируем текстовое сообщение в варианте А
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
    
    await msg.answer(text, parse_mode="HTML")


@router.message(Command("stats"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_stats(msg: Message, bot: Bot):
    """Отправить подробный HTML-отчёт за прошлый месяц."""
    start_date, end_date = get_last_month_period()
    
    # Отправляем сообщение о генерации
    status_msg = await msg.answer("⏳ Генерирую отчёт за прошлый месяц...")
    
    try:
        stats = get_period_stats(start_date, end_date)
        html_content = generate_html_report(stats)
        
        # Формируем имя файла
        filename = f"horiba_stats_{start_date.strftime('%Y_%m')}.html"
        
        # Отправляем HTML как документ
        document = BufferedInputFile(
            html_content.encode('utf-8'),
            filename=filename
        )
        
        month_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        caption = f"📊 Статистика Horiba — {month_names[start_date.month]} {start_date.year}"
        
        await msg.answer_document(
            document=document,
            caption=caption
        )
        
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при генерации отчёта:\n{str(e)}")


async def send_monthly_report(bot: Bot):
    """Автоматическая рассылка отчёта за прошлый месяц всем админам."""
    start_date, end_date = get_last_month_period()
    
    try:
        stats = get_period_stats(start_date, end_date)
        html_content = generate_html_report(stats)
        
        # ✅ СОХРАНЯЕМ HTML-ФАЙЛ В ПАПКУ reports/
        reports_dir = "reports"
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
        
        filename = f"{start_date.year}_{start_date.month:02d}_report.html"
        filepath = os.path.join(reports_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"✅ Отчёт сохранён: {filepath}")
        
        # Отправляем админам
        document = BufferedInputFile(
            html_content.encode('utf-8'),
            filename=filename
        )
        
        month_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        
        caption = f"📊 <b>Автоматический отчёт</b>\n\nСтатистика Horiba — {month_names[start_date.month]} {start_date.year}"
        
        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=document,
                    caption=caption,
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"⚠️ Не удалось отправить отчёт админу {admin_id}: {e}")
        
        print(f"✅ Месячный отчёт за {month_names[start_date.month]} {start_date.year} отправлен")
        
    except Exception as e:
        print(f"❌ Ошибка при генерации месячного отчёта: {e}")