#!/bin/bash
# TG Account Manager — To'xtatuvchi

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8010

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}🛑 TG Account Manager to'xtatilmoqda...${NC}"

# Systemd servisni to'xtatish
if systemctl --user is-active tg-account-manager &>/dev/null; then
    systemctl --user stop tg-account-manager
    echo -e "${GREEN}✅ Systemd servis to'xtatildi${NC}"
fi

# PID fayldan to'xtatish
if [ -f "$DIR/.server.pid" ]; then
    PID=$(cat "$DIR/.server.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        echo -e "${GREEN}✅ Server (PID $PID) to'xtatildi${NC}"
    fi
    rm -f "$DIR/.server.pid"
fi

# Portni tozalash
if lsof -i :"$PORT" -t &>/dev/null; then
    kill $(lsof -i :"$PORT" -t) 2>/dev/null
    echo -e "${GREEN}✅ Port $PORT bo'shatildi${NC}"
fi

echo -e "${GREEN}✅ Tayyor${NC}"
