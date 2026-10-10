import json
import os

import yaml

from .config_agent import _CONFIG_FILE
from .shared import slugify as _slug

_LEVELS = (("off", "Off"), ("warning", "Warning"), ("error", "Error"))


class StackAlertMixin:

    def init_stack_alerts(self):
        self._stack_alert_labels = {level: self.tr(label) for level, label in _LEVELS}
        self._stack_alert_selects = {}
        self._stack_alert_health = {}

    def _stack_alert_cfg(self, source):
        cfg = (self.config.get(source) or {}).get("stack_alerts")
        return cfg if isinstance(cfg, dict) else {}

    @staticmethod
    def _stack_alert_norm(value):
        if value is True:
            return "warning"
        level = str(value or "").strip().lower()
        return level if level in ("warning", "error") else "off"

    def _stack_alert_level(self, source, name):
        cfg = self._stack_alert_cfg(source)
        stacks = cfg.get("stacks")
        if isinstance(stacks, dict) and name in stacks:
            return self._stack_alert_norm(stacks[name])
        return self._stack_alert_norm(cfg.get("default", "warning"))

    def forget_stack_alerts(self, source):
        self._stack_alert_selects.pop(source, None)

    def sync_stack_alerts(self, source, stacks):
        if not self._stack_alert_cfg(source).get("enabled", False):
            stacks = {}
        wanted = {_slug(name): name for name in stacks}
        known = self._stack_alert_selects.get(source, {})
        for slug in set(known) - set(wanted):
            self.publish(self._discovery_topic("select", f"cfgselect_{source}_{slug}_alert"), "", retain=True)
        for slug, name in wanted.items():
            if slug not in known:
                self._register_stack_alert(source, slug, name)
        self._stack_alert_selects[source] = wanted
        self._stack_alert_health[source] = dict(stacks)
        self._apply_stack_alerts(source)

    def _register_stack_alert(self, source, slug, name):
        oid = f"cfgselect_{source}_{slug}_alert"
        state_topic = f"{self.base_topic}/stack_alert/{source}/{slug}"
        self.publish(self._discovery_topic("select", oid), json.dumps({
            "name": f"{name} {self.tr('Health Alert')}",
            "state_topic": state_topic,
            "command_topic": f"{state_topic}/set",
            "options": list(self._stack_alert_labels.values()),
            "unique_id": f"{self.config['device']['name']}_{oid}",
            "device": self.device_info,
            "icon": "mdi:bell-alert-outline",
            "entity_category": "config",
            "default_entity_id": f"select.{self.device_slug}_config_{source}_{slug}_alert",
        }), retain=True)
        self.publish(state_topic, self._stack_alert_labels[self._stack_alert_level(source, name)], retain=True)

    def _apply_stack_alerts(self, source):
        health = self._stack_alert_health.get(source, {})
        raised = {"warning": [], "error": []}
        for name in sorted(health):
            level = self._stack_alert_level(source, name)
            if health[name] and level in raised:
                raised[level].append(name)

        def reason(names):
            return f"{self.tr('Unhealthy stacks')}: {', '.join(names)}"[:250] if names else ""

        self.set_standing_error(f"{source}_stacks", reason(raised["error"]))
        self.set_standing_warning(f"{source}_stacks", reason(raised["warning"]))

    @staticmethod
    def _stack_alert_store(cfg, source, name, level):
        node = cfg
        for key in (source, "stack_alerts", "stacks"):
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        node[name] = level

    def handle_stack_alert_select(self, topic, payload):
        parts = topic.split("/")
        if len(parts) < 4:
            return
        source, slug = parts[-3], parts[-2]
        name = self._stack_alert_selects.get(source, {}).get(slug)
        level = next((lv for lv, label in self._stack_alert_labels.items() if label == payload.strip()), None)
        if name is None or level is None:
            return

        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            self._stack_alert_store(cfg, source, name, level)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            self._config_own_write_mtime = os.path.getmtime(_CONFIG_FILE)
        except Exception:
            return

        self._stack_alert_store(self.config, source, name, level)
        self.publish(f"{self.base_topic}/stack_alert/{source}/{slug}", self._stack_alert_labels[level], retain=True)
        self._apply_stack_alerts(source)
