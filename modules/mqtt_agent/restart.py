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
            icon="mdi:restart"
        )
        self._button_discovery(
            "refresh_agent",
            "Refresh TuxD Entities",
            f"{self.base_topic}/refresh/set",
            icon="mdi:refresh"
        )

    def handle_restart_message(self):
        def _restart():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            msg = f'{ts}: Button "Restart TuxD" pressed!'
            self.state_cache[self.terminal_output_topic] = msg
            self.client.publish(self.terminal_output_topic, msg)
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
            self.state_cache[self.terminal_output_topic] = msg
            self.client.publish(self.terminal_output_topic, msg)
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
            time.sleep(15.0)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_refresh, daemon=True).start()
