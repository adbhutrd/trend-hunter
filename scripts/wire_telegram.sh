#!/usr/bin/env bash
# trend-hunter — one-shot Telegram bot wiring
# Usage:  bash scripts/wire_telegram.sh <BOT_TOKEN>
#
# Steps:
#   1. Validate the token via getMe
#   2. Pull chat_id from the latest private update from a non-bot user
#      (prefer existing TH_TELEGRAM_CHAT_ID if already in .env, re-run is idempotent)
#   3. Write TH_TELEGRAM_BOT_TOKEN + TH_TELEGRAM_CHAT_ID into .env
#   4. Send a test message to confirm round-trip works
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

if [ $# -ne 1 ]; then
    echo -e "${RED}Usage: $0 <BOT_TOKEN>${NC}" >&2
    echo "  Get the token from @BotFather after /newbot" >&2
    echo "  Then DM your bot once (e.g. /start) so it has a chat history" >&2
    exit 1
fi

TOKEN="$1"
ENV_FILE=".env"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── ensure .env exists ────────────────────────────────────────────────────────
[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"

# ── helper: upsert env var (handles optional `export ` prefix, safe delimiter) ─
upsert_env() {
    local key="$1" val="$2"
    if grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" 2>/dev/null; then
        # `#` delimiter — safer than `|` (token chars can't contain it)
        sed -i "s#^[[:space:]]*\(export[[:space:]]\+\)\?${key}=.*#\1${key}=${val}#g" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
}

# ── step 1: validate token via getMe ─────────────────────────────────────────
echo -e "${BOLD}▸ Validating token via getMe...${NC}"
# Capture only stdout; stderr (any curl errors) goes to terminal directly so the
# token is never echoed back via error capture.
ME_JSON=$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe") || {
    echo -e "${RED}✗ getMe failed — invalid token or network issue${NC}" >&2
    exit 1
}
BOT_NAME=$(printf '%s' "$ME_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if not data.get('ok'):
    sys.stderr.write(data.get('description', 'unknown error') + '\n'); sys.exit(2)
print(data['result']['username'])
") || { echo -e "${RED}✗ BotFather rejected the token${NC}" >&2; exit 1; }
echo -e "  ${GREEN}✓${NC} Bot: @$BOT_NAME"

# ── step 2: extract chat_id from getUpdates ───────────────────────────────────
echo -e "${BOLD}▸ Polling getUpdates for chat_id...${NC}"
UPD_JSON=$(curl -fsS "https://api.telegram.org/bot${TOKEN}/getUpdates") || {
    echo -e "${RED}✗ getUpdates failed${NC}" >&2; exit 1
}

# Prefer an existing TH_TELEGRAM_CHAT_ID in .env so re-runs are idempotent on
# the same user.  Otherwise pick the latest private chat from a non-bot user.
EXISTING_CHAT_ID=""
if grep -qE "^[[:space:]]*(export[[:space:]]+)?TH_TELEGRAM_CHAT_ID=" "$ENV_FILE" 2>/dev/null; then
    EXISTING_CHAT_ID=$(sed -nE "s#^[[:space:]]*(export[[:space:]]+)?TH_TELEGRAM_CHAT_ID=##p" "$ENV_FILE" | head -1 | tr -d '[:space:]')
fi

CHAT_ID=$(EXISTING="$EXISTING_CHAT_ID" python3 -c "
import os, sys, json
data = json.load(sys.stdin)
if not data.get('ok'):
    sys.stderr.write('API error: ' + data.get('description', '') + '\n'); sys.exit(3)
updates = data.get('result', []) or []
existing = os.environ.get('EXISTING', '').strip()
# Prefer the existing chat_id if it's already present in the update stream.
for u in updates:
    msg = u.get('message') or u.get('channel_post') or {}
    chat = msg.get('chat') or {}
    if existing and str(chat.get('id', '')) == existing:
        print(existing); sys.exit(0)
# Otherwise pick the latest private chat from a non-bot user.
for u in reversed(updates):
    msg = u.get('message') or {}
    if (msg.get('from') or {}).get('is_bot'):
        continue
    chat = msg.get('chat') or {}
    if chat.get('type') == 'private':
        print(chat['id']); sys.exit(0)
sys.stderr.write('NO_PRIVATE_USER_MSG\n'); sys.exit(4)
") || {
    rc=$?
    if [ "$rc" -eq 4 ]; then
        echo -e "${YELLOW}! No private user messages yet — DM your bot first (e.g. send /start), then re-run${NC}"
        echo -e "  Send a message to @$BOT_NAME from your Telegram app, then:"
        echo -e "  ${BOLD}bash scripts/wire_telegram.sh \"$TOKEN\"${NC}"
        exit 2
    fi
    exit "$rc"
}
echo -e "  ${GREEN}✓${NC} chat_id: $CHAT_ID"

# ── step 3: write to .env ─────────────────────────────────────────────────────
echo -e "${BOLD}▸ Writing to .env...${NC}"
upsert_env "TH_TELEGRAM_BOT_TOKEN" "$TOKEN"
upsert_env "TH_TELEGRAM_CHAT_ID"   "$CHAT_ID"
# Lock down .env now that it contains a bot token.
chmod 600 "$ENV_FILE"
echo -e "  ${GREEN}✓${NC} .env updated (permissions: 600)"

# ── step 4: dispatch test alert via trend-hunter ──────────────────────────────
echo -e "${BOLD}▸ Sending test alert via trend-hunter...${NC}"
cd "$PROJECT_DIR"
if [ ! -f ".venv/bin/python" ]; then
    echo -e "${YELLOW}! No .venv found — alert not dispatched${NC}"
    echo "  Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 0
fi
.venv/bin/python -c "
from trend_hunter.observe.alerts import notify
notify('✅ trend-hunter wired up — Telegram alerts are live!')
print('Sent.')
" && echo -e "  ${GREEN}✓${NC} Check your Telegram now" \
  || echo -e "${RED}✗${NC} dispatch failed — check notify() output above"

echo
echo -e "${GREEN}${BOLD}✓ Done.${NC} Telegram is wired."
echo "  trend-hunter will now alert you on scaffold/scan/aggregate degradation."
