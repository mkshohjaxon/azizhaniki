#!/bin/bash
# ╔══════════════════════════════════════════════════════╗
# ║     TG Account Manager — Tez ishga tushiruvchi       ║
# ╚══════════════════════════════════════════════════════╝

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8010
VENV="$DIR/venv"
URL="http://127.0.0.1:$PORT/7878/"

# Ranglar
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║    📱 TG Account Manager             ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════╝${NC}"
echo ""

# 1. Python tekshirish
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ Python3 topilmadi!${NC}"
    exit 1
fi

# 2. Venv tekshirish / yaratish
if [ ! -d "$VENV" ]; then
    echo -e "${YELLOW}📦 Virtual muhit yaratilmoqda...${NC}"
    python3 -m venv "$VENV"
    source "$VENV/bin/activate"
    pip install -q telethon aiosqlite fastapi uvicorn cryptography
else
    source "$VENV/bin/activate"
fi

# 3. Port band bo'lsa — eski jarayonni to'xtatish
if command -v lsof &>/dev/null && lsof -i :"$PORT" -t &>/dev/null; then
    echo -e "${YELLOW}⚠️  Port $PORT band — eski jarayon to'xtatilmoqda...${NC}"
    if systemctl --user is-active tg-account-manager &>/dev/null 2>&1; then
        systemctl --user stop tg-account-manager 2>/dev/null || true
    fi
    kill $(lsof -i :"$PORT" -t) 2>/dev/null || true
    sleep 1
elif command -v fuser &>/dev/null && fuser "$PORT/tcp" &>/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port $PORT band — eski jarayon to'xtatilmoqda...${NC}"
    fuser -k "$PORT/tcp" 2>/dev/null || true
    sleep 1
fi

# 4. Serverni ishga tushirish
echo -e "${CYAN}🚀 Server ishga tushirilmoqda (port $PORT)...${NC}"

cd "$DIR"
nohup "$VENV/bin/uvicorn" api:root \
    --host 127.0.0.1 \
    --port "$PORT" \
    --timeout-graceful-shutdown 5 \
    --proxy-headers \
    --forwarded-allow-ips 127.0.0.1 \
    > "$DIR/server.log" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$DIR/.server.pid"

# 5. Server tayyor bo'lguncha kutish
echo -ne "${CYAN}   Kutilmoqda"
READY=0
for i in $(seq 1 20); do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qE "200|301|302|307"; then
        READY=1
        break
    fi
    echo -n "."
    sleep 1
done
echo -e "${NC}"

# 6. Muvaffaqiyatni tekshirish
if [ "$READY" = "1" ] || kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "${GREEN}${BOLD}✅ Server ishlayapti!${NC}"
    echo ""
    echo -e "   ${BOLD}Manzil:${NC}  ${CYAN}$URL${NC}"
    echo -e "   ${BOLD}PID:${NC}     $SERVER_PID"
    echo -e "   ${BOLD}Log:${NC}     $DIR/server.log"
    echo ""

    # 7. Brauzerni avtomatik ochish
    echo -e "${CYAN}🌐 Brauzer ochilmoqda...${NC}"
    sleep 1
    if command -v xdg-open &>/dev/null; then
        xdg-open "$URL" 2>/dev/null &
    elif command -v gnome-open &>/dev/null; then
        gnome-open "$URL" 2>/dev/null &
    elif command -v open &>/dev/null; then
        open "$URL" 2>/dev/null &
    else
        echo -e "${YELLOW}   Brauzerni qo'lda oching: $URL${NC}"
    fi

    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${BOLD}To'xtatish:${NC}  ./stop.sh"
    echo -e "  ${BOLD}Log ko'rish:${NC} tail -f server.log"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
else
    echo -e "${RED}❌ Server ishga tushmadi!${NC}"
    echo -e "   Log:"
    tail -5 "$DIR/server.log" 2>/dev/null
    exit 1
fi
