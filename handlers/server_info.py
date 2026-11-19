"""Экран «Параметры сервера» в админ-панели."""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from server_monitor import get_server_metrics, format_bytes, format_uptime, read_connection_status

router = Router()


def _server_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:server:refresh"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin:panel"),
        ]
    ])


def _render_text(m: dict) -> str:
    lines = ["🖥 <b>Параметры сервера</b>\n"]

    # --- Температуры ---
    lines.append("🌡 <b>Температуры компонентов:</b>")
    
    if m["max_temp"] is not None:
        lines.append(f"   🔥 Максимум: <b>{m['max_temp']:.1f}°C</b> ({m['max_source']})")
    else:
        lines.append("   ℹ️ Данные о температуре недоступны")
        
    lines.append("") # Пустая строка
    
    # Выводим все датчики, сгруппированные или просто списком
    sensors = m.get("all_sensors", [])
    if sensors:
        # Сортируем по значению (горячие сверху)
        sorted_sensors = sorted(sensors, key=lambda x: x["value"], reverse=True)
        
        for s in sorted_sensors:
            val = s["value"]
            comp = s["component"]
            label = s["label"]
            
            # Цветовой индикатор простыми словами
            status_icon = "🟢"
            threshold_warn = config.TEMP_WARN_C
            threshold_crit = config.TEMP_CRIT_C
            
            if val >= threshold_crit:
                status_icon = "🔴"
            elif val >= threshold_warn:
                status_icon = "🟡"
                
            lines.append(f"   {status_icon} [{comp}] {label}: <b>{val:.1f}°C</b>")
    else:
        lines.append("   ❌ Датчики не найдены")

    # --- Нагрузка ---
    lines.append("\n📈 <b>Нагрузка системы:</b>")
    load = f"{m['cpu_load']:.0f}%" if m["cpu_load"] is not None else "—"
    ram = (f"{format_bytes(m['ram_used'])} / {format_bytes(m['ram_total'])}"
           if m["ram_total"] else "—")
    disk = (f"{format_bytes(m['disk_used'])} / {format_bytes(m['disk_total'])}"
            if m["disk_total"] else "—")
    lines.append(f"   CPU Load: {load}")
    lines.append(f"   RAM: {ram}")
    lines.append(f"   Disk Space: {disk}")
    lines.append(f"⏱ Аптайм: {format_uptime(m['uptime_sec'])}")

    # --- Статус защиты ---
    lines.append("\n🛡 <b>Статус термозащиты:</b>")
    if m["guard_state"]:
        state_map = {
            "NORMAL": "🟢 Норма",
            "WARNED": "🟡 Внимание",
            "CRITICAL": "🔴 Критический"
        }
        display_state = state_map.get(m["guard_state"], m["guard_state"])
        lines.append(f"   Текущее состояние: {display_state}")
        lines.append(f"   Обновлено: {m['guard_updated']}")
        
        # Показываем последний инцидент, если он был
        # Для этого нам нужно прочитать файл снова или передать его в metrics
        # Проще всего добавить чтение last_event в get_server_metrics, 
        # но давайте сделаем быстрый хак: прочитаем файл здесь
        try:
            with open(os.path.join(PROJECT_DIR, "server_state", "thermal_status.json"), encoding="utf-8") as f:
                full_data = json.load(f)
                le = full_data.get("last_event")
                if le:
                    evt_type = le.get("type")
                    evt_time = le.get("time")
                    evt_src = le.get("source", "")
                    icon = "⚠️" if evt_type == "WARN" else ("🔥" if evt_type == "CRIT" else "✅")
                    lines.append(f"   Последний инцидент: {icon} {evt_type} at {evt_time} ({evt_src})")
        except Exception:
            pass


    else:
        lines.append("   ⚪ Сервис термозащиты не запущен или нет данных")
    # Журнал связи с Telegram API
    conn = read_connection_status()
    lines.append("\n🔌 <b>История связи:</b>")
    
    fail_time = conn.get("last_fail_time")
    fail_reason = conn.get("last_fail_reason")
    rec_time = conn.get("last_recover_time")
    breaks_count = conn.get("today_breaks", 0)
    
    if fail_time:
        lines.append(f"   ⚠️ Последний обрыв: <code>{fail_time}</code>")
        if fail_reason:
            # Обрезаем слишком длинные сообщения для красоты
            short_err = fail_reason.split('\n')[0][:50] + ("..." if len(fail_reason)>50 else "")
            lines.append(f"      Причина: <i>{short_err}</i>")
            
        if rec_time:
            lines.append(f"   ✅ Восстановление: <code>{rec_time}</code>")
        else:
            lines.append(f"   ❓ Статус: <b>Связь отсутствует</b> (или не было активности)")
    else:
        lines.append("   ℹ️ Обрывов связи не зафиксировано")
        
    lines.append(f"   📊 Обрывов за сегодня: <b>{breaks_count}</b>")

    return "\n".join(lines)


async def _show(callback: CallbackQuery):
    m = get_server_metrics()
    await callback.message.edit_text(
        _render_text(m), reply_markup=_server_kb(), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:server:info",
                       F.from_user.id.in_(config.ADMIN_IDS))
async def show_server_info(callback: CallbackQuery):
    await _show(callback)
    await callback.answer()


@router.callback_query(F.data == "admin:server:refresh",
                       F.from_user.id.in_(config.ADMIN_IDS))
async def refresh_server_info(callback: CallbackQuery):
    await _show(callback)
    await callback.answer("Обновлено")

from aiogram.filters import Command
import power_schedule
from datetime import date as _date, timedelta as _td


@router.message(Command("next_offline"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_next_offline(msg: Message):
    """Показать режим (рабочий/выходной/праздник) на ближайшие 14 дней."""
    lines = []
    today = _date.today()
    for i in range(14):
        dd = today + _td(days=i)
        off = power_schedule.is_off_day(dd)
        name = power_schedule.holiday_name(dd)
        mark = "🛑 выходной" if off else "🟢 рабочий"
        if name:
            mark += f" — {name}"
        lines.append(f"{dd:%d.%m.%Y} ({dd:%a}) — {mark}")
    await msg.answer(
        "📆 <b>Режим питания на ближайшие 14 дней:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML"
    )