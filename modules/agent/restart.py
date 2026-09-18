import os
import sys
import datetime
import time
import threading

from .base_direct import HADirectBase


class RestartMixin:
    def _hard_restart(self):
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            print(f"TuxD: os.execv failed ({e!r}) - exiting instead so the service supervisor restarts us")
            os._exit(1)

    def register_restart_button(self):
        self._button_discovery(
            "restart_agent",
            "Restart TuxD",
            f"{self.base_topic}/restart/set",
            icon="mdi:restart",
            entity_category="diagnostic"
        )
        if isinstance(self, HADirectBase):
            self.publish(self._discovery_topic("button", "refresh_agent"), "", retain=True)
        else:
            self._button_discovery(
                "refresh_agent",
                "Refresh TuxD Entities",
                f"{self.base_topic}/refresh/set",
                icon="mdi:refresh",
                entity_category="diagnostic"
            )
        self._button_discovery(
            "force_poll_agent",
            "Force Refresh All Sensors",
            f"{self.base_topic}/force_poll/set",
            icon="mdi:sync",
            entity_category="diagnostic"
        )

    def handle_restart_message(self):
        def _restart():
            try:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                msg = f'{ts}: Button "Restart TuxD" pressed!'
                try:
                    self.publish(self.terminal_output_topic, msg)
                except Exception:
                    pass
                if self.tty_output:
                    print(self._gray(msg))
                if self.log_file is not None:
                    try:
                        full_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.log_file.write(f"[{full_ts}] {msg}\n")
                        self.log_file.flush()
                    except Exception:
                        pass
                try:
                    self.set_error(True, "Restart button pressed")
                except Exception:
                    pass
                time.sleep(1.0)
            except Exception as e:
                print(f"TuxD: restart handler failed before restarting ({e!r}) - restarting anyway")
            self._hard_restart()

        threading.Thread(target=_restart, daemon=True).start()

    def handle_refresh_message(self):
        def _refresh():
            try:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                msg = f'{ts}: Button "Refresh TuxD Entities" pressed - clearing discovery...'
                try:
                    self.publish(self.terminal_output_topic, msg)
                except Exception:
                    pass
                if self.tty_output:
                    print(self._gray(msg))
                if self.log_file is not None:
                    try:
                        full_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.log_file.write(f"[{full_ts}] {msg}\n")
                        self.log_file.flush()
                    except Exception:
                        pass
                self.clear_discovery(timeout=5.0)
                time.sleep(5.0)
            except Exception as e:
                print(f"TuxD: refresh handler failed before restarting ({e!r}) - restarting anyway")
            self._hard_restart()

        threading.Thread(target=_refresh, daemon=True).start()

    def handle_force_poll_message(self):
        def _force_poll():
            try:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                msg = f'{ts}: Button "Force Refresh All Sensors" pressed - restarting to poll everything now...'
                try:
                    self.publish(self.terminal_output_topic, msg)
                except Exception:
                    pass
                if self.tty_output:
                    print(self._gray(msg))
                if self.log_file is not None:
                    try:
                        full_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.log_file.write(f"[{full_ts}] {msg}\n")
                        self.log_file.flush()
                    except Exception:
                        pass
                time.sleep(1.0)
            except Exception as e:
                print(f"TuxD: force-poll handler failed before restarting ({e!r}) - restarting anyway")
            self._hard_restart()

        threading.Thread(target=_force_poll, daemon=True).start()
