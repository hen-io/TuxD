import re
import subprocess


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "_", str(s).lower())
    return s.strip("_")


_ENTITY_COMPONENT_PREFIXES = (
    "builtin_", "custom_button_", "custom_sensor_", "disk_", "docker_",
    "lag_monitor_", "log_sensor_", "net_", "select_",
    "self_update_", "status_", "system_",
)


def entity_object_id(object_id):
    object_id = str(object_id)
    for prefix in _ENTITY_COMPONENT_PREFIXES:
        if object_id.startswith(prefix):
            object_id = object_id[len(prefix):]
            break
    return re.sub(
        r"_(?:bytes?|gb|kb|mb|mbps|pct|percent|ms|seconds?|minutes?|hours?)$",
        "",
        object_id,
    )


def run_cmd(cmd: str, env=None):
    if not cmd or cmd.strip() == "":
        return ""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERR: {e}"
