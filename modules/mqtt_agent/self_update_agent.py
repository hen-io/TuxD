import json
import threading

import yaml

from .config_agent import _CONFIG_FILE

_RELEASE_CHANNELS = ("stable", "rc", "beta")
_RELEASE_CHANNEL_LABELS = {"stable": "Stable", "rc": "RC", "beta": "Beta"}
_RELEASE_CHANNEL_BY_LABEL = {v: k for k, v in _RELEASE_CHANNEL_LABELS.items()}


class SelfUpdateMixin:
    def init_self_update(self):
        device_cfg = self.config.get("device", {}) or {}
        self._self_update_interval = float(device_cfg.get("self_update_check_interval", 300))
        self._self_update_allow_install = bool(device_cfg.get("self_update_allow_install", True))
        self._self_update_installing = False
        self._self_update_last_state = None
        self._release_channel_cmd_topic = None

    def register_self_update(self):
        base = f"{self.base_topic}/self_update"

        command_topic = f"{base}/set" if self._self_update_allow_install else None

        self._update_discovery(
            "self_update",
            "TuxD update",
            f"{base}/state",
            command_topic=command_topic,
            icon="mdi:linux",
            entity_category="diagnostic",
            ha_object_id=f"{self.device_slug}_tuxd",
        )

        self._button_discovery(
            "self_update_check",
            "Check for TuxD Updates",
            f"{base}/check/set",
            icon="mdi:cloud-refresh",
            entity_category="diagnostic",
        )

        if not self._self_update_installing and self._self_update_last_state is None:
            self.publish(
                f"{base}/state",
                json.dumps({
                    "installed_version": self.version,
                    "latest_version": self.version,
                    "title": "TuxD",
                    "in_progress": False,
                }),
                retain=True,
            )

    def register_release_channel_select(self):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                fresh_cfg = yaml.safe_load(f) or {}
        except Exception:
            fresh_cfg = self.config

        current = str((fresh_cfg.get("device") or {}).get("self_update_release_channel", "stable")).strip().lower()
        if current not in _RELEASE_CHANNELS:
            current = "stable"

        oid = "cfgselect_self_update_release_channel"
        state_topic = f"{self.base_topic}/{oid}"
        command_topic = f"{state_topic}/set"
        self._release_channel_cmd_topic = command_topic

        payload = {
            "name": "TuxD Release Channel",
            "state_topic": state_topic,
            "command_topic": command_topic,
            "options": [_RELEASE_CHANNEL_LABELS[c] for c in _RELEASE_CHANNELS],
            "unique_id": f"{self.config['device']['name']}_{oid}",
            "device": self.device_info,
            "icon": "mdi:source-branch",
            "entity_category": "config",
        }
        self.publish(self._discovery_topic("select", oid), json.dumps(payload), retain=True)
        self.publish(state_topic, _RELEASE_CHANNEL_LABELS[current], retain=True)

    def handle_release_channel_select(self, topic, payload):
        if topic != self._release_channel_cmd_topic:
            return
        channel = _RELEASE_CHANNEL_BY_LABEL.get(payload.strip())
        if channel is None:
            return

        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg.setdefault("device", {})["self_update_release_channel"] = channel
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception:
            return

        oid = "cfgselect_self_update_release_channel"
        self.publish(f"{self.base_topic}/{oid}", _RELEASE_CHANNEL_LABELS[channel], retain=True)
        self._cfgnum_schedule_restart()

    def _self_update_check(self, force=False):
        if not callable(self._update_checker):
            return None, None, None, None
        try:
            return self._update_checker(force=force)
        except Exception:
            return None, None, None, None

    def _publish_self_update_state(self, force=False):
        base = f"{self.base_topic}/self_update"
        new_version, src_type, src_val, release_notes = self._self_update_check(force=force)

        state = {
            "installed_version": self.version,
            "latest_version": new_version if new_version else self.version,
            "title": "TuxD",
        }
        if new_version and src_type:
            summary = (release_notes or {}).get("summary") or ""
            if len(summary) > 4000:
                summary = summary[:4000].rstrip() + "..."
            state["release_summary"] = summary or f"{new_version} available via the {src_type} update source."
            release_url = (release_notes or {}).get("url")
            if release_url:
                state["release_url"] = release_url

        self._self_update_last_state = dict(state)
        self.publish(f"{base}/state", json.dumps(state), retain=True)

    def _set_self_update_progress(self, in_progress):
        if not self._self_update_last_state:
            return
        state = dict(self._self_update_last_state)
        state["in_progress"] = in_progress
        self.publish(f"{self.base_topic}/self_update/state", json.dumps(state), retain=True)

    def handle_self_update_check(self):
        threading.Thread(target=lambda: self._publish_self_update_state(force=True), daemon=True).start()

    def self_update_loop(self):
        while not self._stop_event.is_set():
            try:
                self._publish_self_update_state()
            except Exception:
                pass
            self._stop_event.wait(timeout=self._self_update_interval)

    def handle_self_update_install(self):
        if not self._self_update_allow_install or self._self_update_installing:
            return
        if not callable(self._update_applier):
            return

        self._self_update_installing = True
        threading.Thread(target=self._run_self_update_install, daemon=True).start()

    def _run_self_update_install(self):
        try:
            new_version, src_type, src_val, _release_notes = self._self_update_check(force=True)
            if not new_version or not src_type or not src_val:
                return
            self._set_self_update_progress(True)
            self.set_error(True, "TuxD update installing, restarting")
            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, f"Installing TuxD {new_version}...")
            self._update_applier(new_version, src_type, src_val)
        except Exception:
            self._set_self_update_progress(False)
        finally:
            self._self_update_installing = False

    def handle_self_update_install_from_url(self, url):
        if not self._self_update_allow_install or self._self_update_installing:
            return
        if not callable(self._update_applier):
            return
        url = (url or "").strip()
        if not url:
            return

        self._self_update_installing = True
        threading.Thread(target=self._run_self_update_install_from_url, args=(url,), daemon=True).start()

    def _run_self_update_install_from_url(self, url):
        try:
            self._set_self_update_progress(True)
            self.set_error(True, "TuxD update installing (offline tarball), restarting")
            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, f"Installing TuxD from {url}...")
            self._update_applier("offline-tarball", "url", url)
        except Exception:
            self._set_self_update_progress(False)
        finally:
            self._self_update_installing = False
