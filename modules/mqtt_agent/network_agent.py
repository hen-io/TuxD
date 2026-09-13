import time
from collections import deque
from modules.network import NetworkMonitor


class NetworkMixin:
    def init_network(self):
        self.net_monitors = {}
        self.net_samples = {}

        self._sampling_interval = float(self.config.get("device", {}).get("sampling_interval", 1.0))

        self.net_sample_buffers = {}

        self._ha_metric_names = {
            "rx_mbps": "in",
            "tx_mbps": "out",
            "combined_mbps": "in/out",
        }

        self._ha_total_names = {
            "network_in_mbps": "Network in",
            "network_out_mbps": "Network out",
            "network_combined_mbps": "Network in/out",
        }

    def _ha_iface_friendly_name(self, iface, key):
        metric = self._ha_metric_names.get(key, key)
        return f"{iface} {metric}"

    def register_network(self):
        for n in self.config.get("network", []):
            iface = n["iface"]
            base = f"{self.base_topic}/network_{iface}"

            metrics = {
                "rx_mbps": "Mbit/s",
                "tx_mbps": "Mbit/s",
                "combined_mbps": "Mbit/s",
            }

            for key, unit in metrics.items():
                self._sensor_discovery(
                    f"net_{iface}_{key}",
                    self._ha_iface_friendly_name(iface, key),
                    f"{base}/{key}",
                    unit=unit,
                    icon="mdi:lan",
                    state_class="measurement"
                )

        if len(self.config.get("network") or []) < 2:
            return

        self._sensor_discovery(
            "network_in_mbps",
            self._ha_total_names["network_in_mbps"],
            f"{self.base_topic}/network_in_mbps",
            unit="Mbit/s",
            icon="mdi:download-network",
            state_class="measurement"
        )
        self._sensor_discovery(
            "network_out_mbps",
            self._ha_total_names["network_out_mbps"],
            f"{self.base_topic}/network_out_mbps",
            unit="Mbit/s",
            icon="mdi:upload-network",
            state_class="measurement"
        )
        self._sensor_discovery(
            "network_in_out",
            self._ha_total_names["network_combined_mbps"],
            f"{self.base_topic}/network_in_out",
            unit="Mbit/s",
            icon="mdi:lan",
            state_class="measurement"
        )

    def network_loop(self, net_cfg):
        iface = net_cfg["iface"]
        interval = net_cfg.get("update_interval", 5)
        decimals = int(net_cfg.get("accuracy_decimals", 2))
        mon = NetworkMonitor(iface)
        base = f"{self.base_topic}/network_{iface}"

        self.net_sample_buffers[iface] = deque(maxlen=100)

        self.net_monitors[iface] = mon

        mon.sample()

        sample_interval = max(self._sampling_interval, interval / 10.0)

        last_publish = time.time()
        last_sample = 0

        while not self._stop_event.is_set():
            now = time.time()

            if now - last_sample >= sample_interval:
                last_sample = now
                data = mon.sample()

                self.net_sample_buffers[iface].append((data["rx_mbps"], data["tx_mbps"]))

                if now - last_publish >= interval:
                    last_publish = now

                    if self.net_sample_buffers[iface]:
                        avg_rx = sum(s[0] for s in self.net_sample_buffers[iface]) / len(self.net_sample_buffers[iface])
                        avg_tx = sum(s[1] for s in self.net_sample_buffers[iface]) / len(self.net_sample_buffers[iface])
                        avg_combined = avg_rx + avg_tx

                        self.net_samples[iface] = {
                            "rx_mbps": round(avg_rx, decimals),
                            "tx_mbps": round(avg_tx, decimals),
                            "combined_mbps": round(avg_combined, decimals)
                        }

                        self.publish(f"{base}/rx_mbps", round(avg_rx, decimals))
                        self.publish(f"{base}/tx_mbps", round(avg_tx, decimals))
                        self.publish(f"{base}/combined_mbps", round(avg_combined, decimals))

                        self.net_sample_buffers[iface].clear()

                    self.update_global_network_totals()

            self._stop_event.wait(timeout=0.5)

    def update_global_network_totals(self):
        total_rx = 0.0
        total_tx = 0.0

        for sample in self.net_samples.values():
            total_rx += sample["rx_mbps"]
            total_tx += sample["tx_mbps"]

        self.publish(f"{self.base_topic}/network_in_mbps", round(total_rx, 2))
        self.publish(f"{self.base_topic}/network_out_mbps", round(total_tx, 2))
        self.publish(f"{self.base_topic}/network_in_out", round(total_rx + total_tx, 2))