import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")
from modules import config_sync as cs


class RewriteTest(unittest.TestCase):
    def test_applied_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tuxd.conf")
            cfg = {"tugboat": {"update_interval": 30, "stack_alerts": {"enabled": False}},
                   "docker": {"stack_alerts": {"enabled": False}}}
            self.assertTrue(cs._apply_rewrites(cfg, path))
            self.assertEqual(cfg["tugboat"]["update_interval"], 10)
            self.assertTrue(cfg["tugboat"]["stack_alerts"]["enabled"] and cfg["docker"]["stack_alerts"]["enabled"])
            cfg["tugboat"]["update_interval"] = 60
            self.assertFalse(cs._apply_rewrites(cfg, path))
            self.assertEqual(cfg["tugboat"]["update_interval"], 60)

    def test_samples_hash_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tuxd.conf")
            cs._write_samples_hash(path, "abc")
            cs._apply_rewrites({}, path)
            self.assertEqual(cs._read_samples_hash(path), "abc")


if __name__ == "__main__":
    unittest.main()
