#!/bin/bash


set -eo pipefail

FORCE_SYSTEM=0
FORCE_USER=0
for arg in "$@"; do
    [[ "$arg" == "--system" ]] && FORCE_SYSTEM=1
    [[ "$arg" == "--user" ]] && FORCE_USER=1
done

CURRENT_USER="$(id -un)"
if [[ "$EUID" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

run_as_target() {
    if [[ "$CURRENT_USER" == "$TARGET_USER" ]]; then
        "$@"
    else
        sudo -u "$TARGET_USER" "$@"
    fi
}


SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SELF_DIR}/../.." && pwd)"

if [[ -d "${PROJECT_DIR}/MASTER" || -d "${PROJECT_DIR}/Build" || -f "${PROJECT_DIR}/make.ps1" ]]; then
    echo "ERROR: ${PROJECT_DIR} looks like the dev/build repo, not a per-host install." >&2
    echo "Refusing to run preperations.sh here - run it from the actual runtime" >&2
    echo "install directory on this host instead (e.g. ~/TuxD)." >&2
    exit 1
fi

TARGET_USER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || echo root)"
TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo 0)"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)" || true
TARGET_HOME="${TARGET_HOME:-/root}"
USER_SERVICE_DIR="${TARGET_HOME}/.config/systemd/user"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | run_as_target tee -a "${PROJECT_DIR}/upgrade.log" > /dev/null 2>/dev/null || true
}

log_err() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | run_as_target tee -a "${PROJECT_DIR}/upgrade.log" > /dev/null 2>/dev/null || true
}

scu() {
    if [[ "$CURRENT_USER" == "$TARGET_USER" ]]; then
        XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" \
            systemctl --user "$@"
    else
        sudo -u "$TARGET_USER" \
            XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" \
            systemctl --user "$@"
    fi
}

install_system_unit() {
    log "$1"
    sed "s#/home/henrik/TuxD#${PROJECT_DIR}#g" "$TEMPLATE_UNIT" | $SUDO tee /etc/systemd/system/tuxd.service > /dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable tuxd.service
    $SUDO systemctl restart tuxd.service
    log "$2"
}

install_user_unit() {
    log "$1"
    run_as_target mkdir -p "$USER_SERVICE_DIR"
    sed "s#/home/henrik/TuxD#${PROJECT_DIR}#g" "$TEMPLATE_UNIT" | run_as_target tee "${USER_SERVICE_DIR}/tuxd.service" > /dev/null
    scu daemon-reload
    scu enable tuxd.service
    scu restart tuxd.service
    $SUDO loginctl enable-linger "$TARGET_USER" 2>/dev/null || true
    log "$2"
}

if [[ "$FORCE_SYSTEM" -eq 0 && "$FORCE_USER" -eq 0 ]]; then
    ALREADY_HAS_UNIT=0
    [[ -f "${USER_SERVICE_DIR}/tuxd.service" ]] && grep -q 'start\.py' "${USER_SERVICE_DIR}/tuxd.service" && ALREADY_HAS_UNIT=1
    [[ -f "/etc/systemd/system/tuxd.service" ]] && $SUDO grep -q 'start\.py' "/etc/systemd/system/tuxd.service" && ALREADY_HAS_UNIT=1
    if [[ "$ALREADY_HAS_UNIT" -eq 1 ]]; then
        log "A current tuxd.service already exists - nothing to do."
        exit 0
    fi
fi

TEMPLATE_UNIT="${PROJECT_DIR}/bin/upgrade/tuxd.service"

if [[ "$FORCE_SYSTEM" -eq 1 || "$FORCE_USER" -eq 1 ]]; then
    log "Explicit scope requested (--$([[ "$FORCE_SYSTEM" -eq 1 ]] && echo system || echo user)) - removing any existing tuxd.service in both scopes..."

    if [[ -f "${USER_SERVICE_DIR}/tuxd.service" ]]; then
        scu stop tuxd.service 2>/dev/null || true
        scu disable tuxd.service 2>/dev/null || true
        $SUDO rm -f "${USER_SERVICE_DIR}/tuxd.service"
        scu daemon-reload 2>/dev/null || true
    fi
    if [[ -f "/etc/systemd/system/tuxd.service" ]]; then
        $SUDO systemctl stop tuxd.service 2>/dev/null || true
        $SUDO systemctl disable tuxd.service 2>/dev/null || true
        $SUDO rm -f "/etc/systemd/system/tuxd.service"
        $SUDO systemctl daemon-reload 2>/dev/null || true
    fi

    if [[ ! -f "$TEMPLATE_UNIT" ]]; then
        log_err "ERROR: template unit not found at ${TEMPLATE_UNIT}."
        exit 1
    fi

    if [[ "$FORCE_SYSTEM" -eq 1 ]]; then
        install_system_unit \
            "Installing /etc/systemd/system/tuxd.service from template, pointed at ${PROJECT_DIR}..." \
            "System service tuxd.service created and started."
    else
        install_user_unit \
            "Installing ${USER_SERVICE_DIR}/tuxd.service from template, pointed at ${PROJECT_DIR}..." \
            "User service tuxd.service created and started (user: ${TARGET_USER}, linger enabled so it survives logout/reboot)."
    fi

    log "Done."
    exit 0
