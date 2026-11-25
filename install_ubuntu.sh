#!/bin/bash

# =========================================================
# HoriBot Ubuntu Installer
# Устанавливает бота как системный сервис с автозапуском
# =========================================================

set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Установка HoriBot для Ubuntu ===${NC}"

# Проверка прав
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Скрипт нужно запускать с правами суперпользователя: sudo ./install_ubuntu.sh${NC}"
  exit 1
fi

# Определяем пользователя, от имени которого запущен скрипт (чтобы не запускать бота от root)
if [ -n "$SUDO_USER" ]; then
  USER=$SUDO_USER
  USER_HOME=$(eval echo ~$SUDO_USER)
else
  USER=$USER
  USER_HOME=$HOME
fi

PROJECT_DIR=$(pwd)
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_FILE="/etc/systemd/system/horibot.service"
THERMAL_SERVICE_FILE="/etc/systemd/system/horibot-thermal.service"
PROXY_SERVICE_FILE="/etc/systemd/system/tgwsapiproxy.service"

echo -e "${YELLOW}Директория проекта: $PROJECT_DIR${NC}"
echo -e "${YELLOW}Пользователь: $USER${NC}"
echo ""

# ============================================
# ПРОВЕРКА ПРЕДЫДУЩЕЙ УСТАНОВКИ
# ============================================
echo -e "${BLUE}[0/7] Проверка предыдущей установки...${NC}"

SERVICES=("horibot" "tgwsapiproxy" "horibot-thermal")
SERVICES_REMOVED=0

for service in "${SERVICES[@]}"; do
    service_file="/etc/systemd/system/${service}.service"
    
    if [ -f "$service_file" ]; then
        echo -e "${YELLOW}Найден сервис: $service${NC}"
        
        # Проверяем, активен ли сервис
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            echo -e "${YELLOW}  Останавливаю сервис $service...${NC}"
            systemctl stop "$service" 2>/dev/null || true
            SERVICES_REMOVED=$((SERVICES_REMOVED + 1))
        fi
        
        # Отключаем автозапуск
        if systemctl is-enabled --quiet "$service" 2>/dev/null; then
            echo -e "${YELLOW}  Отключаю автозапуск $service...${NC}"
            systemctl disable "$service" 2>/dev/null || true
        fi
        
        # Удаляем файл сервиса
        echo -e "${YELLOW}  Удаляю файл сервиса...${NC}"
        rm -f "$service_file"
        SERVICES_REMOVED=$((SERVICES_REMOVED + 1))
    fi
done

if [ $SERVICES_REMOVED -gt 0 ]; then
    echo -e "${GREEN}Обновляю systemd daemon...${NC}"
    systemctl daemon-reload
    echo -e "${GREEN}Предыдущая установка удалена!${NC}"
else
    echo -e "${GREEN}Предыдущая установка не обнаружена.${NC}"
fi
echo ""

# 1. Установка системных зависимостей
echo -e "${GREEN}[1/7] Установка системных пакетов...${NC}"
apt-get update
apt-get install -y python3 python3-venv python3-pip git util-linux

# 2. Создание виртуального окружения
echo -e "${GREEN}[2/7] Настройка окружения Python...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    sudo -u $USER python3 -m venv "$VENV_DIR"
fi

# Установка зависимостей проекта
sudo -u $USER "$VENV_DIR/bin/pip" install --upgrade pip
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    sudo -u $USER "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
else
    echo -e "${RED}Ошибка: файл requirements.txt не найден!${NC}"
    exit 1
fi

# 3. Настройка .env
echo -e "${GREEN}[3/7] Проверка конфигурации...${NC}"
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        chown $USER:$USER "$PROJECT_DIR/.env"
        echo -e "${YELLOW}Создан файл .env. Пожалуйста, заполните его перед запуском!${NC}"
    else
        echo -e "${RED}.env.example не найден. Создайте .env вручную.${NC}"
    fi
fi

# 4. Установка и настройка TgWsApiProxy
echo -e "${GREEN}[4/7] Установка TgWsApiProxy...${NC}"
sudo -u $USER "$VENV_DIR/bin/pip" install "git+https://github.com/winchanii/TgWsApiProxy#egg=tgwsapiproxy[aiogram]"

# Читаем домен Cloudflare Worker из .env
PROXY_DOMAIN=""
if [ -f "$PROJECT_DIR/.env" ]; then
    # Ищем переменную PROXY_WORKER
    PROXY_DOMAIN=$(grep -E "^PROXY_WORKER=" "$PROJECT_DIR/.env" | cut -d'=' -f2 | tr -d '"' | tr -d "'" | head -n1)
fi

if [ -z "$PROXY_DOMAIN" ]; then
    echo -e "${RED}Внимание: не найден домен Cloudflare Worker в .env!${NC}"
    echo -e "${RED}Пожалуйста, добавьте переменную PROXY_WORKER=your-worker.workers.dev в .env${NC}"
    echo -e "${YELLOW}Использую значение по умолчанию: your-worker.workers.dev${NC}"
    PROXY_DOMAIN="your-worker.workers.dev"
else
    echo -e "${GREEN}Найден домен Cloudflare Worker: $PROXY_DOMAIN${NC}"
fi

# 5. Настройка прав для управления питанием (systemctl и rtcwake)
echo -e "${GREEN}[5/7] Настройка прав sudo для управления питанием...${NC}"
SUDOERS_FILE="/etc/sudoers.d/horibot"

