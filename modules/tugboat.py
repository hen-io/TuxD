import json
import os
import shlex
import subprocess

import yaml

ACTIONS = {
    "Update": "--update",
    "Start": "--start",
    "Stop": "--stop",
    "Health Check": "--healthcheck",
}

DEFAULT_STATUS_FILE = "tugboat.json"


def read_tugboat_conf(path):
    conf_path = os.path.join(path, "TugBoat.conf")
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def status_file_path(path, conf=None):
    conf = conf if conf is not None else read_tugboat_conf(path)
    status_file = str(conf.get("status_file") or DEFAULT_STATUS_FILE).strip()
    if os.path.isabs(status_file):
        return status_file
    return os.path.normpath(os.path.join(path, status_file))


def read_status(path, conf=None):
    try:
        with open(status_file_path(path, conf), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    stacks = data.get("stacks")
    return data if isinstance(stacks, dict) else None


def stack_names(status):
    if not status:
        return []
    return sorted(status.get("stacks", {}).keys())


def build_command(python_bin, path, action_flag, stack):
    return (
        f"cd {shlex.quote(path)} && "
        f"{python_bin} TugBoat.py {action_flag} --stack {shlex.quote(stack)} --auto"
    )


def run_command(python_bin, path, action_flag, stack, timeout=1800):
    cmd = build_command(python_bin, path, action_flag, stack)
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out.strip()
    except Exception as e:
        return f"ERR: {e}"
