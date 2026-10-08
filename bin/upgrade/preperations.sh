#!/bin/bash
set -e


sudo apt install -y python3-websockets 2>/dev/null || true

SCOPE="user"
for arg in "$@"; do
    case "$arg" in
        --system) SCOPE="system" ;;
        --user) SCOPE="user" ;;
    esac
done

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="$(command -v python3)"

UNIT_CONTENTS() {
    local wanted_by="$1"
    local user_line="$2"
    cat <<EOF
[Unit]
Description=TuxD Linux Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
${user_line}WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/start.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=${wanted_by}
EOF
}

if [ "$SCOPE" = "system" ]; then
    RUN_USER="$(stat -c '%U' "$INSTALL_DIR" 2>/dev/null || true)"
    if [ -z "$RUN_USER" ] || [ "$RUN_USER" = "root" ]; then
        RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
    fi
    UNIT_CONTENTS "multi-user.target" "User=${RUN_USER}
" | sudo tee /etc/systemd/system/tuxd.service > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable tuxd.service
    echo "tuxd.service installed (--system, running as ${RUN_USER}) - not started automatically."
    echo "Start it with: sudo systemctl restart tuxd.service"
else
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    UNIT_CONTENTS "default.target" "" > "$UNIT_DIR/tuxd.service"
    systemctl --user daemon-reload
    systemctl --user enable tuxd.service
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
    echo "tuxd.service installed (--user, $(whoami)) - not started automatically."
    echo "Start it with: systemctl --user restart tuxd.service"
fi
