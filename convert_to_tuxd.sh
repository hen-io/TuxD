#!/bin/bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OLD_DIR_NAME="$(basename "$SCRIPT_DIR")"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
NEW_DIR="${PARENT_DIR}/TuxD"
CURRENT_DIR="$SCRIPT_DIR"

# Who actually owns this install - not assumed, since different hosts may run
# this under different users (or root, for an old system-service install).
TARGET_USER="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || echo root)"
TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo 0)"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)" || true
TARGET_HOME="${TARGET_HOME:-/root}"
USER_SERVICE_DIR="${TARGET_HOME}/.config/systemd/user"

# ── Logging: ./upgrade.log, shared with bin/upgrade/preperations.sh ──────────
# CURRENT_DIR tracks wherever the project folder actually is right now, so
# log() keeps writing to the right place across the rename below.

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | sudo -u "$TARGET_USER" tee -a "${CURRENT_DIR}/upgrade.log" > /dev/null 2>/dev/null || true
}

log_err() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | sudo -u "$TARGET_USER" tee -a "${CURRENT_DIR}/upgrade.log" > /dev/null 2>/dev/null || true
}

# ── Rollback failsafe ──────────────────────────────────────────────────────
# Before touching anything, back up the install folder and (once known)
# whichever service unit is being converted. Any command that fails from
# here on (set -e) triggers rollback() via the ERR trap, restoring both.

BACKUP_ROOT="${PARENT_DIR}/.tuxd-convert-backup"
BACKUP_APP="${BACKUP_ROOT}/app"
BACKUP_SERVICE_FILE="${BACKUP_ROOT}/original.service"
BACKUP_SERVICE_META="${BACKUP_ROOT}/original.meta"
ROLLED_BACK=0

rollback() {
    trap - ERR
    if [[ "$ROLLED_BACK" -eq 1 ]]; then
        return
    fi
    ROLLED_BACK=1

    log_err "conversion failed - rolling back..."

    # Preserve this run's log so it survives the directory restore below.
    RUN_LOG_TAIL="${BACKUP_ROOT}/upgrade.log.tail"
    if [[ -f "${CURRENT_DIR}/upgrade.log" ]]; then
        sudo cp "${CURRENT_DIR}/upgrade.log" "$RUN_LOG_TAIL" 2>/dev/null || true
    fi

    if [[ -d "$BACKUP_APP" ]]; then
        echo "Restoring ${SCRIPT_DIR} from backup..."
        sudo rm -rf "$NEW_DIR" "$SCRIPT_DIR" 2>/dev/null
        sudo cp -a "$BACKUP_APP" "$SCRIPT_DIR"
        CURRENT_DIR="$SCRIPT_DIR"
    fi

    if [[ -f "$RUN_LOG_TAIL" ]]; then
        sudo -u "$TARGET_USER" tee -a "${CURRENT_DIR}/upgrade.log" < "$RUN_LOG_TAIL" > /dev/null 2>/dev/null || true
    fi

    if [[ -f "$BACKUP_SERVICE_FILE" && -f "$BACKUP_SERVICE_META" ]]; then
        scope=""
        name=""
        # shellcheck disable=SC1090
        source "$BACKUP_SERVICE_META"

        log "Restoring original service unit (${name}.service, ${scope} scope)..."
        if [[ "$scope" == "user" ]]; then
            sudo -u "$TARGET_USER" cp "$BACKUP_SERVICE_FILE" "${USER_SERVICE_DIR}/${name}.service"
            scu stop tuxd.service 2>/dev/null || true
            scu disable tuxd.service 2>/dev/null || true
            sudo rm -f "${USER_SERVICE_DIR}/tuxd.service"
            scu daemon-reload 2>/dev/null || true
            scu enable "${name}.service" 2>/dev/null || true
            scu restart "${name}.service" 2>/dev/null || true
        else
            sudo cp "$BACKUP_SERVICE_FILE" "/etc/systemd/system/${name}.service"
            sudo systemctl stop tuxd.service 2>/dev/null || true
            sudo systemctl disable tuxd.service 2>/dev/null || true
            sudo rm -f /etc/systemd/system/tuxd.service
            sudo systemctl daemon-reload 2>/dev/null || true
            sudo systemctl enable "${name}.service" 2>/dev/null || true
            sudo systemctl restart "${name}.service" 2>/dev/null || true
        fi
    fi

    log "Rollback complete - left as it was before this script ran."
    sudo rm -rf "$BACKUP_ROOT"
    exit 1
}

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

