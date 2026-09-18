import time
from collections import deque
from modules.disks import read_diskstats, disk_space, smart_errors


class DisksMixin:
    def init_disks(self):
        self.disk_last = {}
        self.disk_rates = {}
        self.disk_spaces = {}

        self._sampling_interval = float(self.config.get("device", {}).get("sampling_interval", 1.0))

        self.disk_sample_buffers = {}

    def register_disks(self):
        for d in self.config.get("disk", []):
            name = d["name"]
            base = f"{self.base_topic}/disk_{name}"

            metrics = {
                "reads": "MB/s",
                "writes": "MB/s",
                "read_writes": "MB/s",
                "used_space_gb": "GB",
                "free_space_gb": "GB",
                "used_space": "%",
            }

            if d.get("smart_enabled", True):
                metrics["smart_errors"] = None

            sensor_names = {
                "used_space_gb": f"{name} Storage Used GB",
                "free_space_gb": f"{name} Storage Free GB",
                "used_space": f"{name} Storage Used %",
            }

            for key, unit in metrics.items():
                if key == "used_space":
                    ha_object_id = f"{self.device_slug}__{name}_storage_used"
                elif key == "used_space_gb":
                    ha_object_id = f"{self.device_slug}_{name}_storage_used_gb"
                else:
                    ha_object_id = None
                self._sensor_discovery(
                    f"disk_{name}_{key}",
                    sensor_names.get(key) or f"{name} {key.replace('_', ' ').title()}",
                    f"{base}/{key}",
                    unit=unit,
                    icon="mdi:harddisk",
                    state_class="measurement",
                    ha_object_id=ha_object_id,
                )

            dev = d["dev"]
            mount = d["mount"]
            smart_enabled = d.get("smart_enabled", True)
            decimals = int(d.get("accuracy_decimals", 2))
            try:
                space = disk_space(mount)
                self.publish(f"{base}/used_space_gb", round(space["used_gb"], decimals))
                self.publish(f"{base}/free_space_gb", round(space["free_gb"], decimals))
                self.publish(f"{base}/used_space", round(space["used_pct"], decimals))
                if smart_enabled:
                    self.publish(f"{base}/smart_errors", smart_errors(f"/dev/{dev}"))
            except Exception:
                pass

        disks = self.config.get("disk") or []

        if len(disks) < 2:
            return

        self._sensor_discovery(
            "storage_used_gb",
            "Storage Used GB",
            f"{self.base_topic}/storage_used_gb",
            unit="GB",
            icon="mdi:harddisk",
            state_class="measurement"
        )
        self._sensor_discovery(
            "storage_free_gb",
            "Storage Free GB",
            f"{self.base_topic}/storage_free_gb",
            unit="GB",
            icon="mdi:harddisk",
            state_class="measurement"
        )
        self._sensor_discovery(
            "storage_used_pct",
            "Storage Used %",
            f"{self.base_topic}/storage_used_pct",
            unit="%",
            icon="mdi:harddisk",
            state_class="measurement"
        )

        try:
            total_used = 0.0
            total_free = 0.0
            for d in disks:
                space = disk_space(d["mount"])
                total_used += space["used_gb"]
                total_free += space["free_gb"]
            total = total_used + total_free
            used_pct = round(total_used / total * 100, 2) if total > 0 else 0
            self.publish(f"{self.base_topic}/storage_used_gb", round(total_used, 2))
            self.publish(f"{self.base_topic}/storage_free_gb", round(total_free, 2))
            self.publish(f"{self.base_topic}/storage_used_pct", used_pct)
        except Exception:
            pass

        self._sensor_discovery(
            "i_o_reads",
            "I/O reads",
            f"{self.base_topic}/i_o_reads",
            unit="MB/s",
            icon="mdi:harddisk",
            state_class="measurement"
        )
        self._sensor_discovery(
            "i_o_writes",
            "I/O writes",
            f"{self.base_topic}/i_o_writes",
            unit="MB/s",
            icon="mdi:harddisk",
            state_class="measurement"
        )
        self._sensor_discovery(
            "i_o_rw",
            "I/O RW",
            f"{self.base_topic}/i_o_rw",
            unit="MB/s",
            icon="mdi:harddisk",
            state_class="measurement"
        )

    def disk_loop(self, disk_cfg):
        name = disk_cfg["name"]
        dev = disk_cfg["dev"]
        mount = disk_cfg["mount"]
        interval = disk_cfg.get("update_interval", 10)
        smart_enabled = disk_cfg.get("smart_enabled", True)
        decimals = int(disk_cfg.get("accuracy_decimals", 2))
        base = f"{self.base_topic}/disk_{name}"

        self.disk_sample_buffers[dev] = deque(maxlen=100)

        try:
            read_b, write_b = read_diskstats(dev)
            self.disk_last[dev] = (read_b, write_b, time.time())
        except Exception:
            pass

        sample_interval = max(self._sampling_interval, interval / 10.0)

        last_publish = time.time()
        last_sample = 0

        while not self._stop_event.is_set():
            now = time.time()

            if now - last_sample >= sample_interval:
                last_sample = now
                read_b, write_b = read_diskstats(dev)
                last = self.disk_last.get(dev)

                if last:
                    last_read, last_write, last_t = last
                    dt = max(now - last_t, 0.001)
                    reads = (read_b - last_read) / dt / 1_000_000
                    writes = (write_b - last_write) / dt / 1_000_000
                else:
                    reads = writes = 0.0

                self.disk_last[dev] = (read_b, write_b, now)

                self.disk_sample_buffers[dev].append((reads, writes))

                if now - last_publish >= interval:
                    last_publish = now

                    space = disk_space(mount)
                    self.disk_spaces[name] = space

                    if self.disk_sample_buffers[dev]:
                        avg_reads = sum(s[0] for s in self.disk_sample_buffers[dev]) / len(self.disk_sample_buffers[dev])
                        avg_writes = sum(s[1] for s in self.disk_sample_buffers[dev]) / len(self.disk_sample_buffers[dev])

                        self.disk_rates[dev] = (avg_reads, avg_writes)

                        total = avg_reads + avg_writes

                        self.publish(f"{base}/reads", round(avg_reads, decimals))
                        self.publish(f"{base}/writes", round(avg_writes, decimals))
                        self.publish(f"{base}/read_writes", round(total, decimals))

                        if smart_enabled:
                            smart = smart_errors(f"/dev/{dev}")
                            self.publish(f"{base}/smart_errors", smart)

                        self.disk_sample_buffers[dev].clear()
                    else:
                        self.publish(f"{base}/reads", 0.0)
                        self.publish(f"{base}/writes", 0.0)
                        self.publish(f"{base}/read_writes", 0.0)

                        if smart_enabled:
                            smart = smart_errors(f"/dev/{dev}")
                            self.publish(f"{base}/smart_errors", smart)

                    self.publish(f"{base}/used_space_gb", round(space["used_gb"], decimals))
                    self.publish(f"{base}/free_space_gb", round(space["free_gb"], decimals))
                    self.publish(f"{base}/used_space", round(space["used_pct"], decimals))

                    self.update_global_disk_totals()

            self._stop_event.wait(timeout=0.5)

    def update_global_disk_totals(self):
        total_read = 0.0
        total_write = 0.0

        for reads, writes in self.disk_rates.values():
            total_read += reads
            total_write += writes

        if len(self.disk_rates) >= 2:
            self.publish(f"{self.base_topic}/i_o_reads", round(total_read, 2))
            self.publish(f"{self.base_topic}/i_o_writes", round(total_write, 2))
            self.publish(f"{self.base_topic}/i_o_rw", round(total_read + total_write, 2))

        if len(self.disk_spaces) >= 2:
            total_used = sum(s["used_gb"] for s in self.disk_spaces.values())
            total_free = sum(s["free_gb"] for s in self.disk_spaces.values())
            total = total_used + total_free
            used_pct = round(total_used / total * 100, 2) if total > 0 else 0
            self.publish(f"{self.base_topic}/storage_used_gb", round(total_used, 2))
            self.publish(f"{self.base_topic}/storage_free_gb", round(total_free, 2))
            self.publish(f"{self.base_topic}/storage_used_pct", used_pct)
