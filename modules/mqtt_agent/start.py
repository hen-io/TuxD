import time
import threading
import datetime

from .base import HAMQTTBase
from .terminal import TerminalMixin
from .restart import RestartMixin
from .status_commands import StatusCommandsMixin
from .components import ComponentSensorsMixin
from .custom_entities import CustomEntitiesMixin
from .log_sensor_agent import LogSensorMixin
from .network_agent import NetworkMixin
from .disks_agent import DisksMixin
from .docker_agent import DockerMixin
from .host_update_agent import HostUpdateMixin
from .lm_sensors_agent import LMSensorsMixin
from .tasks_agent import TasksMixin
from .lag_monitor_agent import LagMonitorMixin
from .self_update_agent import SelfUpdateMixin
from .config_agent import ConfigAgentMixin
from .select_agent import SelectMixin
from .default_entities_agent import DefaultEntitiesMixin
from .system_status_agent import SystemStatusMixin


class HAMQTTAgent(
    TerminalMixin,
    RestartMixin,
    StatusCommandsMixin,
    DefaultEntitiesMixin,
    ComponentSensorsMixin,
    CustomEntitiesMixin,
    LogSensorMixin,
    NetworkMixin,
    DisksMixin,
    DockerMixin,
    HostUpdateMixin,
    LMSensorsMixin,
    LagMonitorMixin,
    SelfUpdateMixin,
    TasksMixin,
    SelectMixin,
    ConfigAgentMixin,
    SystemStatusMixin,
    HAMQTTBase,
):
    def __init__(self, config, version, log_file=None, log_level="all", update_status="Up to date",
                 update_checker=None, update_applier=None):
        HAMQTTBase.__init__(self, config, version, log_file=log_file, log_level=log_level)

        self._update_status = update_status
        self._update_checker = update_checker
        self._update_applier = update_applier
        self._startup_time = datetime.datetime.now().strftime("%H:%M:%S %d.%m.%y")

        self.init_terminal()
        self.init_status()
        self.init_default_entities()
        self.init_components()
        self.init_custom()
        self.init_log_sensors()
        self.init_network()
        self.init_disks()
        self.init_docker()
        self.init_host_update()
        self.init_self_update()
        self.init_lag_monitor()
        self.init_tasks()
        self.init_selects()
        self.init_config_numbers()
        self.init_config_switches()
        self.init_config_texts()
        self.init_system_status()

    def refresh_discovery(self):
        registrars = (
            self.init_terminal,
            self.register_restart_button,
            self.register_status_commands,
            self.register_default_entities,
            self.register_component_sensors,
            self.register_custom_sensors,
            self.register_custom_buttons,
            self.register_log_sensors,
            self.register_network,
            self.register_disks,
            self.register_docker,
            self.register_host_update,
            self.register_self_update,
            self.register_lag_monitor,
            self.register_lm_sensors,
            self.register_selects,
            self.register_config_numbers,
            self.register_config_switches,
            self.register_config_texts,
            self.register_system_status,
        )
        for registrar in registrars:
            try:
                registrar()
            except Exception as e:
                if self.tty_output:
                    print(self._gray(f"{registrar.__name__} failed: {e!r}"))

    def discovery_refresh_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.refresh_interval)
            if not self._stop_event.is_set():
                self.refresh_discovery()

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode()

        if topic == f"{self.base_topic}/terminal_input/set":
            if self._terminal_input_enabled():
                self.handle_terminal_message(payload)
            return

        if topic == f"{self.base_topic}/restart/set":
            self.handle_restart_message()
            return

        if topic == f"{self.base_topic}/refresh/set":
            self.handle_refresh_message()
            return

        if topic == f"{self.base_topic}/force_poll/set":
            self.handle_force_poll_message()
            return

        if topic.startswith(f"{self.base_topic}/custom_button/") and topic.endswith("/set"):
            self.handle_custom_button(topic)
            return

        if topic.startswith(f"{self.base_topic}/select/") and topic.endswith("/set"):
            self.handle_select_message(topic, payload)
            return

        if topic.startswith(f"{self.base_topic}/cfgnum/") and topic.endswith("/set"):
            self.handle_config_number(topic, payload)
            return

        if topic.startswith(f"{self.base_topic}/cfgsw/") and topic.endswith("/set"):
            self.handle_config_switch(topic, payload)
            return

        if topic.startswith(f"{self.base_topic}/cfgtxt/") and topic.endswith("/set"):
            self.handle_config_text(topic, payload)
            return

        if topic.startswith(f"{self.base_topic}/docker/update/") and topic.endswith("/set"):
            self.handle_docker_update_install(topic)
            return

        if topic == f"{self.base_topic}/host_update/set":
            self.handle_host_update_install()
            return

        if topic == f"{self.base_topic}/self_update/set":
            self.handle_self_update_install()
            return

        if topic == f"{self.base_topic}/self_update/check/set":
            self.handle_self_update_check()
            return

    def start(self, on_ready=None):
        self.connect()

        self.refresh_discovery()

        settle = float((self.config.get("device") or {}).get("discovery_settle_delay", 2.0))
        if settle > 0:
            self._stop_event.wait(timeout=settle)

        if self._terminal_input_enabled():
            self.client.subscribe(f"{self.base_topic}/terminal_input/set")
        self.client.subscribe(f"{self.base_topic}/restart/set")
        self.client.subscribe(f"{self.base_topic}/refresh/set")
        self.client.subscribe(f"{self.base_topic}/force_poll/set")
        self.client.subscribe(f"{self.base_topic}/select/+/set")
        self.client.subscribe(f"{self.base_topic}/cfgnum/+/set")
        self.client.subscribe(f"{self.base_topic}/cfgsw/+/set")
        self.client.subscribe(f"{self.base_topic}/cfgtxt/+/set")
        self.client.subscribe(f"{self.base_topic}/docker/update/+/set")
        self.client.subscribe(f"{self.base_topic}/host_update/set")
        self.client.subscribe(f"{self.base_topic}/self_update/set")
        self.client.subscribe(f"{self.base_topic}/self_update/check/set")

        if on_ready is not None:
            try:
                on_ready()
            except Exception:
                pass

        try:
            if self._terminal_output_enabled():
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self.publish(self.terminal_output_topic, f"{ts}: TuxD Startup complete")
        except Exception:
            pass

        threading.Thread(target=self.discovery_refresh_loop, daemon=True).start()
        threading.Thread(target=self.run_status_commands_loop, daemon=True).start()
        threading.Thread(target=self.run_default_entities_loop, daemon=True).start()
        threading.Thread(target=self.component_sensors_loop, daemon=True).start()
        threading.Thread(target=self.custom_sensor_loop, daemon=True).start()
        threading.Thread(target=self.log_sensor_loop, daemon=True).start()
        threading.Thread(target=self.terminal_loop, daemon=True).start()

        for n in self.config.get("network", []):
            t = threading.Thread(target=self.network_loop, args=(n,), daemon=True)
            t.start()

        for d in self.config.get("disk", []):
            t = threading.Thread(target=self.disk_loop, args=(d,), daemon=True)
            t.start()

        lm_cfg = self.config.get("lm_sensors", {})
        if lm_cfg.get("enabled", False):
            interval = lm_cfg.get("update_interval", 10)
            overrides = lm_cfg.get("overrides", {})
            t = threading.Thread(
                target=self.lm_sensors_loop,
                args=(interval, overrides),
                daemon=True
            )
            t.start()

        lag_cfg = self.config.get("lag-monitor", {})
        if lag_cfg.get("enabled", False):
            threading.Thread(target=self.lag_monitor_loop, daemon=True).start()

        if self.config.get("docker", {}).get("enabled", False):
            threading.Thread(target=self.docker_loop, daemon=True).start()

        if self.config.get("host_update", {}).get("enabled", False):
            threading.Thread(target=self.host_update_loop, daemon=True).start()

        threading.Thread(target=self.self_update_loop, daemon=True).start()

        if self.config.get("tasks"):
            threading.Thread(target=self.tasks_loop, daemon=True).start()

        try:
            self._stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.stop()

        return self._broker_lost

    def stop(self):
        self._stop_event.set()
        try:
            self.client.loop_stop()
        except Exception:
            pass
        try:
            self.client.disconnect()
        except Exception:
            pass
