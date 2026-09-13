import time
import subprocess


def _ping_ms(host):
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "time=" in line:
                for part in line.split():
                    if part.startswith("time="):
                        return float(part[5:])
        return None
    except Exception:
        return None


class LagMonitorMixin:
    def init_lag_monitor(self):
        pass

    def register_lag_monitor(self):
        cfg = self.config.get("lag-monitor", {})
        if not cfg.get("enabled", False):
            return

        base = f"{self.base_topic}/lag_monitor"

        sensor_name = cfg.get("latency_sensor_name", "Latency")
        sensor_mode = cfg.get("latency_sensor_mode", "average")

        if sensor_mode == "both":
            self._sensor_discovery(
                object_id="lag_monitor_latency_max",
                name=f"{sensor_name} Max",
                state_topic=f"{base}/latency_ms_max",
                unit="ms",
                icon="mdi:lan-pending",
                state_class="measurement",
                entity_category="diagnostic",
            )
            self._sensor_discovery(
                object_id="lag_monitor_latency_avg",
                name=f"{sensor_name} Average",
                state_topic=f"{base}/latency_ms_avg",
                unit="ms",
                icon="mdi:lan-pending",
                state_class="measurement",
                entity_category="diagnostic",
            )
        else:
            self._sensor_discovery(
                object_id="lag_monitor_latency",
                name=sensor_name,
                state_topic=f"{base}/latency_ms",
                unit="ms",
                icon="mdi:lan-pending",
                state_class="measurement",
                entity_category="diagnostic",
            )

        self._binary_sensor_discovery(
            object_id="lag_monitor_high_latency",
            name=cfg.get("high_latency_sensor_name", "High Latency"),
            state_topic=f"{base}/high_latency",
            device_class="problem",
            entity_category="diagnostic",
        )

    def lag_monitor_loop(self):
        cfg = self.config.get("lag-monitor", {})
        host = cfg.get("host", "1.1.1.1")
        interval = float(cfg.get("update_interval", 3))
        threshold = float(cfg.get("high_latency_threshold", 10))
        sensor_interval = float(cfg.get("latency_sensor_interval", 30))
        sensor_mode = cfg.get("latency_sensor_mode", "average")
        decimals = int(cfg.get("accuracy_decimals", 1))
        base = f"{self.base_topic}/lag_monitor"

        samples = []
        last_sensor_publish = time.time()

        while not self._stop_event.is_set():
            latency = _ping_ms(host)

            if latency is not None:
                samples.append(latency)

            now = time.time()
            if now - last_sensor_publish >= sensor_interval:
                if samples:
                    peak = max(samples)
                    avg = sum(samples) / len(samples)
                    compare = peak if sensor_mode == "max" else avg

                    if sensor_mode == "both":
                        self.publish(f"{base}/latency_ms_max", round(peak, decimals))
                        self.publish(f"{base}/latency_ms_avg", round(avg, decimals))
                    elif sensor_mode == "max":
                        self.publish(f"{base}/latency_ms", round(peak, decimals))
                    else:
                        self.publish(f"{base}/latency_ms", round(avg, decimals))

                    self.publish(f"{base}/high_latency", "ON" if compare > threshold else "OFF")
                    samples.clear()
                last_sensor_publish = now

            self._stop_event.wait(timeout=interval)
