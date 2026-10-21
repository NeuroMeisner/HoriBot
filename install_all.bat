@echo off
chcp 65001 >nul
title HoriBot — Полная установка
color 0E
echo ============================================
echo      HoriBot — Полная установка
echo ============================================
echo.
echo  Этот скрипт выполнит ВСЁ автоматически:
echo    1. Проверку и установку Python 3.14
echo    2. Создание виртуального окружения
echo    3. Установку всех зависимостей
echo    4. Проверку конфигурации (.env, credentials.json)
echo    5. Создание скриптов запуска/остановки
echo    6. Настройку автозапуска с Windows
echo.
echo ============================================
echo.
pause

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

:: ============================================
:: ШАГ 1: Проверка и установка Python
:: ============================================
echo.
echo [1/6] Проверка наличия Python 3.14...
echo.
set "PYTHON_CMD="

:: Пробуем py launcher
py -3.14 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.14"
    echo   Найден Python 3.14 через py launcher.
    goto :python_found
)

:: Пробуем команду python
python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
    echo   Найден Python %PY_VER% через команду python.
    set "PYTHON_CMD=python"
    goto :python_found
)

:: Установка через winget
echo   Python 3.14 не найден. Попытка установки...
echo.
winget --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ОШИБКА] winget не найден!
    echo   Установите Python вручную: https://www.python.org/downloads/
    echo   Обязательно поставьте галочку "Add Python to PATH"
    pause
    exit /b 1
)

winget install Python.Python.3.14 --accept-package-agreements --accept-source-agreements
if %errorlevel% neq 0 (
    echo   [ОШИБКА] Не удалось установить Python автоматически.
    pause
    exit /b 1
)

:: Обновляем PATH для текущей сессии
set "PATH=%LOCALAPPDATA%\Programs\Python\Python314;%LOCALAPPDATA%\Programs\Python\Python314\Scripts;%PATH%"
set "PATH=%ProgramFiles%\Python314;%ProgramFiles%\Python314\Scripts;%PATH%"

py -3.14 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.14"
    echo   Python 3.14 найден!
    goto :python_found
)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    echo   Python найден!
    goto :python_found
)

echo.
echo   [ВНИМАНИЕ] Python установлен, но не найден в текущей сессии.
echo   ЗАКРОЙТЕ это окно и запустите install_all.bat заново.
pause
exit /b 1

:python_found
echo.

:: ============================================
:: ШАГ 2: Виртуальное окружение
:: ============================================
echo [2/6] Создание виртуального окружения...
if exist "%PROJECT_DIR%\venv\Scripts\activate.bat" (
    echo   Виртуальное окружение уже существует. Пропускаю.
) else (
    %PYTHON_CMD% -m venv "%PROJECT_DIR%\venv"
    if %errorlevel% neq 0 (
        echo   [ОШИБКА] Не удалось создать виртуальное окружение!
        pause
        exit /b 1
    )
    echo   Виртуальное окружение создано.
)
echo.

:: ============================================
:: ШАГ 3: Зависимости
:: ============================================
echo [3/6] Установка зависимостей...
echo   (Это может занять 1-3 минуты)
echo.
call "%PROJECT_DIR%\venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r "%PROJECT_DIR%\requirements.txt" --quiet
if %errorlevel% neq 0 (
    echo.
    echo   [ОШИБКА] Не удалось установить зависимости!
    pause
    exit /b 1
)
echo   Зависимости установлены.
echo.

:: ============================================
:: ШАГ 4: Проверка конфигурации
:: ============================================
echo [4/6] Проверка конфигурации...
if not exist "%PROJECT_DIR%\.env" (
    echo.
    echo   [ВНИМАНИЕ] Файл .env не найден! Создаю шаблон...
    (
        echo BOT_TOKEN=вставьте_ваш_токен
        echo GROUP_CHAT_ID=-1001234567890
        echo CALENDAR_ID=ваш-calendar-id@group.calendar.google.com
        echo CALENDAR_LINK=https://calendar.google.com/calendar/u/0/r?cid=ваш-calendar-id
    ) > "%PROJECT_DIR%\.env"
    echo   Шаблон создан. Обязательно заполните файл .env!
) else (
    echo   Файл .env найден.
)
if not exist "%PROJECT_DIR%\credentials.json" (
    echo   [ВНИМАНИЕ] Файл credentials.json не найден!
    echo   Положите его в папку %PROJECT_DIR%
) else (
    echo   Файл credentials.json найден.
)
echo.

:: ============================================
:: ШАГ 5: Генерация скриптов запуска/остановки
:: ============================================
echo [5/6] Создание скриптов запуска и остановки...

