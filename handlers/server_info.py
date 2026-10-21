"""Экран «Параметры сервера» в админ-панели."""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from server_monitor import get_server_metrics, format_bytes, format_uptime

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

    # Температуры
    lines.append("🌡 <b>Температуры:</b>")
    cpu = f"{m['cpu_temp']:.0f}°C" if m["cpu_temp"] is not None else "нет данных"
    lines.append(f"   CPU: {cpu}")
    if m["gpu_temp"] is not None:
        name = f" ({m['gpu_name']})" if m["gpu_name"] else ""
        lines.append(f"   GPU: {m['gpu_temp']:.0f}°C{name}")
    elif m["gpu_name"]:
        lines.append("   GPU: нет данных")
    for disk_name, val in (m["disk_temps"] or {}).items():
        lines.append(f"   {disk_name}: {val:.0f}°C")
    lines.append(
        f"   Пороги: норма &lt; {config.TEMP_NORM_C:.0f}, "
        f"warn {config.TEMP_WARN_C:.0f}, crit {config.TEMP_CRIT_C:.0f}"
    )

    # Нагрузка
    lines.append("\n📈 <b>Нагрузка:</b>")
    load = f"{m['cpu_load']:.0f}%" if m["cpu_load"] is not None else "—"
    ram = (f"{format_bytes(m['ram_used'])} / {format_bytes(m['ram_total'])}"
           if m["ram_total"] else "—")
    disk = (f"{format_bytes(m['disk_used'])} / {format_bytes(m['disk_total'])}"
            if m["disk_total"] else "—")
    lines.append(f"   CPU: {load} • RAM: {ram}")
    lines.append(f"   Диск: {disk}")
    lines.append(f"⏱ Аптайм: {format_uptime(m['uptime_sec'])}")

    # Термозащита
    guard = m.get("guard")
    lines.append("\n🛡 <b>Термозащита:</b>")
    if guard:
        lines.append(
            f"   Состояние: {guard.get('state', '?')}, "
            f"обновлено {guard.get('updated', '?')}"
        )
        le = guard.get("last_event")
        if le:
            lines.append(
                f"🚨 Последний инцидент: {le.get('type')}, "
                f"{le.get('temp')}°C, {le.get('time')}"
            )
        else:
            lines.append("🚨 Инцидентов не было")
    else:
        lines.append("   Статус-файл не найден: guard не запущен "
                     "(или это Windows-машина разработки)")

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