#!/bin/bash

set -o pipefail

REBOOT=1
DRY=0
for arg in "$@"; do
    case "$arg" in
        --no-reboot) REBOOT=0 ;;
        --always-reboot) REBOOT=2 ;;
        --dry-run) DRY=1 ;;
        *) echo "Usage: $0 [--no-reboot | --always-reboot] [--dry-run]"; exit 2 ;;
    esac
done

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

APT=(env DEBIAN_FRONTEND=noninteractive apt-get -y -q
     -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

REBOOT_PACKAGES='^(linux-image-|linux-modules-|proxmox-kernel-|pve-kernel-|intel-microcode|amd64-microcode|firmware-|libc6|systemd|dbus|libssl3)'

say() { echo "$(date '+%H:%M:%S') $*"; }

say "Refreshing package lists..."
$SUDO apt-get update -q || { say "apt-get update failed"; exit 1; }

PACKAGES=$($SUDO apt-get -s -q full-upgrade 2>/dev/null | awk '/^Inst /{print $2}')
if [ -z "$PACKAGES" ]; then
    say "No updates to install."
else
    say "Updates: $(echo "$PACKAGES" | wc -l) package(s)"
    echo "$PACKAGES" | sed 's/^/  /'
fi

if [ "$DRY" -eq 1 ]; then
    say "Dry run - nothing installed."
    exit 0
fi

if [ -n "$PACKAGES" ]; then
    say "Installing..."
    $SUDO "${APT[@]}" full-upgrade || { say "Upgrade failed"; exit 1; }
    $SUDO "${APT[@]}" autoremove || say "autoremove failed (ignored)"
fi

REASON=""
if [ -f /var/run/reboot-required ]; then
    REASON="a package asked for it (/var/run/reboot-required)"
fi
if [ -z "$REASON" ]; then
    RUNNING=$(uname -r)
    NEWEST=$(ls /boot/vmlinuz-* 2>/dev/null | sed 's|^/boot/vmlinuz-||' | sort -V | tail -n 1)
    if [ -n "$NEWEST" ] && [ "$NEWEST" != "$RUNNING" ]; then
        REASON="kernel $NEWEST is installed, $RUNNING is running"
    fi
fi
if [ -z "$REASON" ] && command -v needrestart >/dev/null 2>&1; then
    STATUS=$($SUDO needrestart -b 2>/dev/null)
    KSTA=$(echo "$STATUS" | awk -F': ' '/^NEEDRESTART-KSTA/{print $2}')
    UCSTA=$(echo "$STATUS" | awk -F': ' '/^NEEDRESTART-UCSTA/{print $2}')
    if [ -n "$KSTA" ] && [ "$KSTA" != "1" ] && [ "$KSTA" != "0" ]; then
        REASON="needrestart: a newer kernel is pending"
    elif [ -n "$UCSTA" ] && [ "$UCSTA" != "1" ] && [ "$UCSTA" != "0" ]; then
        REASON="needrestart: new CPU microcode is pending"
    fi
fi
if [ -z "$REASON" ] && [ -n "$PACKAGES" ]; then
    CORE=$(echo "$PACKAGES" | grep -E "$REBOOT_PACKAGES" | head -n 3 | tr '\n' ' ')
    [ -n "$CORE" ] && REASON="updated core package(s): $CORE"
fi

if [ -z "$REASON" ] && [ "$REBOOT" -eq 2 ]; then
    REASON="--always-reboot"
fi
if [ -z "$REASON" ]; then
    say "Done - no reboot needed."
    exit 0
fi

if [ "$REBOOT" -eq 0 ]; then
    say "Done - a reboot is needed: $REASON (not rebooting: --no-reboot)"
    exit 0
fi

say "Reboot needed: $REASON - rebooting in 1 minute (cancel: sudo shutdown -c)"
$SUDO shutdown -r +1 "TuxD: reboot after system updates ($REASON)" \
    || { say "Could not schedule the reboot"; exit 1; }
exit 0
