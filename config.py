import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ID группового чата для уведомлений (отрицательное число)
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-1001234567890"))

# ID Google Calendar
CALENDAR_ID = os.getenv("CALENDAR_ID", "your-calendar-id@group.calendar.google.com")

# Ссылка для добавления календаря себе
# Формат: https://calendar.google.com/calendar/u/0/r?cid=CALENDAR_ID
CALENDAR_LINK = os.getenv(
    "CALENDAR_LINK",
    f"https://calendar.google.com/calendar/u/0/r?cid={os.getenv('CALENDAR_ID', '')}"
)

# Прокси
BOT_PROXY = os.getenv("BOT_PROXY", "YOUR_PROXY_HERE")

# Часовой пояс
TIMEZONE = "Europe/Moscow"

# Список ID администраторов
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ====== НАСТРОЙКИ ОБНОВЛЕНИЙ ======

# Проверка обновлений при запуске и раз в сутки
UPDATE_CHECK_ENABLED = os.getenv("UPDATE_CHECK_ENABLED", "true").lower() == "true"

# Полный автопилот: устанавливать обновления без подтверждения админа.
# По умолчанию ВЫКЛЮЧЕНО — бот только уведомляет о новой версии.
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "false").lower() == "true"