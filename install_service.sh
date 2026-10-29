#!/usr/bin/env bash
# =============================================================================
# install_service.sh — Install / manage iCal Bot as a background systemd service.
#
# Usage:
#   sudo bash install_service.sh install    # Install & enable system-wide service
#   bash install_service.sh install --user  # Install as a user service (no sudo required)
#   sudo bash install_service.sh uninstall  # Stop and remove service
#   bash install_service.sh uninstall --user
#   bash install_service.sh status [--user] # Check status
#   bash install_service.sh restart [--user]# Restart service
#   bash install_service.sh logs [--user]   # Follow live logs
# =============================================================================

set -euo pipefail

SERVICE_NAME="ical-bot"
SERVICE_FILE="${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_UNIT="${SCRIPT_DIR}/${SERVICE_FILE}"

IS_USER_MODE=false
for arg in "$@"; do
  if [[ "$arg" == "--user" ]]; then
    IS_USER_MODE=true
  fi
done

ACTION="${1:-}"

# Detect execution context
if [[ "$IS_USER_MODE" == true ]]; then
  TARGET_USER="$(whoami)"
  TARGET_GROUP="$(id -gn)"
  TARGET_HOME="${HOME}"
  SYSTEMD_CMD="systemctl --user"
  JOURNAL_CMD="journalctl --user-unit ${SERVICE_NAME} -f"
  DEST_DIR="${TARGET_HOME}/.config/systemd/user"
  DEST_UNIT="${DEST_DIR}/${SERVICE_FILE}"
else
  if [[ $EUID -ne 0 && ("$ACTION" == "install" || "$ACTION" == "uninstall") ]]; then
    echo "ERROR: Installing system-wide service requires root. Run with sudo or use '--user':"
    echo "   sudo bash $0 $ACTION"
    echo "   bash $0 $ACTION --user"
    exit 1
  fi
  TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
  TARGET_GROUP="$(id -gn "$TARGET_USER" 2>/dev/null || echo "$TARGET_USER")"
  TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || echo "/home/${TARGET_USER}")"
  SYSTEMD_CMD="systemctl"
  JOURNAL_CMD="journalctl -u ${SERVICE_NAME} -f"
  DEST_DIR="/etc/systemd/system"
  DEST_UNIT="${DEST_DIR}/${SERVICE_FILE}"
fi

# Detect uv or python in venv
UV_PATH="$(which uv 2>/dev/null || echo "")"
if [[ -z "$UV_PATH" && -x "${TARGET_HOME}/.local/bin/uv" ]]; then
  UV_PATH="${TARGET_HOME}/.local/bin/uv"
fi

if [[ -n "$UV_PATH" && -x "$UV_PATH" ]]; then
  EXEC_START="${UV_PATH} run run.py"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
  EXEC_START="${SCRIPT_DIR}/.venv/bin/python run.py"
else
  EXEC_START="/usr/bin/python3 run.py"
fi

case "$ACTION" in
  install)
    echo "==> Installing iCal Bot background service (${IS_USER_MODE:+user}${IS_USER_MODE:-system})..."
    echo "    User:       ${TARGET_USER}"
    echo "    Group:      ${TARGET_GROUP}"
    echo "    Home:       ${TARGET_HOME}"
    echo "    Directory:  ${SCRIPT_DIR}"
    echo "    Command:    ${EXEC_START}"

    mkdir -p "$DEST_DIR"

    # Template the unit file
    sed -e "s|User=.*|User=${TARGET_USER}|g" \
        -e "s|Group=.*|Group=${TARGET_GROUP}|g" \
        -e "s|WorkingDirectory=.*|WorkingDirectory=${SCRIPT_DIR}|g" \
        -e "s|ExecStart=.*|ExecStart=${EXEC_START}|g" \
        -e "s|EnvironmentFile=-.*|EnvironmentFile=-${SCRIPT_DIR}/.env|g" \
        -e "s|Environment=HOME=.*|Environment=HOME=${TARGET_HOME}|g" \
        -e "s|Environment=PATH=.*|Environment=PATH=${TARGET_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin|g" \
        "$SOURCE_UNIT" > "$DEST_UNIT"

    # In user mode, User= and Group= directives must not be present, and target is default.target
    if [[ "$IS_USER_MODE" == true ]]; then
      sed -i '/^User=/d' "$DEST_UNIT"
      sed -i '/^Group=/d' "$DEST_UNIT"
      sed -i 's|WantedBy=multi-user.target|WantedBy=default.target|g' "$DEST_UNIT"
    fi

    echo "    Installed unit file to: ${DEST_UNIT}"

    # Reload systemd and start service
    $SYSTEMD_CMD daemon-reload
    $SYSTEMD_CMD enable "$SERVICE_NAME"
    $SYSTEMD_CMD restart "$SERVICE_NAME"

    echo "==> Service installed, enabled, and started!"
    echo ""
    echo "Useful commands:"
    echo "  $SYSTEMD_CMD status ${SERVICE_NAME}    # Check service status"
    echo "  $JOURNAL_CMD                       # Follow live logs"
    echo "  $SYSTEMD_CMD restart ${SERVICE_NAME}   # Restart service"
    echo "  $SYSTEMD_CMD stop ${SERVICE_NAME}      # Stop service"
    ;;

  uninstall)
    echo "==> Uninstalling iCal Bot background service..."
    $SYSTEMD_CMD stop "$SERVICE_NAME" 2>/dev/null || true
    $SYSTEMD_CMD disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$DEST_UNIT"
    $SYSTEMD_CMD daemon-reload
    echo "==> Service removed successfully."
    ;;

  status)
    $SYSTEMD_CMD status "$SERVICE_NAME" --no-pager || true
    ;;

  restart)
    echo "==> Restarting ${SERVICE_NAME}..."
    $SYSTEMD_CMD restart "$SERVICE_NAME"
    $SYSTEMD_CMD status "$SERVICE_NAME" --no-pager || true
    ;;

  logs)
    echo "==> Tailing logs for ${SERVICE_NAME}..."
    exec $JOURNAL_CMD
    ;;

  *)
    echo "Usage: $0 {install|uninstall|status|restart|logs} [--user]"
    echo ""
    echo "Examples:"
    echo "  sudo bash $0 install          # System service (auto-starts on boot)"
    echo "  bash $0 install --user        # User service (runs under current user)"
    echo "  sudo bash $0 status"
    echo "  bash $0 logs --user"
    exit 1
    ;;
esac
