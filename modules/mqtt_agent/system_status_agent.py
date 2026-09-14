import json
import threading
from contextlib import contextmanager


class SystemStatusMixin:
    def init_system_status(self):
        self._error_active = False
        self._error_reason = ""
        self._busy_count = 0
        self._busy_lock = threading.Lock()
        self._busy_jobs = []

    def register_system_status(self):
        self._binary_sensor_discovery(
            "system_error",
            "Error",
            f"{self.base_topic}/error",
            device_class="problem",
            icon="mdi:alert-circle",
            attributes_topic=f"{self.base_topic}/error_attributes",
            entity_category="diagnostic",
        )
        self._sensor_discovery(
            "system_error_reason",
            "Error Reason",
            f"{self.base_topic}/error_reason",
            icon="mdi:alert-circle-outline",
            entity_category="diagnostic",
        )
        self._binary_sensor_discovery(
            "system_busy",
            "Busy",
            f"{self.base_topic}/busy",
            icon="mdi:progress-clock",
            entity_category="diagnostic",
        )
        self._sensor_discovery(
            "system_busy_job",
            "Current Job",
            f"{self.base_topic}/busy_job",
            icon="mdi:format-list-bulleted",
            entity_category="diagnostic",
        )

        if not self._error_active:
            self.set_error(False)
        self.publish(f"{self.base_topic}/busy", "ON" if self._busy_count > 0 else "OFF")
        self._publish_busy_job()

    def set_error(self, active, reason=""):
        self._error_active = bool(active)
        self._error_reason = reason if active else ""
        self.publish(f"{self.base_topic}/error", "ON" if self._error_active else "OFF")
        self.publish(
            f"{self.base_topic}/error_attributes",
            json.dumps({"reason": self._error_reason}),
        )
        self.publish(f"{self.base_topic}/error_reason", self._error_reason)

    def _publish_busy_job(self):
        if not self._busy_jobs:
            text = ""
        elif len(self._busy_jobs) == 1:
            text = self._busy_jobs[-1]
        else:
            text = f"{self._busy_jobs[-1]} (+{len(self._busy_jobs) - 1} more)"
        self.publish(f"{self.base_topic}/busy_job", text)

    @contextmanager
    def busy(self, description=""):
        job = description or "task"
        with self._busy_lock:
            self._busy_count += 1
            self._busy_jobs.append(job)
            if self._busy_count == 1:
                self.publish(f"{self.base_topic}/busy", "ON")
            self._publish_busy_job()
        try:
            yield
        finally:
            with self._busy_lock:
                try:
                    self._busy_jobs.remove(job)
                except ValueError:
                    pass
                self._busy_count = max(0, self._busy_count - 1)
                if self._busy_count == 0:
                    self.publish(f"{self.base_topic}/busy", "OFF")
                self._publish_busy_job()
