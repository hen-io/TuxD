import json
import sys
import unittest
from unittest import mock

sys.modules.setdefault("paho", mock.MagicMock())
sys.modules.setdefault("paho.mqtt", mock.MagicMock())
sys.modules.setdefault("paho.mqtt.client", mock.MagicMock())
sys.path.insert(0, ".")

from modules import tugboat as tb
from modules.agent.tugboat_agent import TugboatMixin
from modules.agent.i18n import translate

LISTING = (
    "\x1b[1mweb (2)\x1b[0m\n"
    "  05_10_26_14_30_00  \x1b[2m2026-10-05 14:30, 5d ago\x1b[0m\n"
    "  01_10_26_02_00_00  \x1b[2m2026-10-01 02:00, 9d ago\x1b[0m\n"
)


class Agent(TugboatMixin):
    def __init__(self, lang="en"):
        self.config = {"tugboat": {"enabled": True, "path": "/x"}, "device": {"name": "box"}}
        self.language, self.base_topic, self.device_slug, self.device_info = lang, "t", "box", {}
        self.pub, self.runs = {}, []
        self.tr = lambda text: translate(self.language, text)
        self.init_tugboat()
        self._tugboat_known_stacks = ["db", "web"]

    def publish(self, topic, payload, retain=True):
        self.pub[topic] = payload

    def _discovery_topic(self, domain, oid):
        return f"homeassistant/{domain}/box/{oid}/config"

    def _run_tugboat_action(self, stack, action, reset_selects=True, extra=()):
        self.runs.append((stack, action, extra))
        self._tugboat_running = False


class ThreadNow:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class TugboatTest(unittest.TestCase):
    def test_actions_and_command(self):
        self.assertEqual(tb.ACTIONS["Restart"], "--restart")
        self.assertEqual(tb.ACTIONS["Rollback"], "--rollback")
        cmd = tb.build_command("python3", "/x", "--rollback", "web", ("--to", "01_10_26_02_00_00"))
        self.assertTrue(cmd.endswith("--rollback --stack web --auto --to 01_10_26_02_00_00"))
        self.assertTrue(tb.build_command("python3", "/x", "--start", "web").endswith("--start --stack web --auto"))

    def test_list_backups_parses_names_newest_first(self):
        with mock.patch.object(tb.subprocess, "run", return_value=mock.Mock(stdout=LISTING)):
            self.assertEqual(tb.list_backups("python3", "/x", "web"), ["05_10_26_14_30_00", "01_10_26_02_00_00"])
        with mock.patch.object(tb.subprocess, "run", return_value=mock.Mock(stdout="web (0)\n  no backups\n")):
            self.assertEqual(tb.list_backups("python3", "/x", "web"), [])

    def _picked(self, a, stack, action):
        a._tugboat_selected_stack, a._tugboat_selected_action = stack, action

    def test_rollback_newest_and_chosen(self):
        a = Agent()
        a._tugboat_backups = ["05_10_26_14_30_00", "01_10_26_02_00_00"]
        with mock.patch("modules.agent.tugboat_agent.threading.Thread", ThreadNow):
            self._picked(a, "web", "Rollback"); a.handle_tugboat_execute()
            a.handle_tugboat_backup_select("01_10_26_02_00_00"); self._picked(a, "web", "Rollback"); a.handle_tugboat_execute()
        self.assertEqual(a.runs, [("web", "Rollback", ()), ("web", "Rollback", ("--to", "01_10_26_02_00_00"))])

    def test_rollback_needs_one_real_stack(self):
        a = Agent()
        with mock.patch("modules.agent.tugboat_agent.threading.Thread", ThreadNow):
            self._picked(a, a._tugboat_all_stacks, "Rollback"); a.handle_tugboat_execute()
        self.assertEqual(a.runs, [])

    def test_backup_select_follows_stack_and_tells_cards_when(self):
        a = Agent()
        with mock.patch("modules.agent.tugboat_agent.threading.Thread", ThreadNow), \
                mock.patch("modules.agent.tugboat_agent.list_backups", return_value=["05_10_26_14_30_00"]):
            a.handle_tugboat_stack_select("web")
        cfg = json.loads(a.pub["homeassistant/select/box/tugboat_select_backup/config"])
        self.assertEqual(cfg["options"], ["Newest backup", "05_10_26_14_30_00"])
        self.assertEqual(cfg["default_entity_id"], "select.box_tugboat_backup")
        self.assertEqual(json.loads(a.pub["t/tugboat/select_backup/attributes"]), {"for_action": "Rollback"})
        a.handle_tugboat_backup_select("nonsense")
        self.assertEqual(a._tugboat_selected_backup, "Newest backup")

    def test_status_file_location(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            new, old = os.path.join(d, "TugBoat", "tugboat.json"), os.path.join(d, "tugboat.json")
            self.assertEqual(tb.status_file_path(d, {}), new)
            open(old, "w").write("{}")
            self.assertEqual(tb.status_file_path(d, {}), old)
            os.makedirs(os.path.dirname(new)); open(new, "w").write("{}")
            self.assertEqual(tb.status_file_path(d, {}), new)
            self.assertEqual(tb.status_file_path(d, {"status_file": "./x/s.json"}), os.path.join(d, "x", "s.json"))

    def test_stack_icon_becomes_entity_picture(self):
        url = "https://cdn.example/svg/bazarr.svg"
        info = {"icon_url": "javascript:x", "images": [{"icon_url": url}]}
        self.assertEqual(TugboatMixin._tugboat_icon_url(info), url)
        self.assertEqual(TugboatMixin._tugboat_icon_url({"icon_url": "https://a/b.svg", "images": [{"icon_url": url}]}), "https://a/b.svg")
        self.assertEqual(TugboatMixin._tugboat_icon_url({}), "")
        images = [{"image": "r/x:1", "status": "update_available", "local_version": "1", "remote_version": "2", "services": ["x"]}]
        self.assertEqual(TugboatMixin._tugboat_stack_state("web", images, url)["entity_picture"], url)
        self.assertNotIn("entity_picture", TugboatMixin._tugboat_stack_state("web", images))

    def test_norwegian_labels(self):
        a = Agent("nb")
        self.assertEqual(a._rollback_label(), "Tilbakerull")
        self.assertEqual(a._tugboat_backup_default, "Nyeste sikkerhetskopi")


if __name__ == "__main__":
    unittest.main()
