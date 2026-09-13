#!/bin/bash

# Runs once after every applied update, before the agent restarts.
# Normally empty - add one-off migration steps here (new dependencies, a
# one-time data migration, etc.) for whichever release needs them.

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONVERT_SCRIPT="${PROJECT_ROOT}/convert_to_tuxd.sh"

# ── Rename step: Py-K93SYS/k93sys -> TuxD ───────────────────────────────────
# This runs as a child of the very service convert_to_tuxd.sh may stop (it
# stops the old py-k93sys/k93sys unit before installing tuxd.service), so it's
# launched as its own transient systemd scope - a separate cgroup - via
# systemd-run. Without this, systemd's default KillMode=control-group would
# kill this whole process tree the moment the old unit is stopped, truncating
# the conversion mid-way.

if [[ -f "$CONVERT_SCRIPT" ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
        systemd-run --unit="tuxd-convert-$$" --collect bash "$CONVERT_SCRIPT" || true
    else
        systemd-run --user --unit="tuxd-convert-$$" --collect bash "$CONVERT_SCRIPT" || true
    fi
fi
