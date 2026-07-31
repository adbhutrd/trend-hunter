#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# install-service.sh — Install buffy-scheduler as a systemd user service
#
# Usage:
#   ./scripts/install-service.sh              # install + enable + start
#   ./scripts/install-service.sh --dry-run     # preview paths
#   ./scripts/install-service.sh --uninstall   # stop + disable + remove
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="buffy-scheduler"
SERVICE_SRC="$PROJECT_DIR/$SERVICE_NAME.service"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_DST="$SYSTEMD_DIR/$SERVICE_NAME.service"

# ── helpers ──────────────────────────────────────────────────────────────────
info()  { printf "  \033[36mℹ\033[0m  %s\n" "$*"; }
ok()    { printf "  \033[32m✓\033[0m  %s\n" "$*"; }
err()   { printf "  \033[31m✗\033[0m  %s\n" "$*" >&2; }

# ── uninstall ────────────────────────────────────────────────────────────────
do_uninstall() {
    echo ""
    info "Uninstalling $SERVICE_NAME service..."

    if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
        systemctl --user stop "$SERVICE_NAME"
        ok "Service stopped"
    fi

    if systemctl --user is-enabled "$SERVICE_NAME" &>/dev/null 2>&1; then
        systemctl --user disable "$SERVICE_NAME"
        ok "Service disabled"
    fi

    if [ -f "$SERVICE_DST" ]; then
        rm -f "$SERVICE_DST"
        ok "Service file removed: $SERVICE_DST"
    fi

    systemctl --user daemon-reload
    ok "Daemon reloaded"
    echo ""
    info "Done. Service removed."
    exit 0
}

# ── main ─────────────────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   buffy-scheduler — Systemd User Service        ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

# Parse flags
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --uninstall) do_uninstall ;;
        *) err "Unknown flag: $arg"; exit 1 ;;
    esac
done

# Check prerequisites
if [ ! -f "$SERVICE_SRC" ]; then
    err "Service file not found: $SERVICE_SRC"
    err "Are you running from the project root?"
    exit 1
fi

if ! command -v systemctl &>/dev/null; then
    err "systemctl not found — not a systemd system?"
    err "Falling back to: no service installation."
    info "You can still run: make schedule"
    exit 0
fi

info "Project dir:  $PROJECT_DIR"
info "Service file:  $SERVICE_SRC"
info "Install to:    $SERVICE_DST"

if [ "$DRY_RUN" = true ]; then
    info "Dry-run mode — no changes made."
    exit 0
fi

# Create systemd user directory
mkdir -p "$SYSTEMD_DIR"

# Copy service file with HOME placeholder replaced
cp "$SERVICE_SRC" "$SERVICE_DST"
ok "Copied service file to $SERVICE_DST"

# Reload systemd user daemon
systemctl --user daemon-reload
ok "Daemon reloaded"

# Enable boot-start
systemctl --user enable "$SERVICE_NAME"
ok "Service enabled (boot-start)"

# Start now
systemctl --user start "$SERVICE_NAME"
ok "Service started"

echo ""
info "Status check:"
systemctl --user --no-pager status "$SERVICE_NAME" 2>&1 | head -15
echo ""
info "✅ Service installed and running!"
info "   View logs:  journalctl --user -u $SERVICE_NAME -f"
info "   Stop:       systemctl --user stop $SERVICE_NAME"
info "   Restart:    systemctl --user restart $SERVICE_NAME"
info "   Disable:    systemctl --user disable $SERVICE_NAME"
echo ""
