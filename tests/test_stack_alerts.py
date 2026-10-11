import os
import sys
import tempfile
import unittest
from unittest import mock

for m in ("paho", "paho.mqtt", "paho.mqtt.client"):
    sys.modules.setdefault(m, mock.MagicMock())
sys.path.insert(0, ".")
from modules.agent.stack_alert_agent import StackAlertMixin
from modules.agent.system_status_agent import SystemStatusMixin
from modules.agent.i18n import translate


class Agent(StackAlertMixin, SystemStatusMixin):
    def __init__(self, alerts=None):
        self.config = {"device": {"name": "box"}, "tugboat": {"stack_alerts": alerts} if alerts is not None else {}}
        self.base_topic, self.device_slug, self.device_info, self.language = "t", "box", {}, "en"
        self.pub = {}
        self.init_stack_alerts()
        self.init_system_status()

    def tr(self, text):
        return translate(self.language, text)

    def publish(self, topic, payload, retain=True):
        self.pub[topic] = payload

    def _discovery_topic(self, domain, oid):
        return f"homeassistant/{domain}/box/{oid}/config"

    def state(self):
        return (self.pub.get("t/warning"), self.pub.get("t/warning_reason"), self.pub.get("t/error"))


class StackAlertTest(unittest.TestCase):
    def test_warning_without_selects(self):
        a = Agent({"enabled": False})
        a.sync_stack_alerts("tugboat", {"web": True, "db": False})
        self.assertEqual(a.state()[:2], ("ON", "Unhealthy stacks: web"))
        self.assertFalse([t for t in a.pub if "/select/" in t])
        a.sync_stack_alerts("tugboat", {"web": False, "db": False})
        self.assertEqual(a.state()[0], "OFF")

    def test_off_and_error_levels(self):
        a = Agent({"default": "off"})
        a.sync_stack_alerts("tugboat", {"web": True})
        self.assertNotEqual(a.state()[0], "ON")
        a = Agent({"default": "error"})
        a.sync_stack_alerts("tugboat", {"web": True})
        self.assertEqual((a.state()[0] == "ON", a.state()[2]), (False, "ON"))

    def test_selects_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                a = Agent()
                a.sync_stack_alerts("tugboat", {"web": True})
            finally:
                os.chdir(cwd)
        self.assertIn("homeassistant/select/box/cfgselect_tugboat_web_alert/config", a.pub)
        self.assertEqual(a.state()[0], "ON")


if __name__ == "__main__":
    unittest.main()
