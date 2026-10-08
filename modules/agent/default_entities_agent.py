import time
from pathlib import Path

import yaml

from .shared import run_cmd

_BIN = Path(__file__).resolve().parent.parent.parent / "bin"


class DefaultEntitiesMixin:
    def init_default_entities(self):
        self._default_entities = []
        self._default_entities_last_run = {}
        try:
            path = _BIN / "default_entities.yaml"
            if path.exists():
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                self._default_entities = data.get("sensors", []) or []
        except Exception:
            pass

    def register_default_entities(self):
        for s in self._default_entities:
            if not isinstance(s, dict) or not s.get("enabled", True):
                continue

            object_id = s.get("object_id") or s.get("name", "").lower().replace(" ", "_")

            self._sensor_discovery(
                object_id=f"builtin_{object_id}",
                name=s.get("name", object_id),
                state_topic=f"{self.base_topic}/builtin/{object_id}",
                unit=s.get("unit_of_measurement"),
                icon=s.get("icon"),
                entity_category=s.get("entity_category"),
                device_class=s.get("device_class"),
            )

    def run_default_entities_loop(self):
        while not self._stop_event.is_set():
            now = time.time()

            for s in self._default_entities:
                if self._stop_event.is_set():
                    return
                if not isinstance(s, dict) or not s.get("enabled", True):
                    continue

                object_id = s.get("object_id") or s.get("name", "").lower().replace(" ", "_")
                cmd = s.get("cmd", "")
                interval = s.get("update_interval", 60)

                if now - self._default_entities_last_run.get(object_id, 0) < interval:
                    continue

                self._default_entities_last_run[object_id] = now

                if not cmd:
                    continue

                output = run_cmd(cmd)
                lines = output.split("\n")
                value = lines[0] if lines else ""
                suffix = s.get("suffix", "")

                self.publish(
                    f"{self.base_topic}/builtin/{object_id}",
                    value + suffix if value else value,
                )

            self._stop_event.wait(timeout=1)
