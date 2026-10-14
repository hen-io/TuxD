import os
import re
import signal
import subprocess
import threading


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "_", str(s).lower())
    return s.strip("_")


_ENTITY_COMPONENT_PREFIXES = (
    "builtin_", "custom_button_", "custom_sensor_", "disk_", "docker_",
    "lag_monitor_", "log_sensor_", "net_", "select_",
    "self_update_", "status_", "system_", "tugboat_",
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


SENSOR_CMD_TIMEOUT = 60


def stream_cmd(cmd, on_line=None, env=None, timeout=None):
    if not cmd or not str(cmd).strip():
        return ""
    lines = []

    def emit(line):
        lines.append(line)
        if on_line:
            try:
                on_line(line)
            except Exception:
                pass

    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1, env=env,
            start_new_session=bool(timeout),
        )
    except Exception as e:
        emit(f"ERR: {e}")
        return lines[0]
    timer = None
    timed_out = []

    def stop():
        timed_out.append(True)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()

    if timeout:
        timer = threading.Timer(timeout, stop)
        timer.daemon = True
        timer.start()
    try:
        for line in proc.stdout:
            emit(line.rstrip("\r\n"))
        proc.wait()
    finally:
        if timer:
            timer.cancel()
    if timed_out:
        emit(f"ERR: timed out after {timeout}s")
    return "\n".join(lines).strip()


def run_cmd(cmd: str, env=None, timeout=None):
    if not cmd or cmd.strip() == "":
        return ""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"ERR: timed out after {timeout}s"
    except Exception as e:
        return f"ERR: {e}"
