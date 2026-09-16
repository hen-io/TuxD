import asyncio
import json
import ssl
import sys
import threading
import time

from .base import slugify

try:
    import websockets
except ImportError:
    websockets = None


class _FakeMqttClient:
    def __init__(self, backend):
        self._backend = backend

    def subscribe(self, *_args, **_kwargs):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        self._backend._shutdown()


class _IncomingMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()


class HADirectBase:

    def __init__(self, config, version, log_file=None, log_level="all"):
        if websockets is None:
            raise RuntimeError(
                "connection_mode: direct requires the 'websockets' package - "
                "pip install websockets (or apt install python3-websockets)"
            )

        self.config = config
        self.version = version
        self.log_file = log_file
        self.client = _FakeMqttClient(self)

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
            "sw_version": self.version,
        }

        self._use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._CLR_GRAY = "\033[90m"
        self._CLR_RESET = "\033[0m"
        self._interactive = self._use_color

        self._state_topic_to_name = {}
        self._known_discovery_topics = set()
        self._stop_event = threading.Event()
        self._broker_lost = False

        self._loop = None
        self._ws = None
        self._thread = None
        self._connected_event = threading.Event()
        self._auth_error = None

    def _gray(self, text):
        if not self._use_color:
            return str(text)
        return f"{self._CLR_GRAY}{text}{self._CLR_RESET}"


    def connect(self):
        ha_cfg = self.config.get("home-assistant") or {}
        url = (ha_cfg.get("url") or "").strip()
        if not url:
            raise RuntimeError("connection_mode: direct requires home-assistant.url to be set")

        if url.startswith("http://") and not bool(ha_cfg.get("allow_insecure", False)):
            raise RuntimeError(
                "connection_mode: direct requires an https:// home-assistant.url - "
                "http:// would send the pairing key and every command in plaintext. "
                "Set home-assistant.allow_insecure: true only if you specifically "
                "intend to run without encryption (e.g. a fully isolated local network)."
            )

        self._ha_url = url
        self._api_key = ha_cfg.get("api_key", "")
        self._pairing_key = ha_cfg.get("pairing_key", "")
        self._auth_key = self._api_key or self._pairing_key
        self._verify_ssl = bool(ha_cfg.get("verify_ssl", True))
        self._retry_delay = float(ha_cfg.get("retry_delay", 15))

        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

        got_result = self._connected_event.wait(timeout=15)
        if self._auth_error:
            raise RuntimeError(f"Home Assistant rejected connection: {self._auth_error}")
        if not got_result and self.tty_output:
            print(self._gray("Direct: still trying to reach Home Assistant, continuing in background..."))

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as e:
            if self.tty_output:
                print(self._gray(f"Direct: connection thread ended: {e!r}"))

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        ws_url = _to_ws_url(self._ha_url) + "/api/tuxd/ws"
        ssl_ctx = None
        if ws_url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            if not self._verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(ws_url, ssl=ssl_ctx, open_timeout=10) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({
                        "type": "hello",
                        "auth": self._auth_key,
                        "device_id": self.config["device"]["name"],
                        "sw_version": self.version,
                        "model": f"TuxD Linux Agent Version {self.version}",
                    }))

                    ack_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    ack = json.loads(ack_raw)
                    if ack.get("type") == "error":
                        self._auth_error = ack.get("message", "unknown error")
                        self._connected_event.set()
                        return
                    self._auth_error = None
                    self._broker_lost = False
                    self._connected_event.set()

                    async for raw in ws:
                        self._handle_incoming(raw)
            except Exception as e:
                if self.tty_output:
                    print(self._gray(f"Direct: connection error: {e!r}"))
                self._ws = None
                self._broker_lost = True
                self._connected_event.set()
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(self._retry_delay)

    def _handle_incoming(self, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if msg.get("type") == "command":
            key = msg.get("key", "")
            payload = msg.get("payload", "")
            try:
                self.on_message(None, None, _IncomingMsg(key, payload))
            except Exception as e:
                if self.tty_output:
                    print(self._gray(f"Direct: on_message failed for {key}: {e!r}"))
        elif msg.get("type") == "ping":
            self._send_nowait({"type": "pong"})

    def _send_nowait(self, obj):
        if self._loop is None or self._ws is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps(obj)), self._loop)
        except Exception:
            pass

    def _shutdown(self):
        self._stop_event.set()
        if self._loop is not None and self._ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception:
                pass


    def publish(self, topic, payload, retain=True):
        if not isinstance(payload, str):
            payload = str(payload)

        is_discovery = topic.startswith("homeassistant/") and topic.endswith("/config")
        if retain and is_discovery:
            self._known_discovery_topics.add(topic)

        self.state_cache[topic] = payload

        if self.tty_output:
            if is_discovery:
                try:
                    data = json.loads(payload) if payload else {}
                    name = data.get("name") or "unknown"
                    st = data.get("state_topic")
                    if st:
                        self._state_topic_to_name[st] = name
                except Exception:
                    pass
            else:
                name = self._state_topic_to_name.get(topic) or topic.rsplit("/", 1)[-1]
                print(self._gray(f"Sent data: {name} = {payload}"))

        if is_discovery:
            domain, object_id = _parse_discovery_topic(topic)
            if domain is None:
                return
            if payload == "":
                self._send_nowait({"type": "discovery_clear", "domain": domain, "object_id": object_id})
            else:
                self._send_nowait({
                    "type": "discovery",
                    "domain": domain,
                    "object_id": object_id,
                    "config": json.loads(payload),
                })
        else:
            self._send_nowait({"type": "state", "key": topic, "value": payload, "retain": retain})

    def get_state(self, topic: str):
        return self.state_cache.get(topic, "")

    def clear_discovery(self, timeout=2.0):
        for topic in list(self._known_discovery_topics):
            self.publish(topic, "", retain=True)


    def _discovery_topic(self, domain, object_id):
        return f"homeassistant/{domain}/{self.config['device']['name']}/{object_id}/config"


def _to_ws_url(http_url: str) -> str:
    if http_url.startswith("https://"):
        return "wss://" + http_url[len("https://"):].rstrip("/")
    if http_url.startswith("http://"):
        return "ws://" + http_url[len("http://"):].rstrip("/")
    return http_url.rstrip("/")


def _parse_discovery_topic(topic: str):
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != "homeassistant" or parts[4] != "config":
        return None, None
    return parts[1], parts[3]
