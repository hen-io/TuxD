import os
import sys
import datetime
import time
import threading


class RestartMixin:
    def register_restart_button(self):
        self._button_discovery(
            "restart_agent",
            "Restart TuxD",
            f"{self.base_topic}/restart/set",
            icon="mdi:restart",
            entity_category="diagnostic"
        )
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
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            msg = f'{ts}: Button "Restart TuxD" pressed!'
            self.publish(self.terminal_output_topic, msg)
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
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_restart, daemon=True).start()

    def handle_refresh_message(self):
        def _refresh():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            msg = f'{ts}: Button "Refresh TuxD Entities" pressed - clearing discovery...'
            self.publish(self.terminal_output_topic, msg)
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
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_refresh, daemon=True).start()

    def handle_force_poll_message(self):
        def _force_poll():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            msg = f'{ts}: Button "Force Refresh All Sensors" pressed - restarting to poll everything now...'
            self.publish(self.terminal_output_topic, msg)
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
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_force_poll, daemon=True).start()