fi

STALE_USER_UNIT=0
[[ -f "${USER_SERVICE_DIR}/tuxd.service" ]] && ! grep -q 'start\.py' "${USER_SERVICE_DIR}/tuxd.service" && STALE_USER_UNIT=1

STALE_SYSTEM_UNIT=0
[[ -f "/etc/systemd/system/tuxd.service" ]] && ! $SUDO grep -q 'start\.py' "/etc/systemd/system/tuxd.service" && STALE_SYSTEM_UNIT=1

if [[ "$STALE_USER_UNIT" -eq 0 && "$STALE_SYSTEM_UNIT" -eq 0 ]]; then
    if [[ ! -f "$TEMPLATE_UNIT" ]]; then
        log_err "ERROR: template unit not found at ${TEMPLATE_UNIT}."
        exit 1
    fi

    if [[ "$TARGET_USER" == "root" ]]; then
        install_system_unit \
            "No systemd service found at all - installing tuxd.service fresh (system scope)..." \
            "System service tuxd.service created and started."
    else
        install_user_unit \
            "No systemd service found at all - installing tuxd.service fresh (user scope: ${TARGET_USER})..." \
            "User service tuxd.service created and started (user: ${TARGET_USER}, linger enabled so it survives logout/reboot)."
    fi
else
    if [[ "$STALE_USER_UNIT" -eq 1 && "$STALE_SYSTEM_UNIT" -eq 1 ]]; then
        USER_IS_ACTIVE=0
        scu is-active --quiet tuxd.service 2>/dev/null && USER_IS_ACTIVE=1
        SYSTEM_IS_ACTIVE=0
        $SUDO systemctl is-active --quiet tuxd.service 2>/dev/null && SYSTEM_IS_ACTIVE=1

        log "WARNING: found a stale tuxd.service in both scopes (user active: ${USER_IS_ACTIVE}, system active: ${SYSTEM_IS_ACTIVE})."
        if [[ "$SYSTEM_IS_ACTIVE" -eq 1 && "$USER_IS_ACTIVE" -eq 0 ]]; then
            STALE_USER_UNIT=0
            log "Discarding the unused user unit."
            scu stop tuxd.service 2>/dev/null || true
            scu disable tuxd.service 2>/dev/null || true
            $SUDO rm -f "${USER_SERVICE_DIR}/tuxd.service"
            scu daemon-reload 2>/dev/null || true
        else
            STALE_SYSTEM_UNIT=0
            log "Discarding the unused system unit."
            $SUDO systemctl stop tuxd.service 2>/dev/null || true
            $SUDO systemctl disable tuxd.service 2>/dev/null || true
            $SUDO rm -f /etc/systemd/system/tuxd.service
            $SUDO systemctl daemon-reload 2>/dev/null || true
        fi
    fi

    if [[ ! -f "$TEMPLATE_UNIT" ]]; then
        log_err "ERROR: template unit not found at ${TEMPLATE_UNIT}."
        exit 1
    fi

    if [[ "$STALE_USER_UNIT" -eq 1 ]]; then
        install_user_unit \
            "Refreshing stale ${USER_SERVICE_DIR}/tuxd.service from template, pointed at ${PROJECT_DIR}..." \
            "User service tuxd.service refreshed and restarted (user: ${TARGET_USER})."
    fi
    if [[ "$STALE_SYSTEM_UNIT" -eq 1 ]]; then
        install_system_unit \
            "Refreshing stale /etc/systemd/system/tuxd.service from template, pointed at ${PROJECT_DIR}..." \
            "System service tuxd.service refreshed and restarted."
    fi
fi

FINAL_USER_HAS=0
[[ -f "${USER_SERVICE_DIR}/tuxd.service" ]] && FINAL_USER_HAS=1
FINAL_SYSTEM_HAS=0
[[ -f "/etc/systemd/system/tuxd.service" ]] && FINAL_SYSTEM_HAS=1

if [[ "$FINAL_USER_HAS" -eq 1 && "$FINAL_SYSTEM_HAS" -eq 1 ]]; then
    if [[ "$FORCE_SYSTEM" -eq 1 ]]; then
        log "tuxd.service exists in both scopes - keeping system scope, removing user scope..."
        scu stop tuxd.service 2>/dev/null || true
        scu disable tuxd.service 2>/dev/null || true
        $SUDO rm -f "${USER_SERVICE_DIR}/tuxd.service"
        scu daemon-reload 2>/dev/null || true
    else
        log "tuxd.service exists in both scopes - keeping user scope, removing system scope..."
        $SUDO systemctl stop tuxd.service 2>/dev/null || true
        $SUDO systemctl disable tuxd.service 2>/dev/null || true
        $SUDO rm -f /etc/systemd/system/tuxd.service
        $SUDO systemctl daemon-reload 2>/dev/null || true
    fi
fi

log "Done."