:: --- start_bot.bat: защита от двойного запуска + остановка цикла
::     при обнаружении другой копии бота (код выхода 42).
::     БЕЗ перенаправления вывода python: логи пишет сам main.py в bot_log.txt
(
echo @echo off
echo title HoriBot
echo cd /d "%PROJECT_DIR%"
echo.
echo :: Не запускаем второй экземпляр, если бот уже работает
echo tasklist /fi "WINDOWTITLE eq HoriBot" 2^>nul ^| find /i "python.exe" ^>nul
echo if errorlevel 1 goto start
echo echo [ВНИМАНИЕ] Бот уже запущен. Второй экземпляр не стартует.
echo pause
echo exit /b 0
echo :start
echo.
echo :loop
echo echo [%%date%% %%time%%] Запуск бота... ^>^> bot_log.txt
echo call venv\Scripts\activate.bat
echo python main.py
echo if %%errorlevel%% equ 42 goto conflict
echo echo [%%date%% %%time%%] Бот остановился. Перезапуск через 5 секунд... ^>^> bot_log.txt
echo timeout /t 5 /nobreak ^>nul
echo goto loop
echo.
echo :conflict
echo echo [%%date%% %%time%%] Обнаружена другая копия бота. Цикл остановлен. ^>^> bot_log.txt
echo echo [ВНИМАНИЕ] Обнаружена другая копия бота с тем же токеном.
echo echo Закройте лишний экземпляр (stop_bot.bat или Диспетчер задач -^> python.exe),
echo echo затем запустите start_bot.bat заново.
echo pause
echo exit /b 1
) > "%PROJECT_DIR%\start_bot.bat"
echo   start_bot.bat создан.

:: --- start_bot_hidden.vbs: скрытый запуск без окна
(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo WshShell.Run chr^(34^) ^& "%PROJECT_DIR%\start_bot.bat" ^& chr^(34^), 0
echo Set WshShell = Nothing
) > "%PROJECT_DIR%\start_bot_hidden.vbs"
echo   start_bot_hidden.vbs создан.

:: --- stop_bot.bat: надёжная остановка (двойной taskkill на случай зависания)
(
echo @echo off
echo chcp 65001 ^>nul
echo echo Остановка HoriBot...
echo taskkill /f /fi "WINDOWTITLE eq HoriBot" ^>nul 2^>^&1
echo timeout /t 2 /nobreak ^>nul
echo taskkill /f /fi "WINDOWTITLE eq HoriBot" ^>nul 2^>^&1
echo echo Бот остановлен.
echo pause
) > "%PROJECT_DIR%\stop_bot.bat"
echo   stop_bot.bat создан.
echo.

:: ============================================
:: ШАГ 6: Автозапуск (schtasks одной строкой + резервный вариант)
:: ============================================
echo [6/6] Настройка автозапуска...
echo.

:: Чистим старые варианты, чтобы не дублировать
schtasks /delete /tn "HoriBot" /f >nul 2>&1
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start_bot_hidden.vbs" >nul 2>&1

:: Одна строка без переносов и без /rl — меньше точек отказа
schtasks /create /tn "HoriBot" /tr "wscript.exe \"%PROJECT_DIR%\start_bot_hidden.vbs\"" /sc onlogon /f

if %errorlevel% equ 0 (
    echo   Задача в Планировщике создана.
) else (
    echo   [ВНИМАНИЕ] Планировщик недоступен без прав администратора.
    echo   Использую резервный вариант: папка автозагрузки.
    copy /y "%PROJECT_DIR%\start_bot_hidden.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start_bot_hidden.vbs" >nul
    if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start_bot_hidden.vbs" (
        echo   Ярлык добавлен в автозагрузку — бот будет стартовать при входе.
    ) else (
        echo   [ОШИБКА] Не удалось добавить в автозагрузку автоматически.
        echo   Нажмите Win+R, введите shell:startup и скопируйте туда
        echo   файл start_bot_hidden.vbs вручную.
    )
)

:: ============================================
:: ИТОГ
:: ============================================
echo.
echo ============================================
echo   УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!
echo ============================================
echo.
echo   Что создано:
echo     - venv\                  (виртуальное окружение)
echo     - start_bot.bat          (запуск с перезапуском)
echo     - start_bot_hidden.vbs   (скрытый запуск)
echo     - stop_bot.bat           (остановка бота)
echo     - Автозапуск             (Планировщик или папка автозагрузки)
echo.
echo   Управление:
echo     Запустить: start_bot_hidden.vbs
echo     Остановить: stop_bot.bat
echo     Логи: bot_log.txt
echo.
echo   Удалить автозапуск:
echo     schtasks /delete /tn "HoriBot" /f
echo.

echo Запустить бота прямо сейчас? (Y/N)
set /p "ANSWER="
if /i "%ANSWER%"=="Y" (
    start "" wscript.exe "%PROJECT_DIR%\start_bot_hidden.vbs"
    echo Бот запущен! Проверьте его работу в Telegram.
    echo.
)
pause