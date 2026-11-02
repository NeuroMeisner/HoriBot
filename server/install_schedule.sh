#!/usr/bin/env bash
# Установка расписания питания и автозапуска HoriBot на Ubuntu.
# Запуск: bash server/install_schedule.sh
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

echo "============================================"
echo "  HoriBot — расписание питания и автозапуск"
echo "============================================"

mkdir -p "$PROJECT_DIR/server_state"

# --- sudoers: сон, выключение, RTC-будильник ---
echo "[1/3] Настройка sudoers..."
SUDOERS_FILE="/etc/sudoers.d/horibot-power"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl suspend, /bin/systemctl suspend, /usr/bin/systemctl poweroff, /bin/systemctl poweroff, /usr/sbin/rtcwake, /sbin/rtcwake" \
    | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" > /dev/null || { echo "[ОШИБКА] Некорректный sudoers"; exit 1; }

# --- systemd-юниты ---
echo "[2/3] Установка systemd-юнитов..."
for f in horibot.service horibot-wake.service horibot-wake.timer \
         horibot-weekend-off.service horibot-weekend-off.timer; do
    sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__USER__|$USER_NAME|g" \
        "$PROJECT_DIR/server/$f" | sudo tee "/etc/systemd/system/$f" > /dev/null
done

sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/server/weekend_off_check.sh" \
    | sudo tee /usr/local/bin/horibot-weekend-off-check.sh > /dev/null
sudo chmod +x /usr/local/bin/horibot-weekend-off-check.sh

sudo systemctl daemon-reload
sudo systemctl enable --now horibot.service
sudo systemctl enable --now horibot-wake.timer
sudo systemctl enable --now horibot-weekend-off.timer

echo "[3/3] Готово."
echo
echo "Проверка таймеров:   systemctl list-timers | grep horibot"
echo "Логи бота:           journalctl -u horibot -f   и папка logs/"
echo "Разрешить wake-таймеры: sudo powercfg не нужен; проверьте:"
echo "  cat /sys/class/rtc/rtc0/wake_alarm_after_suspend 2>/dev/null || echo ok"