# Удаляем старый sudoers файл, если он существует
if [ -f "$SUDOERS_FILE" ]; then
    rm -f "$SUDOERS_FILE"
fi

cat <<EOF > $SUDOERS_FILE
# Разрешаем пользователю $USER управлять питанием без пароля
$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl suspend
$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff
$USER ALL=(ALL) NOPASSWD: /usr/sbin/rtcwake
EOF
chmod 440 $SUDOERS_FILE

# 6. Создание systemd-сервиса для TgWsApiProxy
echo -e "${GREEN}[6/7] Создание сервиса TgWsApiProxy...${NC}"
cat <<EOF > $PROXY_SERVICE_FILE
[Unit]
Description=Telegram WS API Proxy for HoriBot
After=network.target
Before=horibot.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/tgwsapiproxy --worker-host $PROXY_DOMAIN
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tgwsapiproxy

[Install]
WantedBy=multi-user.target
EOF

# 7. Создание systemd-сервиса для бота
echo -e "${GREEN}[7/7] Создание сервиса HoriBot...${NC}"
cat <<EOF > $SERVICE_FILE
[Unit]
Description=HoriBot Telegram Equipment Booking Bot
After=network.target tgwsapiproxy.service
Requires=tgwsapiproxy.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
# Запуск бота в виртуальном окружении
ExecStart=$VENV_DIR/bin/python main.py
# Автоматический перезапуск при падении
Restart=always
RestartSec=10
# Логи будут доступны через journalctl
StandardOutput=journal
StandardError=journal
SyslogIdentifier=horibot

[Install]
WantedBy=multi-user.target
EOF

# Поиск и настройка сервиса термозащиты (если есть в папке server/)
echo -e "${YELLOW}Проверка термозащиты...${NC}"
THERMAL_SCRIPT=""
if [ -f "$PROJECT_DIR/server/thermal_guard.py" ]; then
    THERMAL_SCRIPT="$PROJECT_DIR/server/thermal_guard.py"
elif [ -f "$PROJECT_DIR/server/thermal_guard.sh" ]; then
    THERMAL_SCRIPT="$PROJECT_DIR/server/thermal_guard.sh"
fi

if [ -n "$THERMAL_SCRIPT" ]; then
    echo -e "${YELLOW}Найден скрипт термозащиты: $THERMAL_SCRIPT${NC}"
    
    if [[ "$THERMAL_SCRIPT" == *.py ]]; then
        EXEC_CMD="$VENV_DIR/bin/python $THERMAL_SCRIPT"
    else
        EXEC_CMD="/bin/bash $THERMAL_SCRIPT"
    fi

    cat <<EOF > $THERMAL_SERVICE_FILE
[Unit]
Description=HoriBot Thermal Guard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$EXEC_CMD
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=horibot-thermal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable horibot-thermal
    systemctl start horibot-thermal
    echo -e "${GREEN}Сервис термозащиты запущен.${NC}"
else
    echo -e "${YELLOW}Скрипт термозащиты в папке server/ не найден. Пропускаем.${NC}"
fi

# Активация и запуск сервисов
systemctl daemon-reload
systemctl enable tgwsapiproxy
systemctl enable horibot
systemctl start tgwsapiproxy
sleep 2
systemctl start horibot

echo ""
echo -e "${GREEN}=== Установка завершена успешно! ===${NC}"
echo -e "Бот и прокси настроены на автозапуск при включении системы."
echo -e "Прокси запускается автоматически до бота."
echo ""
echo -e "${RED}Важно: Проверьте файл .env перед использованием!${NC}"
echo -e "${RED}Убедитесь, что переменная PROXY_WORKER указана корректно!${NC}"
echo ""
echo -e "${YELLOW}=== ПОЛЕЗНЫЕ КОМАНДЫ ===${NC}"
echo ""
echo -e "${GREEN}Управление ботом:${NC}"
echo -e "  Статус бота:                    sudo systemctl status horibot"
echo -e "  Остановить бота:                sudo systemctl stop horibot"
echo -e "  Запустить бота:                 sudo systemctl start horibot"
echo -e "  Перезапустить бота:             sudo systemctl restart horibot"
echo -e "  Отключить автозапуск:           sudo systemctl disable horibot"
echo -e "  Включить автозапуск:            sudo systemctl enable horibot"
echo ""
echo -e "${GREEN}Управление прокси:${NC}"
echo -e "  Статус прокси:                  sudo systemctl status tgwsapiproxy"
echo -e "  Остановить прокси:              sudo systemctl stop tgwsapiproxy"
echo -e "  Запустить прокси:               sudo systemctl start tgwsapiproxy"
echo -e "  Перезапустить прокси:           sudo systemctl restart tgwsapiproxy"
echo ""
echo -e "${GREEN}Просмотр логов:${NC}"
echo -e "  Логи бота в реальном времени:   journalctl -u horibot -f"
echo -e "  Логи бота с последней загрузки: journalctl -u horibot -b"
echo -e "  Логи прокси:                    journalctl -u tgwsapiproxy -f"
echo -e "  Все логи системы:               journalctl -f"
echo ""
echo -e "${GREEN}Термозащита (если настроена):${NC}"
echo -e "  Статус:                         sudo systemctl status horibot-thermal"
echo -e "  Логи:                           journalctl -u horibot-thermal -f"
echo ""