import json
import os
import sys
import threading
import time

from .base import run_cmd
from modules.host_update import update_count, update_list, default_install_cmd, parse_leading_int


class HostUpdateMixin:
    def init_host_update(self):
        self._host_update_installing = False
        self._host_update_last_state = None

    def register_host_update(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False):
            return

        base = f"{self.base_topic}/host_update"

        command_topic = None
        if cfg.get("allow_install", False):
            command_topic = f"{base}/set"

        device_class = cfg.get("device_class", "") or None

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

        self._sensor_discovery(
            "host_update_available",
            cfg.get("count_sensor_name", "Available updates"),
            f"{base}/available",
            icon="mdi:package-up",
            entity_category="diagnostic",
        )

        self._sensor_discovery(
            "host_new_package_version_available",
            cfg.get("packages_sensor_name", "New package version available"),
            f"{base}/package_versions",
            icon="mdi:format-list-bulleted",
            entity_category="diagnostic",
            ha_object_id=f"{self.device_slug}_new_package_version_available",
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
        return run_cmd("uname -r").strip()

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

        source = (cfg.get("count_source") or "").strip()
        if source:
            os_count = parse_leading_int(self.get_state(f"{self.base_topic}/{source}"))
        else:
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

        if pkgs:
            state["release_summary"] = "\n".join(f"- {p}" for p in pkgs)

        self._host_update_last_state = dict(state)
        self.publish(f"{base}/state", json.dumps(state), retain=True)
        self.publish(f"{base}/available", f"{count} updates available")
        self.publish(f"{base}/package_versions", self._packages_value(pkgs))

        attrs = {"kernel_version": self._kernel_version()}
        latest_kernel = self._latest_kernel_from_pkgs(pkgs)
        if latest_kernel:
            attrs["latest_kernel_version"] = latest_kernel
        self.publish(f"{base}/state_attributes", json.dumps(attrs), retain=True)
        return True

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
        name = str(cfg.get("install_button_name") or "Update and reboot").strip().lower()
        if not name:
            return None
        for b in (self.config.get("button") or []):
            if isinstance(b, dict) and str(b.get("name", "")).strip().lower() == name:
                cmd = b.get("command")
                if cmd:
                    return cmd
        return None

    def handle_host_update_install(self):
        cfg = self.config.get("host_update", {}) or {}
        if not cfg.get("enabled", False) or not cfg.get("allow_install", False):
            return
        if self._host_update_installing:
            return

        if cfg.get("use_custom_install_cmd", False):
            cmd = cfg.get("install_cmd") or default_install_cmd()
        else:
            cmd = self._host_update_button_cmd(cfg) or default_install_cmd()

        if not cmd:
            return

        self._host_update_installing = True
        threading.Thread(target=self._run_host_update_install, args=(cmd,), daemon=True).start()

    def _run_host_update_install(self, cmd):
        self._set_host_update_progress(True)
        try:
            self.set_error(True, "Host update running - host may reboot")
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

        try:
            if self._terminal_output_enabled():
                self.publish(
                    self.terminal_output_topic,
                    "Host update finished, restarting TuxD to refresh state...",
                )
                time.sleep(0.5)
        except Exception:
            pass

        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            self._host_update_installing = False
            self._set_host_update_progress(False)
