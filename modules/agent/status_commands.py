import json
import time
from .shared import run_cmd

_DEFAULT_SUFFIXES = {
    "updates_available": " new updates",
}


class StatusCommandsMixin:
    def init_status(self):
        self.commands_status = (self.config.get("commands") or {}).get("status") or {}
        self._status_last_run = {}
        self._status_env = {}

    def register_status_commands(self):
        for key, cfg in self.commands_status.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", False):
                continue

            obj_id = f"status_{key}"
            topic = f"{self.base_topic}/{key}"
            attr_topic = f"{self.base_topic}/{key}_attributes"
            custom_name = cfg.get("name")

            self._sensor_discovery(
                object_id=obj_id,
                name=custom_name or key.replace("_", " ").title(),
                state_topic=topic,
                icon=cfg.get("icon") or "mdi:information-outline",
                attributes_topic=attr_topic,
                device_class=cfg.get("device_class"),
            )

    def run_status_commands_loop(self):
        while not self._stop_event.is_set():
            now = time.time()

            for key, cfg in self.commands_status.items():
                if self._stop_event.is_set():
                    return
                if not isinstance(cfg, dict):
                    continue

                if not cfg.get("enabled", False):
                    continue

                interval = cfg.get("update_interval", 10)
                cmd = cfg.get("cmd", "")

                last = self._status_last_run.get(key, 0)
                if now - last < interval:
                    continue

                self._status_last_run[key] = now

                if not cmd:
                    continue

                expanded = cmd
                for var, val in self._status_env.items():
                    expanded = expanded.replace(f"${var}", val)

                output = run_cmd(expanded)

                lines = output.split("\n")
                value = lines[0] if lines else ""
                attrs = {"lines": lines[1:]} if len(lines) > 1 else {}

                self._status_env[key] = value

                suffix = cfg.get("suffix") or _DEFAULT_SUFFIXES.get(key, "")
                self.publish(f"{self.base_topic}/{key}", value + suffix if value else value)
                self.publish(f"{self.base_topic}/{key}_attributes", json.dumps(attrs))

            self._stop_event.wait(timeout=1)
