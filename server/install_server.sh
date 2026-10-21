#!/usr/bin/env bash
# Установка HoriBot Thermal Guard на Ubuntu.
# Запуск: bash server/install_server.sh
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

echo "============================================"
echo "  HoriBot Thermal Guard — установка"
echo "============================================"
echo "Проект:  $PROJECT_DIR"
echo "Пользователь: $USER_NAME"
echo

command -v python3 >/dev/null 2>&1 || { echo "[ОШИБКА] Нужен python3: sudo apt install python3"; exit 1; }

mkdir -p "$PROJECT_DIR/server_state"

# --- sudoers: право ТОЛЬКО на выключение ---
echo "[1/3] Настройка sudoers (только systemctl poweroff)..."
SUDOERS_FILE="/etc/sudoers.d/horibot-thermal"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /bin/systemctl poweroff" \
    | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" > /dev/null || { echo "[ОШИБКА] Некорректный sudoers"; exit 1; }

# --- systemd-юнит ---
echo "[2/3] Установка systemd-юнита..."
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__USER__|$USER_NAME|g" \
    "$PROJECT_DIR/server/horibot-thermal-guard.service" \
    | sudo tee /etc/systemd/system/horibot-thermal-guard.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now horibot-thermal-guard

echo "[3/3] Готово."
echo
echo "Проверка состояния:  systemctl status horibot-thermal-guard"
echo "Лог защитника:       $PROJECT_DIR/server_state/thermal_guard.log"
echo "Безопасный тест:     python3 $PROJECT_DIR/server/thermal_guard.py --once --simulate 90"