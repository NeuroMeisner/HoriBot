from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import date
import os


def admin_panel_kb() -> InlineKeyboardMarkup:
    """Клавиатура админ-панели."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика за месяц", callback_data="admin:stats_current"),
            InlineKeyboardButton(text="📁 Отчёты", callback_data="admin:reports_list")
        ],
        [
            InlineKeyboardButton(text="📝 Внести запись", callback_data="admin:booking:start"),
            InlineKeyboardButton(text="🚫 Забаненные", callback_data="admin:banned_list")
        ],
        [
            InlineKeyboardButton(text="🖥 Параметры сервера", callback_data="admin:server:info")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="menu:back")
        ]
    ])


def get_available_reports() -> list:
    """Получить список доступных НАСТОЯЩИХ отчётов из папки reports/.
    
    Игнорирует test_report.html — он используется только как пример.
    
    Returns:
        list of dict: [{'year': 2026, 'month': 10, 'filename': '2026_10_report.html'}, ...]
    """
    reports_dir = "reports"
    if not os.path.exists(reports_dir):
        return []
    
    available = []
    for filename in os.listdir(reports_dir):
        # Пропускаем тестовый отчёт
        if filename == "test_report.html":
            continue
        
        # Проверяем формат: YYYY_MM_report.html
        if filename.endswith("_report.html"):
            try:
                parts = filename.replace("_report.html", "").split("_")
                if len(parts) == 2:
                    year = int(parts[0])
                    month = int(parts[1])
                    available.append({
                        'year': year,
                        'month': month,
                        'filename': filename
                    })
            except (ValueError, IndexError):
                continue
    
    # Сортируем по дате (новые первыми)
    available.sort(key=lambda x: (x['year'], x['month']), reverse=True)
    return available


def has_test_report() -> bool:
    """Проверить, существует ли тестовый отчёт."""
    return os.path.exists(os.path.join("reports", "test_report.html"))


def reports_list_kb() -> InlineKeyboardMarkup:
    """Клавиатура со списком доступных отчётов."""
    reports = get_available_reports()
    
    month_names = {
        1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
        5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
        9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
    }
    
    buttons = []
    
    if not reports:
        # Если настоящих отчётов нет — добавляем кнопку тестового отчёта (если он существует)
        if has_test_report():
            buttons.append([
                InlineKeyboardButton(text="🧪 Тестовый отчёт (пример)", callback_data="admin:test_report")
            ])
        
        # Кнопка назад
        buttons.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin:panel")
        ])
    else:
        # Добавляем кнопки для каждого настоящего отчёта
        for report in reports:
            text = f"{month_names[report['month']]} {report['year']}"
            callback_data = f"admin:report:{report['year']}_{report['month']}"
            buttons.append([
                InlineKeyboardButton(text=text, callback_data=callback_data)
            ])
        
        # Кнопка назад
        buttons.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin:panel")
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)