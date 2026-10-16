import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

KINDS = {"qemu": ("qm", "/etc/pve/qemu-server"), "lxc": ("pct", "/etc/pve/lxc")}
LOCK_FILE = "/run/lock/tuxd-proxmox.lock"
UNIT_DIR = "/etc/systemd/system"
UNITS = {"start": "tuxd-proxmox-start.service", "stop": "tuxd-proxmox-stop.service"}
JOBS = {"start": "bulk_start_vms", "stop": "bulk_stop_vms"}
DEFAULT_SHUTDOWN_TIMEOUT = 180
DEFAULT_MAX_RUNTIME = 1800

_STEP_ACTIONS = {
    "start_vm": "start", "start": "start", "start_ct": "start",
    "stop_vm": "stop", "stop": "stop", "stop_ct": "stop", "shutdown_vm": "stop",
}
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|m|min|h)?\s*$", re.I)


def is_proxmox():
    return os.path.isdir("/etc/pve") and shutil.which("pvesh") is not None


def _prefix(args):
    return list(args) if os.geteuid() == 0 else ["sudo", "-n"] + list(args)


def run(args, timeout=None):
    try:
        done = subprocess.run(_prefix(args), capture_output=True, text=True, errors="replace", timeout=timeout)
        return done.returncode, (done.stdout + done.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as e:
        return 1, str(e)



def parse_conf(text):
    conf = {}
    for line in (text or "").splitlines():
        if line.startswith("["):
            break
        key, sep, value = line.partition(":")
        if sep and not key.startswith("#"):
            conf[key.strip()] = value.strip()
    return conf


def parse_startup(value):
    out = {}
    for part in str(value or "").split(","):
        key, _, val = part.partition("=")
        if val.strip().lstrip("-").isdigit():
            out[key.strip()] = int(val)
    return out


def _guest_conf(kind, vmid, runner):
    try:
        return parse_conf(Path(KINDS[kind][1], f"{vmid}.conf").read_text(encoding="utf-8"))
    except OSError:
        rc, out = runner(["pvesh", "get", f"/nodes/localhost/{kind}/{vmid}/config", "--output-format", "json"], timeout=30)
        try:
            data = json.loads(out) if rc == 0 else {}
        except ValueError:
            data = {}
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def read_guests(runner=run):
    guests = {}
    for kind in KINDS:
        rc, out = runner(["pvesh", "get", f"/nodes/localhost/{kind}", "--output-format", "json"], timeout=30)
        if rc != 0:
            raise RuntimeError(out or f"pvesh failed ({rc})")
        try:
            rows = json.loads(out)
        except ValueError:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            try:
                vmid = int(row.get("vmid"))
            except (TypeError, ValueError):
                continue
            conf = _guest_conf(kind, vmid, runner)
            startup = parse_startup(conf.get("startup"))
            guests[vmid] = {
                "vmid": vmid,
                "kind": kind,
                "name": str(row.get("name") or conf.get("name") or conf.get("hostname") or vmid),
                "status": str(row.get("status") or "unknown"),
                "onboot": conf.get("onboot") == "1",
                "order": startup.get("order"),
                "cpu": row.get("cpu"),
                "mem": row.get("mem"),
                "maxmem": row.get("maxmem"),
                "uptime": row.get("uptime"),
                "template": str(row.get("template") or conf.get("template") or "") in ("1", "True", "true"),
            }
    return guests



def parse_duration(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    m = _DURATION.match(str(value or ""))
    if not m:
        return None
    unit = (m.group(2) or "s").lower()
    return float(m.group(1)) * {"ms": 0.001, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600}[unit]


def parse_targets(value):
    items = value if isinstance(value, list) else str(value if value is not None else "").replace(",", " ").split()
    if not isinstance(value, list) and items and items[0].lower() == "all":
        return "all"
    ids = []
    for item in items:
        if str(item).strip().lower() == "all":
            return "all"
        try:
            ids.append(int(str(item).strip()))
        except ValueError:
            break
    return ids


def parse_order(order):
    steps, problems = [], []
    for n, raw in enumerate(order if isinstance(order, list) else [], 1):
        if not isinstance(raw, dict):
            problems.append(f"step {n}: not a mapping")
            continue
        action = next((k for k in raw if str(k) in _STEP_ACTIONS), None)
        if action is not None:
            targets = parse_targets(raw[action])
            if not targets:
                problems.append(f"step {n}: no guest ids")
                continue
            options = {}
            if "timeout" in raw and parse_duration(raw["timeout"]) is not None:
                options["timeout"] = parse_duration(raw["timeout"])
            steps.append((_STEP_ACTIONS[str(action)], targets, options))
        elif "delay" in raw:
            seconds = parse_duration(raw["delay"])
            if seconds is None:
                problems.append(f"step {n}: delay {raw['delay']!r} is not a duration")
            else:
                steps.append(("delay", seconds, {}))
        elif "wait" in raw:
            value = raw["wait"]
            if value is False:
                continue
            steps.append(("wait", None if value is True else parse_duration(value), {}))
        else:
            problems.append(f"step {n}: unknown step {', '.join(map(str, raw))}")
    return steps, problems


def _sorted_for(action, guests):
    key = lambda g: (g["order"] is None, g["order"] if g["order"] is not None else 0, g["vmid"])
    return sorted(guests, key=key, reverse=(action == "stop"))


def plan(steps, guests):
    planned = []
    for i, (kind, value, options) in enumerate(steps):
        if kind in ("start", "stop") and value == "all":
            later = {vmid for k, v, _o in steps[i + 1:] if k in ("start", "stop") and v != "all" for vmid in v}
            pool = [g for g in guests.values() if g["onboot"] and not g["template"] and g["vmid"] not in later]
            value = [g["vmid"] for g in _sorted_for(kind, pool)]
        planned.append((kind, value, options))
    return planned


def job_settings(cfg, job):
    section = (cfg or {}).get(JOBS[job]) or {}
    section = section if isinstance(section, dict) else {}
    order = section.get("order") if isinstance(section.get("order"), list) else []
    return bool(section.get("enabled", False)) and bool(order), order


def _section(cfg, job):
    section = (cfg or {}).get(JOBS[job]) or {}
    return section if isinstance(section, dict) else {}


def max_runtime(cfg, job):
    return parse_duration(_section(cfg, job).get("max_runtime", DEFAULT_MAX_RUNTIME)) or DEFAULT_MAX_RUNTIME



class JobBusy(RuntimeError):
    pass


class _Lock:

    def __init__(self, wait=0):
        self.wait = wait
        self.fd = None

    def __enter__(self):
        self.fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    raise JobBusy("A Proxmox bulk job is already running")
                time.sleep(1)

    def __exit__(self, *_exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


class BulkJob:

    def __init__(self, job, cfg, log=print, progress=None, cancel=None, runner=run, guests_reader=None):
        self.job = job
        self.cfg = cfg or {}
        self.log = log
        self.progress = progress or (lambda _text: None)
        self.cancel = cancel or threading.Event()
        self.runner = runner
        self.read_guests = guests_reader or (lambda: read_guests(runner))
        section = _section(self.cfg, "stop")
        self.shutdown_timeout = parse_duration(section.get("shutdown_timeout", DEFAULT_SHUTDOWN_TIMEOUT)) or DEFAULT_SHUTDOWN_TIMEOUT
        self.force_stop = bool(section.get("force_stop", True))
        self._pending = []
        self.failed = []

    def _say(self, text):
        self.log(f'{time.strftime("%H:%M:%S")}: {text}')

    def _sleep(self, seconds):
        return not self.cancel.wait(timeout=seconds)

    def run(self, lock_wait=0):
        _enabled, order = job_settings(self.cfg, self.job)
        steps, problems = parse_order(order)
        for problem in problems:
            self._say(f"Proxmox order: {problem} - skipped")
        with _Lock(lock_wait):
            guests = self.read_guests()
            planned = plan(steps, guests)
            title = "Bulk start" if self.job == "start" else "Bulk stop"
            self._say(f"{title}: {len(planned)} step(s)")
            for n, (kind, value, options) in enumerate(planned, 1):
                if self.cancel.is_set():
                    self._say(f"{title}: cancelled")
                    break
                self.progress(f"{title} {n}/{len(planned)}")
                if kind == "start":
                    self._start(value, guests)
                elif kind == "stop":
                    self._stop(value, guests, options.get("timeout"))
                elif kind == "delay":
                    self._say(f"waiting {value:g}s")
                    self._sleep(value)
                elif kind == "wait":
                    self._wait(value)
            self._wait(None)
            self.progress("")
            self._say(f"{title}: done" + (f", failed: {', '.join(map(str, self.failed))}" if self.failed else ""))
        return not self.failed

    def _label(self, vmid, guests):
        g = guests.get(vmid)
        return f"{vmid} ({g['name']})" if g else str(vmid)

    def _start(self, ids, guests):
        for vmid in ids:
            if self.cancel.is_set():
                return
            g = guests.get(vmid)
            if g is None:
                self._say(f"{vmid}: not on this node - skipped")
                continue
            if g["status"] == "running":
                continue
            rc, out = self.runner([KINDS[g["kind"]][0], "start", str(vmid)], timeout=300)
            self._say(f"started {self._label(vmid, guests)}" if rc == 0 else f"start {self._label(vmid, guests)} failed: {out}")
            if rc != 0:
                self.failed.append(vmid)
            else:
                g["status"] = "running"

    def _stop(self, ids, guests, timeout=None):
        timeout = int(timeout or self.shutdown_timeout)
        for vmid in ids:
            g = guests.get(vmid)
            if g is None:
                self._say(f"{vmid}: not on this node - skipped")
                continue
            if g["status"] == "stopped":
                continue
            args = [KINDS[g["kind"]][0], "shutdown", str(vmid), "--timeout", str(timeout)]
            if self.force_stop:
                args += ["--forceStop", "1"]

            def work(vmid=vmid, g=g, args=args):
                rc, out = self.runner(args, timeout=timeout + 60)
                self._say(f"stopped {self._label(vmid, guests)}" if rc == 0 else f"stop {self._label(vmid, guests)} failed: {out}")
                if rc != 0:
                    self.failed.append(vmid)
                else:
                    g["status"] = "stopped"
            self._say(f"shutting down {self._label(vmid, guests)}")
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            self._pending.append(thread)

    def _wait(self, limit):
        deadline = None if limit is None else time.monotonic() + limit
        for thread in self._pending:
            while thread.is_alive():
                if deadline is not None and time.monotonic() >= deadline:
                    self._say(f"wait: gave up after {limit:g}s")
                    self._pending = [t for t in self._pending if t.is_alive()]
                    return
                thread.join(timeout=1)
        self._pending = []


def guest_command(vmid, kind, verb, runner=run):
    tool = KINDS[kind][0]
    if verb == "snapshot":
        return runner([tool, "snapshot", str(vmid), time.strftime("tuxd_%Y%m%d_%H%M%S")], timeout=1800)
    if verb not in ("start", "shutdown", "reboot", "stop"):
        raise ValueError(verb)
    return runner([tool, verb, str(vmid)], timeout=600)



def unit_texts(python, workdir, cfg):
    start_timeout = int(max_runtime(cfg, "start")) + 60
    stop_timeout = int(max_runtime(cfg, "stop")) + 60
    return {
        "start": (
            "[Unit]\n"
            "Description=TuxD: start Proxmox guests in the order from tuxd.conf (before Proxmox's own start)\n"
            "After=pve-cluster.service pvedaemon.service pvestatd.service pveproxy.service\n"
            "Before=pve-guests.service\n"
            "ConditionPathExists=/etc/pve\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"WorkingDirectory={workdir}\n"
            f"ExecStart={python} -m modules.proxmox start --boot\n"
            f"TimeoutStartSec={start_timeout}\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
        "stop": (
            "[Unit]\n"
            "Description=TuxD: stop Proxmox guests in the order from tuxd.conf (before Proxmox's own shutdown)\n"
            "After=pve-guests.service\n"
            "ConditionPathExists=/etc/pve\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            f"WorkingDirectory={workdir}\n"
            "ExecStart=/bin/true\n"
            f"ExecStop={python} -m modules.proxmox stop --shutdown\n"
            f"TimeoutStopSec={stop_timeout}\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        ),
    }


def sync_units(cfg, python, workdir, runner=run, unit_dir=UNIT_DIR):
    if os.geteuid() != 0:
        return {job: ("needs root" if job_settings(cfg, job)[0] else "off") for job in UNITS}
    texts = unit_texts(python, workdir, cfg)
    states, reload_needed, enable = {}, False, []
    for job, name in UNITS.items():
        path = Path(unit_dir, name)
        wanted = job_settings(cfg, job)[0]
        try:
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if wanted:
                if current != texts[job]:
                    path.write_text(texts[job], encoding="utf-8")
                    reload_needed = True
                enable.append(job)
                states[job] = "installed"
            else:
                if current is not None:
                    runner(["systemctl", "disable", name], timeout=30)
                    path.unlink()
                    reload_needed = True
                states[job] = "off"
        except OSError as e:
            states[job] = f"error: {e}"
    if reload_needed:
        runner(["systemctl", "daemon-reload"], timeout=60)
    for job in enable:
        args = ["systemctl", "enable", "--now", UNITS[job]] if job == "stop" else ["systemctl", "enable", UNITS[job]]
        rc, out = runner(args, timeout=60)
        if rc != 0:
            states[job] = f"error: {out}"
    return states



def _load_config(path="tuxd.conf"):
    import yaml
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    extra = (cfg.get("device") or {}).get("extra_config_path", "")
    if extra:
        try:
            from modules.config_sync import merge_extra_config
            merge_extra_config(cfg, yaml.safe_load(Path(extra).read_text(encoding="utf-8")) or {})
        except Exception as e:
            print(f"TuxD Proxmox: extra config not loaded ({e})")
    return cfg


def _system_state():
    try:
        return subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in UNITS:
        print("usage: python3 -m modules.proxmox start|stop [--boot|--shutdown]")
        return 2
    job = argv[0]
    if "--boot" in argv and _system_state() not in ("initializing", "starting"):
        return 0
    if "--shutdown" in argv and _system_state() != "stopping":
        return 0
    cfg = (_load_config().get("proxmox") or {})
    if not cfg.get("enabled", False) or not job_settings(cfg, job)[0]:
        return 0
    try:
        ok = BulkJob(job, cfg, log=lambda line: print(line, flush=True)).run(lock_wait=max_runtime(cfg, job))
    except JobBusy as e:
        print(e)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
