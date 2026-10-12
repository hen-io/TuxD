import json
import os
import re
import shlex
import subprocess

import yaml

ACTIONS = {
    "Update": "--update",
    "Start": "--start",
    "Stop": "--stop",
    "Restart": "--restart",
    "Rollback": "--rollback",
    "Health Check": "--healthcheck",
}

DEFAULT_STATUS_FILES = (".TugBoat/tugboat.json", "TugBoat/tugboat.json", "tugboat.json")
CONF_FILES = (".TugBoat/TugBoat.conf", "TugBoat/TugBoat.conf", "TugBoat.conf")


def read_tugboat_conf(path):
    for name in CONF_FILES:
        try:
            with open(os.path.join(path, name), "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            continue
        except Exception:
            return {}
    return {}


def status_file_path(path, conf=None):
    conf = conf if conf is not None else read_tugboat_conf(path)
    configured = str(conf.get("status_file") or "").strip()
    if configured:
        return configured if os.path.isabs(configured) else os.path.normpath(os.path.join(path, configured))
    found = [os.path.join(path, p) for p in DEFAULT_STATUS_FILES]
    return next((f for f in found if os.path.isfile(f)), found[0])


def read_status(path, conf=None):
    try:
        with open(status_file_path(path, conf), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("stacks"), dict):
        return None
    return data


def stack_names(status):
    if not status:
        return []
    return sorted(status.get("stacks", {}).keys())


def build_command(python_bin, path, action_flag, stack=None, extra=()):
    return (
        f"cd {shlex.quote(path)} && "
        f"{python_bin} TugBoat.py {action_flag}"
        + (f" --stack {shlex.quote(stack)}" if stack else "")
        + " --auto"
        + "".join(f" {shlex.quote(a)}" for a in extra)
    )


def run_command(python_bin, path, action_flag, stack, timeout=1800, extra=()):
    cmd = build_command(python_bin, path, action_flag, stack, extra)
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out.strip()
    except Exception as e:
        return f"ERR: {e}"


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BACKUP_LINE = re.compile(r"^\s+(\d\d_\d\d_\d\d_\d\d_\d\d_\d\d)\b")


def list_backups(python_bin, path, stack, timeout=60):
    cmd = f"cd {shlex.quote(path)} && {python_bin} TugBoat.py --list-backups {shlex.quote(stack)}"
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return []
    names = []
    for line in _ANSI.sub("", out or "").splitlines():
        m = _BACKUP_LINE.match(line)
        if m:
            names.append(m.group(1))
    return names
