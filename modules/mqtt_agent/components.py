import json
import os
import time
import socket


class ComponentSensorsMixin:
    def init_components(self):
        self.commands_components = self.config.get("components", {})
        self._comp_last_run = {}

        self._sampling_interval = float(self.config.get("device", {}).get("sampling_interval", 1.0))

        self._cpu_prev = {}

        self._cpu_baseline = {}
        self._cpu_sample_times = {}

        self._web_last_state = None

    def register_component_sensors(self):
        cfg = self.commands_components

        if cfg.get("cpu_load", {}).get("enabled", False):
            self._sensor_discovery(
                "cpu_load",
                "CPU load",
                f"{self.base_topic}/cpu_load",
                unit="%",
                icon="mdi:cpu-64-bit",
                state_class="measurement"
            )

            host_cpu_cfg = cfg.get("cpu_load", {}).get("host_cpu_load", {})
            if host_cpu_cfg.get("enabled", False):
                host_name = host_cpu_cfg.get("name") or "Host CPU Load"
                attrs_topic = f"{self.base_topic}/host_cpu_load_attrs"
                self._sensor_discovery(
                    "host_cpu_load",
                    host_name,
                    f"{self.base_topic}/host_cpu_load",
                    unit="%",
                    icon="mdi:cpu-64-bit",
                    state_class="measurement",
                    attributes_topic=attrs_topic
                )
                self.publish(attrs_topic, json.dumps({"hostname": self.config["device"]["name"]}))

        if cfg.get("memory_used", {}).get("enabled", False):
            self._sensor_discovery(
                "memory_used_percent",
                "Memory used percent",
                f"{self.base_topic}/memory_used_percent",
                unit="%",
                icon="mdi:memory",
                ha_object_id=f"{self.device_slug}_memory_used",
                state_class="measurement"
            )

        if cfg.get("iowait", {}).get("enabled", False):
            self._sensor_discovery(
                "iowait_pct",
                "iowait",
                f"{self.base_topic}/iowait",
                unit="%",
                icon="mdi:timer-sand",
                state_class="measurement"
            )
            self.publish(f"{self.base_topic}/iowait", 0)

        self._sensor_discovery(
            "update_status",
            "Update status",
            f"{self.base_topic}/update_status",
            icon="mdi:update",
            entity_category="diagnostic"
        )
        self.publish(f"{self.base_topic}/update_status", self._update_status, retain=True)

        self._sensor_discovery(
            "startup_time",
            "Startup time",
            f"{self.base_topic}/startup_time",
            icon="mdi:clock-start",
            entity_category="diagnostic"
        )
        self.publish(f"{self.base_topic}/startup_time", self._startup_time, retain=True)

        self._sensor_discovery(
            "mqtt_base_topic",
            "MQTT Base Topic",
            f"{self.base_topic}/mqtt_base_topic",
            icon="mdi:folder-network",
            entity_category="config"
        )
        self.publish(f"{self.base_topic}/mqtt_base_topic", self.base_topic, retain=True)

        if cfg.get("system_load", {}).get("enabled", True):
            self._sensor_discovery(
                "load_avg_1m",
                "Load average 1m",
                f"{self.base_topic}/load_avg_1m",
                icon="mdi:gauge",
                state_class="measurement"
            )
            self._sensor_discovery(
                "load_avg_5m",
                "Load average 5m",
                f"{self.base_topic}/load_avg_5m",
                icon="mdi:gauge",
                state_class="measurement"
            )
            self._sensor_discovery(
                "load_avg_15m",
                "Load average 15m",
                f"{self.base_topic}/load_avg_15m",
                icon="mdi:gauge",
                state_class="measurement"
            )

        web_cfg = cfg.get("web_check", {})
        if web_cfg.get("enabled", False):
            name = web_cfg.get("name") or "Internet"
            icon = web_cfg.get("icon") or "mdi:cloud-check"
            state_topic = f"{self.base_topic}/web_check"
            try:
                self._binary_sensor_discovery(
                    "web_check",
                    name,
                    state_topic,
                    device_class="connectivity",
                    icon=icon
                )
            except Exception:
                pass

    def _read_cpu_aggregate(self):
        with open("/proc/stat", "r", encoding="utf-8", errors="replace") as f:
            line = f.readline()

        parts = line.split()
        if not parts or parts[0] != "cpu":
            raise RuntimeError("Unexpected /proc/stat format")

        nums = [int(x) for x in parts[1:]]

        user = nums[0] if len(nums) > 0 else 0
        nice = nums[1] if len(nums) > 1 else 0
        system = nums[2] if len(nums) > 2 else 0
        idle = nums[3] if len(nums) > 3 else 0
        iowait = nums[4] if len(nums) > 4 else 0
        irq = nums[5] if len(nums) > 5 else 0
        softirq = nums[6] if len(nums) > 6 else 0
        steal = nums[7] if len(nums) > 7 else 0

        total = user + nice + system + idle + iowait + irq + softirq + steal
        return total, idle, iowait

    def _cpu_count(self):
        try:
            n = os.cpu_count()
            return int(n) if n and int(n) > 0 else 1
        except Exception:
            return 1

    def _calc_cpu_and_iowait_pct(self, prev, cur, include_iowait_as_idle=False):
        prev_total, prev_idle, prev_iow = prev
        cur_total, cur_idle, cur_iow = cur

        dt_total = cur_total - prev_total
        dt_idle = cur_idle - prev_idle
        dt_iow = cur_iow - prev_iow

        if dt_total <= 0:
            return None, None

        idle_all = dt_idle + dt_iow if include_iowait_as_idle else dt_idle
        busy = dt_total - idle_all

        usage = (busy / dt_total) * 100.0
        iowait_pct = (dt_iow / dt_total) * 100.0

        if usage < 0:
            usage = 0.0
        if usage > 100.0:
            usage = 100.0

        if iowait_pct < 0:
            iowait_pct = 0.0
        if iowait_pct > 100.0:
            iowait_pct = 100.0

        return usage, iowait_pct

    def component_sensors_loop(self):
        while not self._stop_event.is_set():
            now = time.monotonic()
            cfg = self.commands_components

            cpu_enabled = cfg.get("cpu_load", {}).get("enabled", False)
            io_enabled = cfg.get("iowait", {}).get("enabled", False)
            mem_enabled = cfg.get("memory_used", {}).get("enabled", False)

            web_enabled = cfg.get("web_check", {}).get("enabled", False)
            if web_enabled:
                web_cfg = cfg.get("web_check", {})
                host = web_cfg.get("host", "1.1.1.1")
                interval = web_cfg.get("update_interval", 3)
                try:
                    interval = float(interval)
                except Exception:
                    interval = 3.0

                last = self._comp_last_run.get("web_check", 0.0)
                if now - last >= interval:
                    self._comp_last_run["web_check"] = now
                    ok = False
                    try:
                        with socket.create_connection((host, 53), timeout=2):
                            ok = True
                    except Exception:
                        ok = False

                    payload = "ON" if ok else "OFF"
                    try:
                        self.publish(f"{self.base_topic}/web_check", payload, retain=True)
                    except Exception:
                        pass

            cpu_snapshot = None
            if cpu_enabled or io_enabled:
                try:
                    cpu_snapshot = self._read_cpu_aggregate()
                except Exception:
                    cpu_snapshot = None

            if cpu_enabled and cpu_snapshot is not None:
                cpu_cfg = cfg.get("cpu_load", {})
                interval = cpu_cfg.get("update_interval", 5)
                try:
                    interval = float(interval)
                except Exception:
                    interval = 5.0

                sample_interval = max(self._sampling_interval, interval / 10.0)
                last_sample = self._cpu_sample_times.get("cpu_load", 0.0)

                if now - last_sample >= sample_interval:
                    self._cpu_sample_times["cpu_load"] = now

                    include_iowait_as_idle = bool(cpu_cfg.get("include_iowait_as_idle", False))
                    scale = str(cpu_cfg.get("scale", "normalized")).strip().lower()
                    if scale not in ("normalized", "scaled"):
                        scale = "normalized"

                    decimals = int(cpu_cfg.get("accuracy_decimals", 2))
                    publish_time = self._comp_last_run.get("cpu_load", 0.0)

                    if "cpu_load" not in self._cpu_baseline:
                        self._cpu_baseline["cpu_load"] = cpu_snapshot

                    if now - publish_time >= interval:
                        self._comp_last_run["cpu_load"] = now
                        baseline = self._cpu_baseline.get("cpu_load")

                        if baseline is not None:
                            usage_norm, _ = self._calc_cpu_and_iowait_pct(baseline, cpu_snapshot, include_iowait_as_idle)

                            if usage_norm is not None:
                                if scale == "scaled":
                                    usage_out = round(usage_norm * float(self._cpu_count()), decimals)
                                else:
                                    usage_out = round(usage_norm, decimals)
                                try:
                                    self.publish(f"{self.base_topic}/cpu_load", usage_out)
                                except Exception:
                                    pass

                                host_cpu_cfg = cpu_cfg.get("host_cpu_load", {})
                                if host_cpu_cfg.get("enabled", False):
                                    host_cores = max(1, int(host_cpu_cfg.get("host_cores", 1)))
                                    vm_cores = self._cpu_count()
                                    host_load = max(0.0, min(100.0, usage_norm * vm_cores / host_cores))
                                    try:
                                        self.publish(f"{self.base_topic}/host_cpu_load", round(host_load, decimals))
                                    except Exception:
                                        pass

                        self._cpu_baseline["cpu_load"] = cpu_snapshot

            if io_enabled and cpu_snapshot is not None:
                io_cfg = cfg.get("iowait", {})
                interval = io_cfg.get("update_interval", 5)
                try:
                    interval = float(interval)
                except Exception:
                    interval = 5.0

                sample_interval = max(self._sampling_interval, interval / 10.0)
                last_sample = self._cpu_sample_times.get("iowait", 0.0)

                if now - last_sample >= sample_interval:
                    self._cpu_sample_times["iowait"] = now

                    decimals = int(io_cfg.get("accuracy_decimals", 2))
                    publish_time = self._comp_last_run.get("iowait", 0.0)

                    if "iowait" not in self._cpu_baseline:
                        self._cpu_baseline["iowait"] = cpu_snapshot

                    if now - publish_time >= interval:
                        self._comp_last_run["iowait"] = now
                        baseline = self._cpu_baseline.get("iowait")

                        if baseline is not None:
                            _, iowait_pct = self._calc_cpu_and_iowait_pct(baseline, cpu_snapshot, include_iowait_as_idle=False)

                            if iowait_pct is not None:
                                try:
                                    self.publish(f"{self.base_topic}/iowait", round(iowait_pct, decimals))
                                except Exception:
                                    pass

                        self._cpu_baseline["iowait"] = cpu_snapshot

            if cfg.get("system_load", {}).get("enabled", True):
                load_cfg = cfg.get("system_load", {})
                interval = load_cfg.get("update_interval", 60)
                try:
                    interval = float(interval)
                except Exception:
                    interval = 60.0

                last = self._comp_last_run.get("system_load", 0.0)
                if now - last >= interval:
                    self._comp_last_run["system_load"] = now
                    try:
                        load1, load5, load15 = os.getloadavg()
                        self.publish(f"{self.base_topic}/load_avg_1m", round(load1, 2))
                        self.publish(f"{self.base_topic}/load_avg_5m", round(load5, 2))
                        self.publish(f"{self.base_topic}/load_avg_15m", round(load15, 2))
                    except Exception:
                        pass

            if mem_enabled:
                mem_cfg = cfg.get("memory_used", {})
                interval = mem_cfg.get("update_interval", 10)
                try:
                    interval = float(interval)
                except Exception:
                    interval = 10.0

                decimals = int(mem_cfg.get("accuracy_decimals", 2))
                last = self._comp_last_run.get("memory_used", 0.0)
                if now - last >= interval:
                    self._comp_last_run["memory_used"] = now

                    mem = {}
                    try:
                        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as f:
                            for line in f:
                                if ":" not in line:
                                    continue
                                k, v = line.split(":", 1)
                                mem[k] = int(v.strip().split()[0])

                        total = mem["MemTotal"]
                        avail = mem["MemAvailable"]
                        used_pct = round((total - avail) / total * 100.0, decimals)

                        self.publish(f"{self.base_topic}/memory_used_percent", used_pct)
                    except Exception:
                        pass

            self._stop_event.wait(timeout=0.5)
