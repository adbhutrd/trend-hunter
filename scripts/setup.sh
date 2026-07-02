#!/usr/bin/env bash
# trend-hunter — interactive first-time setup
# Run: bash scripts/setup.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_step() { echo -e "\n${BOLD}${CYAN}▸${NC} ${BOLD}$1${NC}"; }
print_ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
print_warn() { echo -e "  ${YELLOW}!${NC} $1"; }
print_info() { echo -e "  $1"; }

ENV_FILE=".env"

# ── banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║       🏹  trend-hunter — setup          ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo "  This wizard helps you configure alerts and preferences."
echo "  Everything is optional — trend-hunter runs fine with defaults."
echo ""

# ── ensure .env exists ────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    print_step "Creating $ENV_FILE from .env.example"
    cp .env.example "$ENV_FILE"
    print_ok "$ENV_FILE created"
else
    print_ok "$ENV_FILE already exists — we'll update it"
fi

# ── helper: upsert env var ────────────────────────────────────────────────────
upsert_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# ── step 1: Discord webhook ───────────────────────────────────────────────────
print_step "1/3 — Discord alerts (optional)"
echo ""
echo "  To get alerts in Discord:"
echo "    → Discord → Server Settings → Integrations → Webhooks"
echo "    → New Webhook → name: 'trend-hunter' → Copy URL"
echo ""
read -r -p "  Paste webhook URL (or press Enter to skip): " discord_url

if [ -n "$discord_url" ]; then
    upsert_env "TH_DISCORD_WEBHOOK" "$discord_url"
    print_ok "Discord webhook saved"
else
    print_info "Skipped — alerts will only work once you set TH_DISCORD_WEBHOOK"
fi

# ── step 2: Telegram bot ──────────────────────────────────────────────────────
print_step "2/3 — Telegram alerts (optional)"
echo ""
echo "  To get alerts in Telegram:"
echo "    → DM @BotFather → /newbot → name it → get bot token"
echo "    → DM your bot once, then visit:"
echo "      https://api.telegram.org/bot<TOKEN>/getUpdates"
echo "    → Find 'chat': {'id': 123456789} — that's your chat_id"
echo ""
read -r -p "  Paste bot token (or press Enter to skip): " tg_token

if [ -n "$tg_token" ]; then
    upsert_env "TH_TELEGRAM_BOT_TOKEN" "$tg_token"

    read -r -p "  Paste chat ID: " tg_chat_id
    if [ -n "$tg_chat_id" ]; then
        upsert_env "TH_TELEGRAM_CHAT_ID" "$tg_chat_id"
        print_ok "Telegram bot + chat ID saved"
    else
        print_warn "Telegram bot token saved but no chat ID — incomplete config"
    fi
else
    print_info "Skipped — alerts will only work once you set TH_TELEGRAM_BOT_TOKEN + TH_TELEGRAM_CHAT_ID"
fi

# ── step 3: test alerts ───────────────────────────────────────────────────────
print_step "3/3 — Test alert delivery"
echo ""

HAS_DISCORD=$(grep -c "^TH_DISCORD_WEBHOOK=.\+" "$ENV_FILE" 2>/dev/null || true)
HAS_TG_TOKEN=$(grep -c "^TH_TELEGRAM_BOT_TOKEN=.\+" "$ENV_FILE" 2>/dev/null || true)
HAS_TG_CHAT=$(grep -c "^TH_TELEGRAM_CHAT_ID=.\+" "$ENV_FILE" 2>/dev/null || true)

if [ "$HAS_DISCORD" -gt 0 ] || { [ "$HAS_TG_TOKEN" -gt 0 ] && [ "$HAS_TG_CHAT" -gt 0 ]; }; then
    PROJECT_DIR="$(dirname "$0")/.."
    if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
        print_warn "No .venv found — run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
        print_info "Then re-run: bash scripts/setup.sh"
    else
        echo "  Sending a test alert now..."
        if cd "$PROJECT_DIR" && .venv/bin/python -c "
import os
from trend_hunter.observe.alerts import notify
notify('✅ trend-hunter setup complete — alerts are working!')
print('  Test alert sent. Check your Discord/Telegram.')
"; then
            print_ok "Test alert dispatched"
        else
            print_warn "Test alert may have failed — check your credentials"
        fi
    fi
else
    print_info "No alert channels configured — skipping test"
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}  ✓ Setup complete!${NC}"
echo ""
echo "  Quick commands to try:"
echo "    make scan              # find trending products"
echo "    make run               # full pipeline: scan → match → draft"
echo "    make money-sweep       # top 3 profitable picks + auto-draft"
echo "    make loop-report       # 6-section feedback report"
echo "    make alert             # check + push degradation alerts"
echo "    make dash              # Streamlit dashboard"
echo ""
echo "  To update config later:  edit .env  or re-run: bash scripts/setup.sh"
echo ""
