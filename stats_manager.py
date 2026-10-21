from datetime import date, timedelta
from collections import defaultdict
from google_calendar import get_events_for_period


def get_period_stats(start_date: date, end_date: date) -> dict:
    """Получить полную статистику за период.
    
    Returns:
        dict с полями:
        - users: {username: {total_minutes, count, records: [...]}}
        - total_minutes: общее время
        - total_count: общее количество записей
        - records: список всех записей
        - longest_session: самая длинная запись
        - longest_day: самый длинный день измерений
        - earliest_start: самый ранний приход
        - latest_end: самый поздний уход
        - busiest_day: самый загруженный день
        - busiest_weekday: самый загруженный день недели
        - avg_duration: среднее время записи
        - utilization_percent: процент загрузки прибора
    """
    events = get_events_for_period(start_date, end_date)
    
    # Базовая статистика по пользователям
    users = defaultdict(lambda: {
        'total_minutes': 0,
        'count': 0,
        'records': []
    })
    
    # Статистика по дням
    days = defaultdict(lambda: {
        'total_minutes': 0,
        'count': 0
    })
    
    # Статистика по дням недели (0=пн, 6=вс)
    weekdays = defaultdict(int)
    
    # Пользователи по дням (для "самого длинного дня")
    user_days = defaultdict(lambda: defaultdict(float))
    
    total_minutes = 0
    
    for event in events:
        username = event['username']
        duration = event['duration_minutes']
        event_date = event['start'].date()
        weekday = event['start'].weekday()
        
        # По пользователям
        users[username]['total_minutes'] += duration
        users[username]['count'] += 1
        users[username]['records'].append(event)
        
        # По дням
        days[event_date]['total_minutes'] += duration
        days[event_date]['count'] += 1
        
        # По дням недели
        weekdays[weekday] += 1
        
        # Пользователи по дням
        user_days[username][event_date] += duration
        
        total_minutes += duration
    
    total_count = len(events)
    
    # === Рекорды ===
    
    # Самая продолжительная запись
    longest_session = None
    if events:
        longest_session = max(events, key=lambda e: e['duration_minutes'])
    
    # Самый длинный день измерений (суммарно одним пользователем)
    longest_day = None
    if user_days:
        best_user = None
        best_date = None
        best_minutes = 0
        for username, day_stats in user_days.items():
            for day_date, minutes in day_stats.items():
                if minutes > best_minutes:
                    best_minutes = minutes
                    best_user = username
                    best_date = day_date
        if best_user:
            longest_day = {
                'username': best_user,
                'date': best_date,
                'total_minutes': best_minutes
            }
    
    # Самый ранний приход
    earliest_start = None
    if events:
        earliest_start = min(events, key=lambda e: e['start'].time())
    
    # Самый поздний уход
    latest_end = None
    if events:
        latest_end = max(events, key=lambda e: e['end'].time())
    
    # Самый загруженный день
    busiest_day = None
    if days:
        busiest_day_date = max(days.keys(), key=lambda d: days[d]['total_minutes'])
        busiest_day = {
            'date': busiest_day_date,
            'total_minutes': days[busiest_day_date]['total_minutes'],
            'count': days[busiest_day_date]['count']
        }
    
    # Самый загруженный день недели
    busiest_weekday = None
    if weekdays:
        busiest_weekday_num = max(weekdays.keys(), key=lambda w: weekdays[w])
        weekday_names = {
            0: 'Понедельник', 1: 'Вторник', 2: 'Среда',
            3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'
        }
        busiest_weekday = {
            'name': weekday_names[busiest_weekday_num],
            'count': weekdays[busiest_weekday_num]
        }
    
    # Среднее время записи
    avg_duration = total_minutes / total_count if total_count > 0 else 0
    
    # Процент загрузки прибора (рабочее время: 07:00 - 23:00 = 16 часов в день)
    days_count = (end_date - start_date).days + 1
    total_working_minutes = days_count * 16 * 60  # 16 часов в день
    utilization_percent = (total_minutes / total_working_minutes * 100) if total_working_minutes > 0 else 0
    
    # Топ-3 по времени
    top_by_time = sorted(
        users.items(),
        key=lambda x: x[1]['total_minutes'],
        reverse=True
    )[:3]
    
    # Топ-3 по количеству записей
    top_by_count = sorted(
        users.items(),
        key=lambda x: x[1]['count'],
        reverse=True
    )[:3]
    
    # Полная таблица пользователей (отсортированная по времени)
    all_users_sorted = sorted(
        users.items(),
        key=lambda x: x[1]['total_minutes'],
        reverse=True
    )
    
    return {
        'start_date': start_date,
        'end_date': end_date,
        'users': dict(users),
        'total_minutes': total_minutes,
        'total_count': total_count,
        'longest_session': longest_session,
        'longest_day': longest_day,
        'earliest_start': earliest_start,
        'latest_end': latest_end,
        'busiest_day': busiest_day,
        'busiest_weekday': busiest_weekday,
        'avg_duration': avg_duration,
        'utilization_percent': utilization_percent,
        'top_by_time': top_by_time,
        'top_by_count': top_by_count,
        'all_users_sorted': all_users_sorted
    }


def format_minutes(minutes: float) -> str:
    """Форматировать минуты в читаемый вид: '18 ч 30 мин'."""
    if minutes < 1:
        return f"{int(minutes)} мин"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours == 0:
        return f"{mins} мин"
    if mins == 0:
        return f"{hours} ч"
    return f"{hours} ч {mins} мин"


def get_last_month_period() -> tuple:
    """Получить период прошлого месяца (start_date, end_date)."""
    today = date.today()
    first_of_current = today.replace(day=1)
    last_day_of_prev = first_of_current - timedelta(days=1)
    first_day_of_prev = last_day_of_prev.replace(day=1)
    return first_day_of_prev, last_day_of_prev


def get_current_month_period() -> tuple:
    """Получить период текущего месяца с начала до сегодня."""
    today = date.today()
    first_of_current = today.replace(day=1)
    return first_of_current, today