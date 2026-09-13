import time
from modules.lm_sensors import read_lm_sensors


class LMSensorsMixin:
    def register_lm_sensors(self):
        lm_cfg = (self.config or {}).get("lm_sensors", {}) or {}
        if not lm_cfg.get("enabled", False):
            return

        sensors_cfg = lm_cfg.get("sensors", {}) or {}
        overrides = lm_cfg.get("overrides", {}) or {}
        default_icon = lm_cfg.get("default_icon", "mdi:thermometer")
        default_unit = lm_cfg.get("default_unit_of_measurement")

        if sensors_cfg:
            avg_keys = []
            for raw_key, sc in sensors_cfg.items():
                if not isinstance(sc, dict):
                    sc = {}

                if not sc.get("enabled", True):
                    continue

                name = sc.get("name") or overrides.get(raw_key) or raw_key
                icon = sc.get("icon") or default_icon
                unit = sc.get("unit_of_measurement", default_unit)

                object_id = f"lm_{raw_key}"
                state_topic = f"{self.base_topic}/lm_{raw_key}"

                self._sensor_discovery(
                    object_id,
                    name,
                    state_topic,
                    unit=unit,
                    icon=icon,
                    state_class="measurement",
                    device_class=sc.get("device_class"),
                )
                if sc.get("include_in_avg_sensor", False):
                    avg_keys.append(raw_key)

            if avg_keys:
                avg_name = lm_cfg.get("avg_sensor_name") or "Average temperature"
                avg_icon = lm_cfg.get("avg_sensor_icon") or default_icon
                avg_unit = default_unit or "°C"
                self._sensor_discovery(
                    "lm_avg_temp",
                    avg_name,
                    f"{self.base_topic}/lm_avg_temp",
                    unit=avg_unit,
                    icon=avg_icon,
                    ha_object_id=f"{self.device_slug}_average_temp",
                    state_class="measurement",
                )
            return

    def lm_sensors_loop(self, interval, overrides):
        lm_cfg = (self.config or {}).get("lm_sensors", {}) or {}
        if not lm_cfg.get("enabled", False):
            return

        sensors_cfg = lm_cfg.get("sensors", {}) or {}
        overrides_cfg = lm_cfg.get("overrides", {}) or {}

        overrides = overrides if overrides is not None else overrides_cfg
        default_interval = int(lm_cfg.get("default_interval", 15))
        default_accuracy_decimals = int(lm_cfg.get("default_accuracy_decimals", 1))

        if sensors_cfg:
            schedule = {}
            last_sent = {}
            avg_keys = []

            for raw_key, sc in sensors_cfg.items():
                if not isinstance(sc, dict):
                    sc = {}

                if not sc.get("enabled", True):
                    continue

                try:
                    sensor_interval = int(sc.get("update_interval", default_interval))
                except Exception:
                    sensor_interval = default_interval

                schedule[raw_key] = sensor_interval
                last_sent[raw_key] = 0.0
                if sc.get("include_in_avg_sensor", False):
                    avg_keys.append(raw_key)

            if not schedule:
                return

            while not self._stop_event.is_set():
                now = time.time()

                due_keys = []
                for raw_key, sensor_interval in schedule.items():
                    last = last_sent.get(raw_key, 0.0)
                    if sensor_interval <= 0 or (now - last) >= sensor_interval:
                        due_keys.append(raw_key)

                if due_keys:
                    values = read_lm_sensors(include=due_keys)
                    any_avg_updated = False

                    for raw_key in due_keys:
                        if self._stop_event.is_set():
                            return
                        if raw_key in values:
                            sc = sensors_cfg.get(raw_key, {})
                            if not isinstance(sc, dict):
                                sc = {}
                            try:
                                decimals = int(sc.get("accuracy_decimals", default_accuracy_decimals))
                            except Exception:
                                decimals = default_accuracy_decimals
                            rounded_val = round(values[raw_key], decimals)
                            self.publish(f"{self.base_topic}/lm_{raw_key}", rounded_val)
                            last_sent[raw_key] = now
                            if raw_key in avg_keys:
                                any_avg_updated = True

                    if any_avg_updated and avg_keys:
                        try:
                            vals = read_lm_sensors(include=avg_keys)
                            nums = [v for v in vals.values() if isinstance(v, (int, float))]
                            if nums:
                                avg_decimals = lm_cfg.get("avg_accuracy_decimals")
                                if avg_decimals is None:
                                    avg_decimals = default_accuracy_decimals
                                else:
                                    try:
                                        avg_decimals = int(avg_decimals)
                                    except Exception:
                                        avg_decimals = default_accuracy_decimals
                                avg = round(sum(nums) / len(nums), avg_decimals)
                                self.publish(f"{self.base_topic}/lm_avg_temp", avg)
                        except Exception:
                            pass

                self._stop_event.wait(timeout=1)

        while not self._stop_event.is_set():
            values = read_lm_sensors(flat_overrides=overrides)
            for name, val in values.items():
                self.publish(f"{self.base_topic}/lm_{name}", val)
            self._stop_event.wait(timeout=interval if interval is not None else default_interval)