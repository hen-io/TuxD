#!/bin/bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OLD_DIR_NAME="$(basename "$SCRIPT_DIR")"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
NEW_DIR="${PARENT_DIR}/TuxD"

# Who actually owns this install - not assumed, since different hosts may run
# this under different users (or root, for an old system-service install).
TARGET_USER="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || echo root)"
TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo 0)"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)" || true
TARGET_HOME="${TARGET_HOME:-/root}"
USER_SERVICE_DIR="${TARGET_HOME}/.config/systemd/user"

# Helper: run a systemctl --user command as TARGET_USER
scu() {
    sudo -u "$TARGET_USER" \
        XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" \
        systemctl --user "$@"
}

# ── Sanity check: only touch a folder that actually looks like this app ──────

if [[ "$OLD_DIR_NAME" != "TuxD" \
        && "${OLD_DIR_NAME,,}" != "py-k93sys" \
        && "${OLD_DIR_NAME,,}" != "k93sys" ]]; then
    echo "ERROR: this script's folder is named '${OLD_DIR_NAME}', not a recognized" >&2
    echo "legacy name (Py-K93SYS / k93sys) or TuxD - refusing to rename it." >&2
    exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/K93SYS.py" && ! -f "${SCRIPT_DIR}/TuxD.py" ]]; then
    echo "ERROR: no K93SYS.py or TuxD.py found in ${SCRIPT_DIR} - this doesn't" >&2
    echo "look like an install of this app, refusing to rename it." >&2
    exit 1
fi

# ── Rename the folder this script sits in to TuxD ─────────────────────────────

if [[ "$OLD_DIR_NAME" == "TuxD" ]]; then
    echo "Already named TuxD, nothing to rename."
    NEW_DIR="$SCRIPT_DIR"
elif [[ -e "$NEW_DIR" ]]; then
    echo "ERROR: ${NEW_DIR} already exists, refusing to overwrite it." >&2
    exit 1
else
    echo "Renaming ${SCRIPT_DIR} -> ${NEW_DIR}..."
    sudo mv "$SCRIPT_DIR" "$NEW_DIR"
fi

if [[ -f "${NEW_DIR}/K93SYS.py" ]]; then
    echo "Renaming K93SYS.py -> TuxD.py..."
    sudo mv "${NEW_DIR}/K93SYS.py" "${NEW_DIR}/TuxD.py"
fi

# ── Find whichever legacy systemd unit(s) exist ───────────────────────────────

USER_UNIT=""
for name in py-k93sys k93sys; do
    if [[ -f "${USER_SERVICE_DIR}/${name}.service" ]]; then
        USER_UNIT="${USER_SERVICE_DIR}/${name}.service"
        break
    fi
done

SYSTEM_UNIT=""
for name in py-k93sys k93sys; do
    if [[ -f "/etc/systemd/system/${name}.service" ]]; then
        SYSTEM_UNIT="/etc/systemd/system/${name}.service"
        break
    fi
done

if [[ -z "$USER_UNIT" && -z "$SYSTEM_UNIT" ]]; then
    echo "No py-k93sys/k93sys systemd unit found - nothing to convert there."
    echo "Done. TuxD now lives at: ${NEW_DIR}"
    exit 0
fi

# If both scopes somehow have a legacy unit, only the one that is actually
# running gets converted and restarted - the other is removed without
# starting a second, competing instance of the agent.
USER_IS_ACTIVE=0
[[ -n "$USER_UNIT" ]] && scu is-active --quiet "$(basename "$USER_UNIT")" 2>/dev/null && USER_IS_ACTIVE=1

SYSTEM_IS_ACTIVE=0
[[ -n "$SYSTEM_UNIT" ]] && sudo systemctl is-active --quiet "$(basename "$SYSTEM_UNIT")" 2>/dev/null && SYSTEM_IS_ACTIVE=1

CONVERT_USER=0
CONVERT_SYSTEM=0
DISCARD_USER=0
DISCARD_SYSTEM=0

