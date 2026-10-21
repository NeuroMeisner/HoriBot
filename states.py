from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    """Состояния процесса записи.
    
    Используются как для обычной записи пользователя,
    так и для админской записи за другого сотрудника.
    Различение идёт через флаг admin_mode в данных состояния.
    """
    # Состояние выбора пользователя (только для админской записи)
    choosing_user = State()
    
    # Состояние выбора даты
    choosing_date = State()
    
    # Состояние выбора времени начала
    choosing_start_time = State()
    
    # Состояние выбора времени окончания
    choosing_end_time = State()
    
    # Состояние подтверждения записи
    confirming = State()
    
    # Состояния пикера времени (ручная настройка кнопками +/-)
    picking_time_start = State()
    picking_time_end = State()