import json
import threading
from contextlib import contextmanager


class SystemStatusMixin:
    def init_system_status(self):
        self._error_active = False
        self._error_reason = ""
        self._startup_error = ""
        self._standing_errors = {}
        self._warning_active = False
        self._warning_reason = ""
        self._standing_warnings = {}
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
            "system_warning",
            "Warning",
            f"{self.base_topic}/warning",
            icon="mdi:alert",
            attributes_topic=f"{self.base_topic}/warning_attributes",
            entity_category="diagnostic",
        )
        self._sensor_discovery(
            "system_warning_reason",
            "Warning Reason",
            f"{self.base_topic}/warning_reason",
            icon="mdi:alert-outline",
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

        standing = self._standing_error()
        if not self._error_active or self._error_reason == standing:
            self.set_error(bool(standing), standing)
        self.set_warning(self._warning_active, self._warning_reason)
        self.publish(f"{self.base_topic}/busy", "ON" if self._busy_count > 0 else "OFF")
        self._publish_busy_job()

    def set_warning(self, active, reason=""):
        if not active:
            reason = self._standing_warning()
            active = bool(reason)
        self._warning_active = bool(active)
        self._warning_reason = reason if active else ""
        self.publish(f"{self.base_topic}/warning", "ON" if self._warning_active else "OFF")
        self.publish(
            f"{self.base_topic}/warning_attributes",
            json.dumps({"reason": self._warning_reason}),
        )
        self.publish(f"{self.base_topic}/warning_reason", self._warning_reason)

    def set_error(self, active, reason=""):
        self._error_active = bool(active)
        self._error_reason = reason if active else ""
        self.publish(f"{self.base_topic}/error", "ON" if self._error_active else "OFF")
        self.publish(
            f"{self.base_topic}/error_attributes",
            json.dumps({"reason": self._error_reason}),
        )
        self.publish(f"{self.base_topic}/error_reason", self._error_reason)

    def _standing_error(self):
        reasons = [self._startup_error] + list(self._standing_errors.values())
        return "; ".join(r for r in reasons if r)

    def set_standing_error(self, key, reason):
        if self._standing_errors.get(key, "") == reason:
            return
        before = self._standing_error()
        if reason:
            self._standing_errors[key] = reason
        else:
            self._standing_errors.pop(key, None)
        if not self._error_active or self._error_reason == before:
            standing = self._standing_error()
            self.set_error(bool(standing), standing)

    def _standing_warning(self):
        return "; ".join(r for r in self._standing_warnings.values() if r)

    def set_standing_warning(self, key, reason):
        if self._standing_warnings.get(key, "") == reason:
            return
        before = self._standing_warning()
        if reason:
            self._standing_warnings[key] = reason
        else:
            self._standing_warnings.pop(key, None)
        if not self._warning_active or self._warning_reason == before:
            standing = self._standing_warning()
            self.set_warning(bool(standing), standing)

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