if [[ -n "$USER_UNIT" && -n "$SYSTEM_UNIT" ]]; then
    echo "WARNING: found a legacy unit in both scopes:" >&2
    echo "  user:   $USER_UNIT (active: $USER_IS_ACTIVE)" >&2
    echo "  system: $SYSTEM_UNIT (active: $SYSTEM_IS_ACTIVE)" >&2
    if [[ "$SYSTEM_IS_ACTIVE" -eq 1 && "$USER_IS_ACTIVE" -eq 0 ]]; then
        echo "Converting the system unit only (it's the one actually running); the unused user unit will be stopped/disabled and removed." >&2
        CONVERT_SYSTEM=1
        DISCARD_USER=1
    else
        echo "Converting the user unit only (it's the one actually running, or neither reported active); the unused system unit will be stopped/disabled and removed." >&2
        CONVERT_USER=1
        DISCARD_SYSTEM=1
    fi
else
    [[ -n "$USER_UNIT" ]] && CONVERT_USER=1
    [[ -n "$SYSTEM_UNIT" ]] && CONVERT_SYSTEM=1
fi

if [[ "$DISCARD_USER" -eq 1 ]]; then
    scu stop "$(basename "$USER_UNIT")" 2>/dev/null || true
    scu disable "$(basename "$USER_UNIT")" 2>/dev/null || true
    sudo rm -f "$USER_UNIT"
    scu daemon-reload 2>/dev/null || true
fi

if [[ "$DISCARD_SYSTEM" -eq 1 ]]; then
    sudo systemctl stop "$(basename "$SYSTEM_UNIT")" || true
    sudo systemctl disable "$(basename "$SYSTEM_UNIT")" || true
    sudo rm -f "$SYSTEM_UNIT"
    sudo systemctl daemon-reload
fi

# ── Convert the user-scope unit ───────────────────────────────────────────────
# Rewrites the existing unit's own text (search/replace py-k93sys -> TuxD)
# rather than installing a fresh template, so any host-specific customization
# already in the unit is preserved.

if [[ "$CONVERT_USER" -eq 1 ]]; then
    echo "Converting ${USER_UNIT} -> ${USER_SERVICE_DIR}/tuxd.service..."
    sed \
        -e "s/py-k93sys/TuxD/gI" \
        -e "s/k93sys/TuxD/gI" \
        -e "s/K93SYS\.py/TuxD.py/g" \
        "$USER_UNIT" | sudo -u "$TARGET_USER" tee "${USER_SERVICE_DIR}/tuxd.service" > /dev/null

    scu stop "$(basename "$USER_UNIT")" 2>/dev/null || true
    scu disable "$(basename "$USER_UNIT")" 2>/dev/null || true
    sudo rm -f "$USER_UNIT"

    scu daemon-reload
    scu enable tuxd.service
    scu restart tuxd.service
    echo "User service converted and restarted as tuxd.service (user: ${TARGET_USER})."
fi

# ── Convert the system-scope unit ─────────────────────────────────────────────

if [[ "$CONVERT_SYSTEM" -eq 1 ]]; then
    echo "Converting ${SYSTEM_UNIT} -> /etc/systemd/system/tuxd.service..."
    sudo sed \
        -e "s/py-k93sys/TuxD/gI" \
        -e "s/k93sys/TuxD/gI" \
        -e "s/K93SYS\.py/TuxD.py/g" \
        "$SYSTEM_UNIT" | sudo tee /etc/systemd/system/tuxd.service > /dev/null

    sudo systemctl stop "$(basename "$SYSTEM_UNIT")" || true
    sudo systemctl disable "$(basename "$SYSTEM_UNIT")" || true
    sudo rm -f "$SYSTEM_UNIT"

    sudo systemctl daemon-reload
    sudo systemctl enable tuxd.service
    sudo systemctl restart tuxd.service
    echo "System service converted and restarted as tuxd.service."
fi

echo "Done. TuxD now lives at: ${NEW_DIR}"
