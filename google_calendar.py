from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, time, timedelta, date, timezone
from zoneinfo import ZoneInfo
import config
import logging
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    """Получить сервис Google Calendar API."""
    creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    return service

def create_event(date: date, start_time: time, end_time: time, username: str, user_id: int, admin_username: str = None):
    """Создать событие в Google Calendar.
    
    Args:
        date: дата события
        start_time: время начала
        end_time: время окончания
        username: имя пользователя (для кого запись)
        user_id: ID пользователя
        admin_username: если запись внесена админом — его username
    """
    service = get_calendar_service()
    
    start_datetime = datetime.combine(date, start_time)
    end_datetime = datetime.combine(date, end_time)
    
    # Формируем описание
    description = f'Запись через Telegram-бот\nTelegram: @{username}\nUser ID: {user_id}'
    if admin_username:
        description += f'\nЗапись внесена администратором: @{admin_username}'
    
    event = {
        'summary': f'Horiba — @{username}',
        'description': description,
        'start': {
            'dateTime': start_datetime.isoformat(),
            'timeZone': config.TIMEZONE,
        },
        'end': {
            'dateTime': end_datetime.isoformat(),
            'timeZone': config.TIMEZONE,
        },
    }
    
    event = service.events().insert(
        calendarId=config.CALENDAR_ID,
        body=event
    ).execute()
    
    return event

def check_availability(date: date, start_time: time, end_time: time) -> bool:
    """Проверить, свободно ли время (не пересекается с существующими записями)."""
    service = get_calendar_service()
    
    day_start = datetime.combine(date, time.min)
    day_end = datetime.combine(date, time.max)
    
    events_result = service.events().list(
        calendarId=config.CALENDAR_ID,
        timeMin=day_start.isoformat() + 'Z',
        timeMax=day_end.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    events = events_result.get('items', [])
    
    for event in events:
        event_start = datetime.fromisoformat(event['start']['dateTime']).time()
        event_end = datetime.fromisoformat(event['end']['dateTime']).time()
        
        # Пересечение: start < event_end AND end > event_start
        if start_time < event_end and end_time > event_start:
            return False
    
    return True

def get_user_events(user_id: int) -> list:
    """Получить все записи пользователя начиная с сегодняшнего дня.
    
    Показывает:
    - Все записи на сегодня (включая уже прошедшие по времени)
    - Все записи на завтра и позже
    
    НЕ показывает:
    - Записи на вчера и ранее
    """
    service = get_calendar_service()
    
    # ✅ ИСПРАВЛЕНИЕ: Правильная обработка часовых зон
    tz = ZoneInfo(config.TIMEZONE)
    now_local = datetime.now(tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Преобразуем в UTC для API
    time_min = today_start_local.astimezone(timezone.utc).isoformat()
    
    events_result = service.events().list(
        calendarId=config.CALENDAR_ID,
        timeMin=time_min,
        singleEvents=True,
        orderBy='startTime',
        maxResults=50
    ).execute()
    
    events = events_result.get('items', [])
    
    user_events = []
    search_marker = f'User ID: {user_id}'
    
    for event in events:
        description = event.get('description', '')
        if search_marker in description:
            user_events.append(event)
    
    return user_events

def get_today_events() -> list:
    """Получить все события на сегодня."""
    service = get_calendar_service()
    
    # ✅ ИСПРАВЛЕНИЕ: Правильная обработка часовых зон
    tz = ZoneInfo(config.TIMEZONE)
    now_local = datetime.now(tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_local = today_start_local + timedelta(days=1)
    
    # Преобразуем в UTC для API
    time_min = today_start_local.astimezone(timezone.utc).isoformat()
    time_max = today_end_local.astimezone(timezone.utc).isoformat()
    
    events_result = service.events().list(
        calendarId=config.CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    return events_result.get('items', [])

def get_busy_intervals(target_date: date) -> list:
    """Получить список занятых интервалов на указанную дату."""
    service = get_calendar_service()
    
    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)
    
    events_result = service.events().list(
        calendarId=config.CALENDAR_ID,
        timeMin=day_start.isoformat() + 'Z',
        timeMax=day_end.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    
    events = events_result.get('items', [])
    
    busy_intervals = []
    for event in events:
        start = datetime.fromisoformat(event['start']['dateTime']).time()
        end = datetime.fromisoformat(event['end']['dateTime']).time()
        busy_intervals.append((start, end))
    
    return busy_intervals

def delete_event(event_id: str) -> bool:
    """Удалить событие из календаря."""
    service = get_calendar_service()
    
    try:
        service.events().delete(
            calendarId=config.CALENDAR_ID,
            eventId=event_id
        ).execute()
        return True
    except Exception as e:
        print(f"Ошибка при удалении события: {e}")
        return False

def get_event_by_id(event_id: str) -> dict:
    """Получить событие по ID."""
    service = get_calendar_service()
    
    try:
        event = service.events().get(
            calendarId=config.CALENDAR_ID,
            eventId=event_id
        ).execute()
        return event
    except Exception as e:
        print(f"Ошибка при получении события: {e}")
        return None

def get_events_for_period(start_date: date, end_date: date) -> list:
    """Получить все события за указанный период (включительно).
    
    Args:
        start_date: начало периода
        end_date: конец периода
    
    Returns:
        list of dict: список событий с полями start, end, username, user_id
    """
    service = get_calendar_service()
    
    # Период с 00:00 start_date до 23:59 end_date
    period_start = datetime.combine(start_date, time.min)
    period_end = datetime.combine(end_date, time.max)
    
    events_result = service.events().list(
        calendarId=config.CALENDAR_ID,
        timeMin=period_start.isoformat() + 'Z',
        timeMax=period_end.isoformat() + 'Z',
        singleEvents=True,
        orderBy='startTime',
        maxResults=1000
    ).execute()
    
    events = events_result.get('items', [])
    
    parsed_events = []
    for event in events:
        try:
            start = datetime.fromisoformat(event['start']['dateTime'])
            end = datetime.fromisoformat(event['end']['dateTime'])
            
            # Парсим username и user_id из описания
            description = event.get('description', '')
            username = None
            user_id = None
            
            for line in description.split('\n'):
                if line.startswith('Telegram: @'):
                    username = line.replace('Telegram: @', '').strip()
                elif line.startswith('User ID: '):
                    try:
                        user_id = int(line.replace('User ID: ', '').strip())
                    except ValueError:
                        pass
            
            # Если username не найден — берём из summary
            if not username:
                summary = event.get('summary', '')
                if summary.startswith('Horiba — @'):
                    username = summary.replace('Horiba — @', '').strip()
            
            parsed_events.append({
                'start': start,
                'end': end,
                'username': username or 'Неизвестный',
                'user_id': user_id,
                'duration_minutes': (end - start).total_seconds() / 60
            })
        except Exception as e:
            logger.warning(f"Ошибка парсинга события {event.get('id')}: {e}")
            continue
    
    return parsed_events