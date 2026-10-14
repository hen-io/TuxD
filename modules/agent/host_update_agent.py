import json
import shlex
import threading
import time
from pathlib import Path

from .shared import SENSOR_CMD_TIMEOUT, run_cmd
from modules.host_update import update_count, update_list, default_install_cmd, parse_leading_int

_DEBIAN_UPDATE_SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "etc" / "debian_update.sh"


_STANDARD_BUTTONS = {
    "update": ("Update", "update_cmd", "mdi:package-up"),
    "update_and_reboot": ("Update and reboot", "update_reboot_cmd", "mdi:restart-alert"),
    "update_and_reboot_if_needed": ("Update and reboot if needed", "update_reboot_if_needed_cmd", "mdi:restart"),
}

DEFAULT_INSTALL_BUTTON = "Update and reboot if needed"

_REBOOT_IF_NEEDED = (
    "if [ -f /var/run/reboot-required ] || "
    "{ command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; }; "
    "then echo 'Reboot needed - rebooting in 1 minute'; sudo shutdown -r +1; "
    "else echo 'No reboot needed'; fi"
)

_BUTTON_ALIASES = {"update_&_reboot": "update_and_reboot"}


class HostUpdateMixin:
    def init_host_update(self):
        self._host_update_installing = False
        self._host_update_last_state = None
        self._host_update_button_cmds = {}

        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False):
            return

        legacy = {}
        kept = []
        for b in self.custom_buttons:
            slug = str(b.get("name", "")).replace(" ", "_").lower() if isinstance(b, dict) else ""
            slug = _BUTTON_ALIASES.get(slug, slug)
            if slug in _STANDARD_BUTTONS:
                legacy[slug] = b.get("command") or b.get("cmd", "")
            else:
                kept.append(b)
        self.custom_buttons = kept

        install = default_install_cmd()
        defaults = {
            "update": install,
            "update_and_reboot": f"{install} && sudo reboot",
            "update_and_reboot_if_needed": f"{install} && {{ {_REBOOT_IF_NEEDED}; }}",
        }
        if "debian" in self._distro_ids():
            script = shlex.quote(str(_DEBIAN_UPDATE_SCRIPT))
            defaults = {
                "update": f"bash {script} --no-reboot",
                "update_and_reboot": f"bash {script} --always-reboot",
                "update_and_reboot_if_needed": f"bash {script}",
            }
        for slug, (_name, key, _icon) in _STANDARD_BUTTONS.items():
            self._host_update_button_cmds[slug] = cfg.get(key) or legacy.get(slug) or defaults[slug]

    def register_host_update(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False):
            return

        base = f"{self.base_topic}/host_update"

        command_topic = None
        if cfg.get("allow_install", False):
            command_topic = f"{base}/set"

        device_class = cfg.get("device_class", "") or None

        for slug, (name, _key, icon) in _STANDARD_BUTTONS.items():
            self._button_discovery(
                f"custom_button_{slug}",
                name,
                f"{self.base_topic}/custom_button/{slug}/set",
                icon=icon,
            )
            self.client.subscribe(f"{self.base_topic}/custom_button/{slug}/set")

        self._update_discovery(
            "host_update",
            cfg.get("name", "Host updates"),
            f"{base}/state",
            command_topic=command_topic,
            device_class=device_class,
            icon="mdi:server-security",
            entity_category="diagnostic",
            attributes_topic=f"{base}/state_attributes",
        )

        self.publish(self._discovery_topic("sensor", "host_update_available"), "", retain=True)

        self._sensor_discovery(
            "host_new_package_version_available",
            cfg.get("packages_sensor_name", "New package version available"),
            f"{base}/package_versions",
            icon="mdi:format-list-bulleted",
            entity_category="diagnostic",
        )

        if not self._host_update_installing and self._host_update_last_state is None:
            self.publish(
                f"{base}/state",
                json.dumps({
                    "installed_version": "0",
                    "latest_version": "0",
                    "title": cfg.get("name", "Host updates"),
                    "in_progress": False,
                }),
                retain=True,
            )

    def host_update_loop(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False):
            return

        interval = float(cfg.get("update_interval", 3600))

        while not self._stop_event.is_set():
            published = False
            try:
                published = self._publish_host_update_state()
            except Exception:
                published = False
            self._stop_event.wait(timeout=interval if published else min(interval, 30.0))

    def _docker_update_count(self, cfg):
        if not cfg.get("include_docker_updates", True):
            return 0
        counter = getattr(self, "docker_update_count", None)
        return counter() if callable(counter) else 0

    def _kernel_version(self):
        return run_cmd("uname -r", timeout=SENSOR_CMD_TIMEOUT).strip()

    def _latest_kernel_from_pkgs(self, pkgs):
        for p in pkgs:
            head = p.split(">", 1)[0] if ">" in p else p
            if "kernel" in head.lower() or "linux-image" in head.lower():
                if ">" in p:
                    return p.rsplit(">", 1)[-1].strip()
        return ""

    def _publish_host_update_state(self):
        cfg = self.config.get("host_update", {}) or {}
        base = f"{self.base_topic}/host_update"

        source = (cfg.get("count_source") or "updates_available").strip()
        os_count = parse_leading_int(self.get_state(f"{self.base_topic}/{source}")) if source else None
        if os_count is None:
            os_count = update_count(cfg.get("check_cmd") or None)

        docker_count = self._docker_update_count(cfg)

        if os_count is None and docker_count is None:
            return False

        os_count = os_count or 0
        docker_count = docker_count or 0
        count = os_count + docker_count

        title = cfg.get("name", "Host updates")
        state = {
            "installed_version": "0",
            "latest_version": str(count),
            "title": title,
        }

        pkgs = update_list(cfg.get("list_cmd") or None) if os_count > 0 else []

        if count > 0:
            parts = []
            if pkgs:
                parts.append("\n".join(f"- {p}" for p in pkgs))
            parts.append(self._host_update_install_note(cfg))
            state["release_summary"] = "\n\n".join(parts)

        state["distro"] = self._distro_ids()
        self._host_update_last_state = dict(state)
        self.publish(f"{base}/state", json.dumps(state), retain=True)
        self.publish(f"{base}/package_versions", self._packages_value(pkgs))

        attrs = {"kernel_version": self._kernel_version()}
        latest_kernel = self._latest_kernel_from_pkgs(pkgs)
        if latest_kernel:
            attrs["latest_kernel_version"] = latest_kernel
        self.publish(f"{base}/state_attributes", json.dumps(attrs), retain=True)
        return True

    @staticmethod
    def _distro_ids():
        ids = []
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                for line in f:
                    key, _, value = line.strip().partition("=")
                    if key in ("ID", "ID_LIKE"):
                        ids += value.strip("\"'").lower().split()
        except OSError:
            pass
        return ids

    def handle_host_update_check(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False):
            return
        threading.Thread(target=self._publish_host_update_state, daemon=True).start()

    def _set_host_update_progress(self, in_progress):
        if not self._host_update_last_state:
            return
        state = dict(self._host_update_last_state)
        state["in_progress"] = in_progress
        self.publish(f"{self.base_topic}/host_update/state", json.dumps(state), retain=True)

    def _packages_value(self, pkgs):
        if not pkgs:
            return ""

        joined = ", ".join(pkgs)
        if len(joined) <= 255:
            return joined

        out = []
        length = 0
        for name in pkgs:
            add = len(name) + (2 if out else 0)
            if length + add > 251:
                break
            out.append(name)
            length += add

        return ", ".join(out) + " ..."

    def _host_update_button_cmd(self, cfg):
        name = str(cfg.get("install_button_name") or DEFAULT_INSTALL_BUTTON).strip().lower()
        if not name:
            return None
        slug = name.replace(" ", "_")
        standard = self._host_update_button_cmds.get(_BUTTON_ALIASES.get(slug, slug))
        if standard:
            return standard
        for b in (self.config.get("button") or []):
            if isinstance(b, dict) and str(b.get("name", "")).strip().lower() == name:
                cmd = b.get("command")
                if cmd:
                    return cmd
        return None

    def _host_update_install_plan(self, cfg):
        if cfg.get("use_custom_install_cmd", False):
            if cfg.get("install_cmd"):
                return cfg.get("install_cmd"), self.tr("custom install_cmd")
        else:
            cmd = self._host_update_button_cmd(cfg)
            if cmd:
                name = str(cfg.get("install_button_name") or DEFAULT_INSTALL_BUTTON).strip()
                return cmd, f'{self.tr("button")} "{name}"'
        return default_install_cmd(), self.tr("default for this system")

    def _host_update_install_note(self, cfg):
        if not cfg.get("allow_install", False):
            return self.tr("Installing from Home Assistant is disabled (host_update.allow_install: false).")
        cmd, source = self._host_update_install_plan(cfg)
        return f'{self.tr("Install runs")} ({source}): {cmd}'

    def handle_host_update_button(self, slug):
        cmd = self._host_update_button_cmds.get(slug)
        if not cmd or self._host_update_installing:
            return
        self._host_update_installing = True
        threading.Thread(target=self._run_host_update_button, args=(slug, cmd), daemon=True).start()

    def _run_host_update_button(self, slug, cmd):
        name = _STANDARD_BUTTONS[slug][0]
        out = ""
        try:
            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, f'{time.strftime("%H:%M:%S")}: Button "{name}" pressed! > {cmd}')
            if slug == "update_and_reboot":
                self.set_warning(True, f'"{name}" button triggered a reboot/shutdown')
            self._set_host_update_progress(True)
            with self.busy(f"button: {name}"):
                out = run_cmd(cmd)
            if out and self._terminal_output_enabled():
                for line in out.splitlines():
                    if line:
                        self.publish(self.terminal_output_topic, line)
        except Exception:
            pass
        finally:
            self._host_update_installing = False

        try:
            cfg = self.config.get("host_update", {}) or {}
            source = (cfg.get("count_source") or "updates_available").strip()
            if source:
                self.refresh_status_command(source)
            if not self._publish_host_update_state():
                self._set_host_update_progress(False)
        except Exception:
            self._set_host_update_progress(False)
        try:
            if "rebooting in 1 minute" in (out or ""):
                self.set_warning(True, f'"{name}": rebooting after the updates')
            elif slug == "update_and_reboot":
                self.set_warning(False)
        except Exception:
            pass

    def handle_host_update_install(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False) or not cfg.get("allow_install", False):
            return
        if self._host_update_installing:
            return

        cmd, _source = self._host_update_install_plan(cfg)

        if not cmd:
            return

        self._host_update_installing = True
        threading.Thread(target=self._run_host_update_install, args=(cmd,), daemon=True).start()

    def _run_host_update_install(self, cmd):
        self._set_host_update_progress(True)
        try:
            self.set_warning(True, "Host update running - host may reboot")
        except Exception:
            pass
        try:
            with self.busy("host update"):
                out = run_cmd(cmd)
            if self._terminal_output_enabled() and out:
                for line in out.splitlines():
                    if line:
                        self.publish(self.terminal_output_topic, line)
        except Exception:
            pass

        self._host_update_installing = False

        try:
            cfg = self.config.get("host_update", {}) or {}
            source = (cfg.get("count_source") or "updates_available").strip()
            if source:
                self.refresh_status_command(source)
            if not self._publish_host_update_state():
                self._set_host_update_progress(False)
        except Exception:
            self._set_host_update_progress(False)

        try:
            if self._terminal_output_enabled():
                self.publish(
                    self.terminal_output_topic,
                    "Host update finished, restarting TuxD to refresh state...",
                )
                time.sleep(0.5)
        except Exception:
            pass

        self._hard_restart()
