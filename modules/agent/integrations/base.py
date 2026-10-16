import json


class Action:

    def __init__(self, action_id, name, icon="mdi:play", targets=None, target_name="", confirm=False):
        self.id = action_id
        self.name = name
        self.icon = icon
        self.targets = list(targets) if targets is not None else None
        self.target_name = target_name
        self.confirm = confirm

    def to_dict(self, integration):
        data = {
            "integration": integration.KEY,
            "integration_name": integration.tr(integration.NAME),
            "id": self.id,
            "name": integration.tr(self.name),
            "icon": self.icon,
            "confirm": bool(self.confirm),
        }
        if self.targets is not None:
            data["targets"] = self.targets
            data["target_name"] = integration.tr(self.target_name)
        return data


class Integration:
    KEY = ""
    NAME = ""
    DEFAULT_INTERVAL = 30
    ALWAYS_ON = False

    def __init__(self, agent):
        self.agent = agent

    @property
    def cfg(self):
        value = self.agent.config.get(self.KEY)
        return value if isinstance(value, dict) else {}

    def enabled(self):
        return self.ALWAYS_ON or bool(self.cfg.get("enabled", False))

    def interval(self):
        try:
            return max(1.0, float(self.cfg.get("update_interval", self.DEFAULT_INTERVAL)))
        except (TypeError, ValueError):
            return float(self.DEFAULT_INTERVAL)

    def register(self):
        pass

    def poll(self):
        pass

    def has_poll(self):
        return type(self).poll is not Integration.poll

    def actions(self):
        return []

    def run(self, action_id, target):
        raise NotImplementedError

    def before_power(self, kind):
        pass

    def tr(self, text):
        return self.agent.tr(text)

    @property
    def base_topic(self):
        return f"{self.agent.base_topic}/{self.KEY}"

    def publish_changed(self, topic, payload):
        payload = payload if isinstance(payload, str) else json.dumps(payload)
        if getattr(self.agent, "state_cache", {}).get(topic) != payload:
            self.agent.publish(topic, payload)
