from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from status_manager import get_status, get_systems_order, get_checked_systems, is_all_checked


def status_message_text() -> str:
    """Сформировать текст сообщения со статусом прибора."""
    status = get_status()
    order = get_systems_order()
    checked = get_checked_systems()
    
    if status == "off":
        header = "🔴 <b>Прибор Horiba сейчас ВЫКЛЮЧЕН</b>\n\n"
        instruction = "Для включения проверьте следующие системы:\n\n"
    else:
        header = "🟢 <b>Прибор Horiba сейчас ВКЛЮЧЁН</b>\n\n"
        instruction = "Для выключения проверьте следующие системы:\n\n"
    
    # Нумерованный список (зачёркнутые — уже проверенные)
    lines = []
    for i, system in enumerate(order, 1):
        if system in checked:
            lines.append(f"<s>{i}. {system}</s>")
        else:
            lines.append(f"{i}. {system}")
    
    return header + instruction + "\n".join(lines)


def status_kb() -> InlineKeyboardMarkup:
    """Клавиатура со списком систем для проверки."""
    order = get_systems_order()
    checked = get_checked_systems()
    status = get_status()
    
    buttons = []
    i = 0
    
    # Проходим по списку систем
    while i < len(order):
        system = order[i]
        
        # Пропускаем уже проверенные
        if system in checked:
            i += 1
            continue
        
        # Обработка пары лазеров — всегда на одной строке
        if system in ("лазер 633 нм", "лазер 532 нм"):
            laser_buttons = []
            # Собираем все лазеры, идущие подряд и не проверенные
            while i < len(order) and order[i] in ("лазер 633 нм", "лазер 532 нм"):
                if order[i] not in checked:
                    laser_buttons.append(
                        InlineKeyboardButton(
                            text=order[i],
                            callback_data=f"status:check:{i}"
                        )
                    )
                i += 1
            if laser_buttons:
                buttons.append(laser_buttons)
            continue
        else:
            # Обычная система — одна кнопка в строке
            buttons.append([
                InlineKeyboardButton(
                    text=system,
                    callback_data=f"status:check:{i}"
                )
            ])
            i += 1
    
    # Если все системы проверены — финальная кнопка
    if is_all_checked():
        if status == "off":
            buttons.append([
                InlineKeyboardButton(
                    text="✅ Всё включено",
                    callback_data="status:confirm:on"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text="🔴 Всё выключено",
                    callback_data="status:confirm:off"
                )
            ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)