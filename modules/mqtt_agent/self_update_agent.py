import json
import threading


class SelfUpdateMixin:
    def init_self_update(self):
        device_cfg = self.config.get("device", {}) or {}
        self._self_update_interval = float(device_cfg.get("self_update_check_interval", 21600))
        self._self_update_allow_install = bool(device_cfg.get("self_update_allow_install", True))
        self._self_update_installing = False
        self._self_update_last_state = None

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

    def _self_update_check(self):
        if not callable(self._update_checker):
            return None, None, None, None
        try:
            return self._update_checker()
        except Exception:
            return None, None, None, None

    def _publish_self_update_state(self):
        base = f"{self.base_topic}/self_update"
        new_version, src_type, src_val, release_notes = self._self_update_check()

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
        threading.Thread(target=self._publish_self_update_state, daemon=True).start()

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
            new_version, src_type, src_val, _release_notes = self._self_update_check()
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
