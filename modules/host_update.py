import shutil
import subprocess


def _run(cmd):
    if not cmd or not str(cmd).strip():
        return None
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.stdout if result.stdout is not None else ""
    except Exception:
        return None


def _pkg_manager():
    for name in ("apt-get", "dnf", "yum", "zypper", "pacman"):
        if shutil.which(name):
            return name
    return None


_CHECK_CMDS = {
    "apt-get": "apt-get -s -o Debug::NoLocking=true upgrade 2>/dev/null | grep -c '^Inst '",
    "dnf": "dnf -q check-update 2>/dev/null | grep -cE '^[A-Za-z0-9._+-]+\\.[A-Za-z0-9_]+ '",
    "yum": "yum -q check-update 2>/dev/null | grep -cE '^[A-Za-z0-9._+-]+\\.[A-Za-z0-9_]+ '",
    "zypper": "zypper -q -t list-updates 2>/dev/null | grep -c '^v |'",
    "pacman": "pacman -Qu 2>/dev/null | wc -l",
}

_LIST_CMDS = {
    "apt-get": "apt-get -s -o Debug::NoLocking=true upgrade 2>/dev/null | "
               "awk '/^Inst /{c=$3;n=$4;gsub(/[][]/,\"\",c);gsub(/[()]/,\"\",n);print $2 \"-\" c \">\" n}'",
    "dnf": "dnf -q check-update 2>/dev/null | awk '/^[A-Za-z0-9._+-]+\\.[A-Za-z0-9_]+ /{print $1 \">\" $2}'",
    "yum": "yum -q check-update 2>/dev/null | awk '/^[A-Za-z0-9._+-]+\\.[A-Za-z0-9_]+ /{print $1 \">\" $2}'",
    "zypper": "zypper -q -t list-updates 2>/dev/null | awk -F'|' '/^v \\|/{gsub(/ /,\"\",$3); gsub(/ /,\"\",$5); print $3 \">\" $5}'",
    "pacman": "pacman -Qu 2>/dev/null | awk '{print $1 \"-\" $2 \">\" $4}'",
}

_INSTALL_CMDS = {
    "apt-get": "sudo apt update && sudo apt upgrade -y",
    "dnf": "sudo dnf -y upgrade",
    "yum": "sudo yum -y update",
    "zypper": "sudo zypper -n update",
    "pacman": "sudo pacman -Syu --noconfirm",
}


def default_check_cmd():
    return _CHECK_CMDS.get(_pkg_manager()) or _CHECK_CMDS["apt-get"]


def default_list_cmd():
    return _LIST_CMDS.get(_pkg_manager()) or _LIST_CMDS["apt-get"]


def default_install_cmd():
    return _INSTALL_CMDS.get(_pkg_manager()) or _INSTALL_CMDS["apt-get"]


def parse_leading_int(s):
    if s is None:
        return None
    digits = ""
    for ch in str(s).strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def update_count(check_cmd=None):
    cmd = check_cmd or default_check_cmd()
    out = _run(cmd)
    if out is None:
        return None

    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if not lines:
        return 0
    try:
        return int(lines[-1])
    except Exception:
        return None


def update_list(list_cmd=None, limit=50):
    cmd = list_cmd or default_list_cmd()
    out = _run(cmd)
    if not out:
        return []

    names = [l.strip() for l in out.splitlines() if l.strip()]
    return names[:limit]
