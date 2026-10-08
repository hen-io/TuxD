import time
import datetime
import threading
from .shared import run_cmd


_POWER_BUTTONS = {
    "reboot": ("Reboot", "reboot_cmd", "mdi:restart", "sudo reboot"),
    "shutdown": ("Shutdown", "shutdown_cmd", "mdi:power", "sudo shutdown now"),
}


class CustomEntitiesMixin:
    def init_custom(self):
        self.custom_sensors = self.config.get("sensor", []) or []

        device = self.config.get("device", {}) or {}
        legacy = {}
        kept = []
        for b in self.config.get("button", []) or []:
            slug = str(b.get("name", "")).replace(" ", "_").lower() if isinstance(b, dict) else ""
            if slug in _POWER_BUTTONS:
                legacy[slug] = b.get("command") or b.get("cmd", "")
            else:
                kept.append(b)
        power = [
            {"name": name, "command": device.get(key) or legacy.get(slug) or default, "icon": icon}
            for slug, (name, key, icon, default) in _POWER_BUTTONS.items()
        ]
        self.custom_buttons = power + kept

    def register_custom_sensors(self):
        for s in self.custom_sensors:
            name = s["name"]
            slug = name.replace(" ", "_").lower()
            obj_id = f"custom_sensor_{slug}"
            topic = f"{self.base_topic}/custom_sensor/{slug}"

            self._sensor_discovery(
                object_id=obj_id,
                name=name,
                state_topic=topic,
                unit=s.get("unit_of_measurement"),
                icon=s.get("icon"),
                device_class=s.get("device_class")
            )

    def custom_sensor_loop(self):
        next_run = {}
        while not self._stop_event.is_set():
            now = time.time()
            for s in self.custom_sensors:
                if self._stop_event.is_set():
                    return
                name = s["name"]
                slug = name.replace(" ", "_").lower()
                cmd = s.get("cmd") or s.get("command", "")
                interval = s.get("update_interval", 10)

                nr = next_run.get(slug, 0)
                if now >= nr:
                    output = run_cmd(cmd)
                    topic = f"{self.base_topic}/custom_sensor/{slug}"
                    self.publish(topic, output)
                    next_run[slug] = now + interval

            self._stop_event.wait(timeout=1)

    def register_custom_buttons(self):
        for b in self.custom_buttons:
            name = b["name"]
            slug = name.replace(" ", "_").lower()
            obj_id = f"custom_button_{slug}"
            topic = f"{self.base_topic}/custom_button/{slug}/set"

            self._button_discovery(
                object_id=obj_id,
                name=name,
                command_topic=topic,
                icon=b.get("icon")
            )

            self.client.subscribe(topic)

    def handle_custom_button(self, topic):
        slug = topic.split("/")[-2]
        for b in self.custom_buttons:
            name_slug = b["name"].replace(" ", "_").lower()
            if name_slug == slug:
                cmd = b.get("command") or b.get("cmd", "")
                write_to_terminal = bool(b.get("write_to_terminal", True))
                if self._terminal_output_enabled():
                    ts = datetime.datetime.now().strftime("%H:%M:%S")
                    msg = f'{ts}: Button "{b["name"]}" pressed! > {cmd}'
                    self.publish(self.terminal_output_topic, msg)
                cmd_lower = cmd.lower()
                if any(kw in cmd_lower for kw in ("shutdown", "reboot", "poweroff", "halt")):
                    try:
                        self.set_error(True, f'"{b["name"]}" button triggered a reboot/shutdown')
                    except Exception:
                        pass
                if write_to_terminal:
                    def _run(c=cmd, n=b["name"]):
                        with self.busy(f"button: {n}"):
                            output = run_cmd(c)
                        if output and self._terminal_output_enabled():
                            for line in output.split("\n"):
                                if line:
                                    self.publish(self.terminal_output_topic, line)
                    threading.Thread(target=_run, daemon=True).start()
                else:
                    def _run_quiet(c=cmd, n=b["name"]):
                        with self.busy(f"button: {n}"):
                            run_cmd(c)
                    threading.Thread(target=_run_quiet, daemon=True).start()
                return