if [[ -e "$BACKUP_ROOT" ]]; then
    echo "ERROR: ${BACKUP_ROOT} already exists - a previous run may have failed" >&2
    echo "without cleaning up. Check it (and the current install) by hand before" >&2
    echo "running this again." >&2
    exit 1
fi

# ── Take the folder backup and arm the rollback trap ──────────────────────────

log "Backing up ${SCRIPT_DIR} to ${BACKUP_APP}..."
sudo mkdir -p "$BACKUP_ROOT"
sudo cp -a "$SCRIPT_DIR" "$BACKUP_APP"

trap rollback ERR

# ── Rename the folder this script sits in to TuxD ─────────────────────────────

if [[ "$OLD_DIR_NAME" == "TuxD" ]]; then
    log "Already named TuxD, nothing to rename."
    NEW_DIR="$SCRIPT_DIR"
elif [[ -e "$NEW_DIR" ]]; then
    log_err "ERROR: ${NEW_DIR} already exists, refusing to overwrite it."
    exit 1
else
    log "Renaming ${SCRIPT_DIR} -> ${NEW_DIR}..."
    sudo mv "$SCRIPT_DIR" "$NEW_DIR"
fi
CURRENT_DIR="$NEW_DIR"

if [[ -f "${NEW_DIR}/K93SYS.py" ]]; then
    log "Renaming K93SYS.py -> TuxD.py..."
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
    log "No py-k93sys/k93sys systemd unit found - nothing to convert there."
    trap - ERR
    sudo rm -rf "$BACKUP_ROOT"
    log "Done. TuxD now lives at: ${NEW_DIR}"
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
    log "WARNING: found a legacy unit in both scopes:"
    log "  user:   $USER_UNIT (active: $USER_IS_ACTIVE)"
    log "  system: $SYSTEM_UNIT (active: $SYSTEM_IS_ACTIVE)"
    if [[ "$SYSTEM_IS_ACTIVE" -eq 1 && "$USER_IS_ACTIVE" -eq 0 ]]; then
        log "Converting the system unit only (it's the one actually running); the unused user unit will be stopped/disabled and removed."
        CONVERT_SYSTEM=1
        DISCARD_USER=1
    else
        log "Converting the user unit only (it's the one actually running, or neither reported active); the unused system unit will be stopped/disabled and removed."
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
# already in the unit is preserved. The original is backed up first so
# rollback() can restore it if anything below fails.

if [[ "$CONVERT_USER" -eq 1 ]]; then
    sudo cp "$USER_UNIT" "$BACKUP_SERVICE_FILE"
    { echo "scope=user"; echo "name=$(basename "$USER_UNIT" .service)"; } | sudo tee "$BACKUP_SERVICE_META" > /dev/null

    log "Converting ${USER_UNIT} -> ${USER_SERVICE_DIR}/tuxd.service..."
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
    log "User service converted and restarted as tuxd.service (user: ${TARGET_USER})."
fi

# ── Convert the system-scope unit ─────────────────────────────────────────────

if [[ "$CONVERT_SYSTEM" -eq 1 ]]; then
    sudo cp "$SYSTEM_UNIT" "$BACKUP_SERVICE_FILE"
    { echo "scope=system"; echo "name=$(basename "$SYSTEM_UNIT" .service)"; } | sudo tee "$BACKUP_SERVICE_META" > /dev/null

    log "Converting ${SYSTEM_UNIT} -> /etc/systemd/system/tuxd.service..."
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
    log "System service converted and restarted as tuxd.service."
fi

trap - ERR
sudo rm -rf "$BACKUP_ROOT"
log "Done. TuxD now lives at: ${NEW_DIR}"
