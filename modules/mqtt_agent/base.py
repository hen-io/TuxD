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


_ENTITY_COMPONENT_PREFIXES = (
    "builtin_", "custom_button_", "custom_sensor_", "disk_", "docker_",
    "lag_monitor_", "log_sensor_", "net_", "select_",
    "self_update_", "status_", "system_", "terminal_",
)


def entity_object_id(object_id):
    object_id = str(object_id)
    for prefix in _ENTITY_COMPONENT_PREFIXES:
        if object_id.startswith(prefix):
            object_id = object_id[len(prefix):]
            break
    return re.sub(
        r"_(?:bytes?|gb|kb|mb|mbps|pct|percent|ms|seconds?|minutes?|hours?)$",
        "",
        object_id,
    )


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
        self.base_topic = f"tuxd/{config['device']['name']}"
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