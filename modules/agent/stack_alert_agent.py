import json

from .shared import slugify as _slug

_LEVELS = (("off", "Off"), ("warning", "Warning"), ("error", "Error"))

_SOURCES = {
    "tugboat": ("stack_alerts", "stacks", "Unhealthy stacks", "Health Alert"),
    "docker": ("stack_alerts", "stacks", "Unhealthy stacks", "Health Alert"),
    "systemd": ("service_alerts", "services", "Failed services", "Alert"),
}
_DEFAULT_SOURCE = _SOURCES["tugboat"]


class StackAlertMixin:

    def init_stack_alerts(self):
        self._stack_alert_labels = {level: self.tr(label) for level, label in _LEVELS}
        self._stack_alert_selects = {}
        self._stack_alert_health = {}

    @staticmethod
    def _alert_profile(source):
        return _SOURCES.get(source, _DEFAULT_SOURCE)

    def _stack_alert_cfg(self, source):
        cfg = (self.config.get(source) or {}).get(self._alert_profile(source)[0])
        return cfg if isinstance(cfg, dict) else {}

    @staticmethod
    def _stack_alert_norm(value):
        if value is True:
            return "warning"
        level = str(value or "").strip().lower()
        return level if level in ("warning", "error") else "off"

    def _stack_alert_level(self, source, name):
        cfg = self._stack_alert_cfg(source)
        stacks = cfg.get(self._alert_profile(source)[1])
        if isinstance(stacks, dict) and name in stacks:
            return self._stack_alert_norm(stacks[name])
        return self._stack_alert_norm(cfg.get("default", "warning"))

    def forget_stack_alerts(self, source):
        self._stack_alert_selects.pop(source, None)

    def sync_stack_alerts(self, source, stacks):
        selects = bool(self._stack_alert_cfg(source).get("enabled", True))
        wanted = {_slug(name): name for name in stacks} if selects else {}
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
            "name": f"{name} {self.tr(self._alert_profile(source)[3])}",
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

        _block, items, label, _suffix = self._alert_profile(source)

        def reason(names):
            return f"{self.tr(label)}: {', '.join(names)}"[:250] if names else ""

        self.set_standing_error(f"{source}_{items}", reason(raised["error"]))
        self.set_standing_warning(f"{source}_{items}", reason(raised["warning"]))

    @classmethod
    def _stack_alert_store(cls, cfg, source, name, level):
        block, items, _label, _suffix = cls._alert_profile(source)
        node = cfg
        for key in (source, block, items):
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
            self.edit_config(lambda cfg: self._stack_alert_store(cfg, source, name, level))
        except Exception:
            return

        self._stack_alert_store(self.config, source, name, level)
        self.publish(f"{self.base_topic}/stack_alert/{source}/{slug}", self._stack_alert_labels[level], retain=True)
        self._apply_stack_alerts(source)
