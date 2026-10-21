import threading
import time

from modules.lm_sensors import discover_active, read_lm_sensors


class LMSensorsMixin:

    def _lm_cfg(self):
        return (self.config or {}).get("lm_sensors", {}) or {}

    def _lm_sensors(self):
        listed = self._lm_cfg().get("sensors", {}) or {}
        return {k: (sc if isinstance(sc, dict) else {}) for k, sc in listed.items()
                if not isinstance(sc, dict) or sc.get("enabled", True)}

    def register_lm_sensors(self):
        lm_cfg = self._lm_cfg()
        if not lm_cfg.get("enabled", False):
            return

        overrides = lm_cfg.get("overrides", {}) or {}
        default_icon = lm_cfg.get("default_icon", "mdi:thermometer")
        default_unit = lm_cfg.get("default_unit_of_measurement")

        avg_keys = []
        for raw_key, sc in self._lm_sensors().items():
            self._sensor_discovery(
                f"lm_{raw_key}",
                sc.get("name") or overrides.get(raw_key) or raw_key,
                f"{self.base_topic}/lm_{raw_key}",
                unit=sc.get("unit_of_measurement", default_unit),
                icon=sc.get("icon") or default_icon,
                state_class="measurement",
                device_class=sc.get("device_class"),
            )
            if sc.get("include_in_avg_sensor", False):
                avg_keys.append(raw_key)

        if avg_keys:
            self._sensor_discovery(
                "lm_avg_temp",
                lm_cfg.get("avg_sensor_name") or "Average temperature",
                f"{self.base_topic}/lm_avg_temp",
                unit=default_unit or "°C",
                icon=lm_cfg.get("avg_sensor_icon") or default_icon,
                state_class="measurement",
            )


    def run_lm_discovery(self):
        lock = self.__dict__.setdefault("_lm_discovery_lock", threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("lm-sensors discovery is already running")
        try:
            say = self._lm_say
            lm_cfg = self._lm_cfg()
            try:
                window = float(lm_cfg.get("auto_discovery_window", 10))
            except (TypeError, ValueError):
                window = 10.0
            with self.busy("lm-sensors: discovery"):
                say(f"lm-sensors: looking for live readings ({window:g}s)...")
                found = discover_active(window=window, stop_event=self._stop_event)
                added = {}

                def change(cfg):
                    section = cfg.setdefault("lm_sensors", {})
                    if not isinstance(section.get("sensors"), dict):
                        section["sensors"] = {}
                    for raw_key, meta in found.items():
                        if raw_key not in section["sensors"]:
                            entry = {"enabled": True}
                            entry.update({k: v for k, v in meta.items() if v is not None})
                            section["sensors"][raw_key] = entry
                            added[raw_key] = entry
                    section["auto_discovery"] = False
                self.edit_config(change)

            live = self.config.setdefault("lm_sensors", {})
            if not isinstance(live.get("sensors"), dict):
                live["sensors"] = {}
            live["sensors"].update(added)
            live["auto_discovery"] = False
            if added:
                self.register_lm_sensors()
                self._lm_generation = getattr(self, "_lm_generation", 0) + 1
                self._lm_start_loop()
            names = ", ".join(sorted(meta.get("name", k) for k, meta in added.items()))
            say(f"lm-sensors: {len(found)} live reading(s), {len(added)} added" + (f": {names}" if names else ""))
            return len(added)
        finally:
            lock.release()

    def _lm_say(self, line):
        if self._terminal_output_enabled():
            self.publish(self.terminal_output_topic, line)
        elif self.tty_output:
            print(self._gray(line))

    def _lm_start_loop(self):
        if getattr(self, "_lm_loop_running", False) or not self._lm_cfg().get("enabled", False):
            return
        threading.Thread(target=self.lm_sensors_loop, daemon=True).start()


    def lm_sensors_loop(self, interval=None, overrides=None):
        lm_cfg = self._lm_cfg()
        if not lm_cfg.get("enabled", False) or getattr(self, "_lm_loop_running", False):
            return
        self._lm_loop_running = True
        try:
            if lm_cfg.get("auto_discovery", True):
                try:
                    self.run_lm_discovery()
                except Exception as e:
                    self._lm_say(f"lm-sensors: discovery failed: {e}")
            self._lm_poll(interval)
        finally:
            self._lm_loop_running = False

    def _lm_poll(self, interval):
        lm_cfg = self._lm_cfg()
        default_interval = int(lm_cfg.get("default_interval", interval or 15))
        default_accuracy_decimals = int(lm_cfg.get("default_accuracy_decimals", 1))

        while not self._stop_event.is_set():
            generation = getattr(self, "_lm_generation", 0)
            sensors_cfg = self._lm_sensors()
            schedule, last_sent, avg_keys = {}, {}, []
            for raw_key, sc in sensors_cfg.items():
                try:
                    schedule[raw_key] = int(sc.get("update_interval", default_interval))
                except (TypeError, ValueError):
                    schedule[raw_key] = default_interval
                last_sent[raw_key] = 0.0
                if sc.get("include_in_avg_sensor", False):
                    avg_keys.append(raw_key)
            if not schedule:
                return

            while not self._stop_event.is_set() and getattr(self, "_lm_generation", 0) == generation:
                now = time.time()
                due_keys = [k for k, every in schedule.items() if every <= 0 or (now - last_sent.get(k, 0.0)) >= every]
                if due_keys:
                    values = read_lm_sensors(include=due_keys)
                    any_avg_updated = False
                    for raw_key in due_keys:
                        if raw_key not in values:
                            continue
                        try:
                            decimals = int(sensors_cfg[raw_key].get("accuracy_decimals", default_accuracy_decimals))
                        except (TypeError, ValueError):
                            decimals = default_accuracy_decimals
                        self.publish(f"{self.base_topic}/lm_{raw_key}", round(values[raw_key], decimals))
                        last_sent[raw_key] = now
                        if raw_key in avg_keys:
                            any_avg_updated = True

                    if any_avg_updated and avg_keys:
                        try:
                            vals = read_lm_sensors(include=avg_keys)
                            nums = [v for v in vals.values() if isinstance(v, (int, float))]
                            if nums:
                                try:
                                    avg_decimals = int(lm_cfg.get("avg_accuracy_decimals", default_accuracy_decimals))
                                except (TypeError, ValueError):
                                    avg_decimals = default_accuracy_decimals
                                self.publish(f"{self.base_topic}/lm_avg_temp", round(sum(nums) / len(nums), avg_decimals))
                        except Exception:
                            pass

                self._stop_event.wait(timeout=1)
