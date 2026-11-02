#!/usr/bin/env bash
# Резервное выключение в пятницу: срабатывает только если бот сам
# не ушёл в режим weekend к 23:15 (например, бот мёртв).
STATE="__PROJECT_DIR__/server_state/power_state.json"
MODE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode',''))" "$STATE" 2>/dev/null)
if [ "$MODE" != "weekend" ]; then
    logger -t horibot-weekend-off "Бот не ушёл в weekend — выключаю машину резервно"
    systemctl poweroff
fi
exit 0