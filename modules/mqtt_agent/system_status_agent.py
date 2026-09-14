import json


class SystemStatusMixin:
    def init_system_status(self):
        self._error_active = False
        self._error_reason = ""

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

        if not self._error_active:
            self.set_error(False)

    def set_error(self, active, reason=""):
        self._error_active = bool(active)
        self._error_reason = reason if active else ""
        self.publish(f"{self.base_topic}/error", "ON" if self._error_active else "OFF")
        self.publish(
            f"{self.base_topic}/error_attributes",
            json.dumps({"reason": self._error_reason}),
        )
