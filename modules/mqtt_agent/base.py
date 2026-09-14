import json
import re
import sys
import subprocess
import threading
import time
import paho.mqtt.client as mqtt


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "_", str(s).lower())
    return s.strip("_")


def run_cmd(cmd: str, env=None):
    if not cmd or cmd.strip() == "":
        return ""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERR: {e}"


class HAMQTTBase:
    def __init__(self, config, version, log_file=None, log_level="all"):
        self.config = config
        self.version = version
        self.log_file = log_file
        self.client = mqtt.Client()
        self.base_topic = f"TuxD{config['device']['name']}"
        self.device_slug = slugify(config["device"]["name"])

        self.tty_output = config["device"].get("tty_output", False)
        self.refresh_interval = config["device"].get("refresh_entities", 300)

        self.state_cache = {}

        self.device_info = {
            "identifiers": [config["device"]["name"]],
            "name": config["device"]["name"],
            "manufacturer": "Henrik Isefjær Olsen",
            "model": f"TuxD Linux Agent Version {self.version}",
            "sw_version": self.version
        }

        self._use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._CLR_GRAY = "\033[90m"
        self._CLR_RESET = "\033[0m"

        self._interactive = self._use_color

        self._state_topic_to_name = {}
        self._known_discovery_topics = set()
        self._stop_event = threading.Event()
        self._broker_lost = False

    def _gray(self, text):
        if not self._use_color:
            return str(text)
        return f"{self._CLR_GRAY}{text}{self._CLR_RESET}"

    def _on_disconnect(self, _client, _userdata, rc, *_args):
        if rc != 0 and not self._stop_event.is_set():
            self._broker_lost = True
            if self.tty_output:
                print(self._gray(f"MQTT: broker connection lost (rc={rc})"))
            self._stop_event.set()

    def connect(self):
        mqtt_cfg = self.config["mqtt"]

        self.client.username_pw_set(
            mqtt_cfg.get("username"), mqtt_cfg.get("password")
        )

        original_on_message = self.on_message

        def wrapped_on_message(client, userdata, msg):
            topic = msg.topic
            payload = msg.payload.decode()
            self.state_cache[topic] = payload
            original_on_message(client, userdata, msg)

        self.client.on_message = wrapped_on_message
        self.client.on_disconnect = self._on_disconnect

        broker = mqtt_cfg["broker"]
        port = int(mqtt_cfg.get("port", 1883))
        self.client.connect(broker, port, 60)
        self.client.loop_start()

    def clear_discovery(self, timeout=2.0):
        for topic in list(self._known_discovery_topics):
            self.client.publish(topic, "", retain=True)

        if self._known_discovery_topics:
            time.sleep(0.5)

        device_name = self.config["device"]["name"]
        wildcard = f"homeassistant/+/{device_name}/+/config"

        found_topics = set()

        def _on_config(*args):
            msg = args[2]
            if msg.payload:
                found_topics.add(msg.topic)

        self.client.message_callback_add(wildcard, _on_config)
        self.client.subscribe(wildcard)
        time.sleep(timeout)
        self.client.unsubscribe(wildcard)
        self.client.message_callback_remove(wildcard)

        for topic in found_topics:
            self.client.publish(topic, "", retain=True)

        if found_topics:
            time.sleep(0.5)

    def publish(self, topic, payload, retain=True):
        if not isinstance(payload, str):
            payload = str(payload)

        if retain and topic.startswith("homeassistant/") and topic.endswith("/config"):
            self._known_discovery_topics.add(topic)

        self.state_cache[topic] = payload

        if self.tty_output:
            is_config = topic.endswith("/config") or "/config" in topic
            if is_config:
                try:
                    data = json.loads(payload)
                    name = data.get("name") or "unknown"
                    st = data.get("state_topic")
                    if st:
                        self._state_topic_to_name[st] = name
                except Exception:
                    name = "unknown"
            else:
                name = self._state_topic_to_name.get(topic) or topic.rsplit("/", 1)[-1]
                print(self._gray(f"Sent data: {name} = {payload}"))

        self.client.publish(topic, payload, retain=retain)

    def get_state(self, topic: str):
        return self.state_cache.get(topic, "")

    def _discovery_topic(self, domain, object_id):
        return f"homeassistant/{domain}/{self.config['device']['name']}/{object_id}/config"

    def _sensor_discovery(self, object_id, name, state_topic, unit=None, icon=None, attributes_topic=None, ha_object_id=None, state_class=None, entity_category=None, device_class=None):
        payload = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
        }
        if ha_object_id:
            payload["default_entity_id"] = f"sensor.{ha_object_id}"
        if state_class:
            payload["state_class"] = state_class
        if unit:
            payload["unit_of_measurement"] = unit
        if icon:
            payload["icon"] = icon
        if attributes_topic:
            payload["json_attributes_topic"] = attributes_topic
        if entity_category:
            payload["entity_category"] = entity_category
        if device_class:
            payload["device_class"] = device_class

        self.publish(
            self._discovery_topic("sensor", object_id),
            json.dumps(payload),
            retain=True
        )

    def _binary_sensor_discovery(self, object_id, name, state_topic, device_class=None, icon=None, attributes_topic=None, entity_category=None):
        payload = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
            "payload_on": "ON",
            "payload_off": "OFF",
        }
        if device_class:
            payload["device_class"] = device_class
        if icon:
            payload["icon"] = icon
        if attributes_topic:
            payload["json_attributes_topic"] = attributes_topic
        if entity_category:
            payload["entity_category"] = entity_category

        self.publish(
            self._discovery_topic("binary_sensor", object_id),
            json.dumps(payload),
            retain=True,
        )

    def _update_discovery(self, object_id, name, state_topic, command_topic=None, device_class=None, icon=None, entity_category=None, ha_object_id=None, payload_install="INSTALL"):
        payload = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
        }
        if command_topic:
            payload["command_topic"] = command_topic
            payload["payload_install"] = payload_install
        if device_class:
            payload["device_class"] = device_class
        if icon:
            payload["icon"] = icon
        if entity_category:
            payload["entity_category"] = entity_category
        if ha_object_id:
            payload["default_entity_id"] = f"update.{ha_object_id}"

        self.publish(
            self._discovery_topic("update", object_id),
            json.dumps(payload),
            retain=True,
        )

    def _text_discovery(self, object_id, name, command_topic, icon=None, state_topic=None, entity_category=None, ha_object_id=None):
        state_topic = state_topic or f"{self.base_topic}/{object_id}"

        payload = {
            "name": name,
            "command_topic": command_topic,
            "state_topic": state_topic,
            "cmd_t": command_topic,
            "stat_t": state_topic,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
        }
        if icon:
            payload["icon"] = icon
        if entity_category:
            payload["entity_category"] = entity_category
        if ha_object_id:
            payload["default_entity_id"] = f"text.{ha_object_id}"

        self.publish(
            self._discovery_topic("text", object_id),
            json.dumps(payload),
            retain=True
        )

    def _button_discovery(self, object_id, name, command_topic, icon=None, entity_category=None):
        payload = {
            "name": name,
            "command_topic": command_topic,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
        }
        if icon:
            payload["icon"] = icon
        if entity_category:
            payload["entity_category"] = entity_category

        self.publish(
            self._discovery_topic("button", object_id),
            json.dumps(payload),
            retain=True
        )