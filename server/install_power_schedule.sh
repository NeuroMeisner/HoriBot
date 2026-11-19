#!/usr/bin/env bash
# Установка расписания питания с праздниками на Ubuntu.
# Запуск: bash server/install_power_schedule.sh
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

echo "============================================"
echo "  HoriBot — расписание питания + праздники"
echo "============================================"

mkdir -p "$PROJECT_DIR/server_state"

# --- 1. Библиотека праздников в venv ---
echo "[1/4] Установка библиотеки holidays..."
"$PROJECT_DIR/venv/bin/pip" install --quiet holidays

# --- 2. Шаблоны ручных списков ---
[ -f "$PROJECT_DIR/custom_holidays.json" ]  || echo '[]' > "$PROJECT_DIR/custom_holidays.json"
[ -f "$PROJECT_DIR/custom_working_days.json" ] || echo '[]' > "$PROJECT_DIR/custom_working_days.json"

# --- 3. sudoers: сон, выключение, rtcwake ---
echo "[2/4] Настройка sudoers..."
SUDOERS_FILE="/etc/sudoers.d/horibot-power"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl suspend, /bin/systemctl suspend, /usr/bin/systemctl poweroff, /bin/systemctl poweroff, /usr/sbin/rtcwake, /sbin/rtcwake" \
    | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" > /dev/null || { echo "[ОШИБКА] Некорректный sudoers"; exit 1; }

# --- 4. systemd-юниты ---
echo "[3/4] Установка systemd-юнитов..."
for f in horibot-wake.service horibot-wake.timer \
         horibot-offcheck.service horibot-offcheck.timer; do
    sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__USER__|$USER_NAME|g" \
        "$PROJECT_DIR/server/$f" | sudo tee "/etc/systemd/system/$f" > /dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now horibot-wake.timer
sudo systemctl enable --now horibot-offcheck.timer

echo "[4/4] Готово."
echo
echo "Таймеры:      systemctl list-timers | grep horibot"
echo "Расписание:   $PROJECT_DIR/venv/bin/python $PROJECT_DIR/power_schedule.py --print-schedule 14"
echo "Проверка RTC: см. инструкцию по BIOS ниже"