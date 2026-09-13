import time
import datetime
import threading

from .base import run_cmd


class TasksMixin:
    def init_tasks(self):
        self._tasks_cfg = self.config.get("tasks", []) or []

    def _run_task(self, cmd, write_to_terminal=True):
        output = run_cmd(cmd)
        if write_to_terminal and self._terminal_output_enabled() and output:
            for line in output.split("\n"):
                if line:
                    self.publish(self.terminal_output_topic, line)

    def _log_task(self, cmd, write_to_terminal=True, log_on_run=True):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        msg = f'{ts}: Task "{cmd}" started!'
        if log_on_run and self._terminal_output_enabled():
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

    def tasks_loop(self):
        if not self._tasks_cfg:
            return

        last_run = [0.0] * len(self._tasks_cfg)

        while not self._stop_event.is_set():
            now = time.time()

            for i, task in enumerate(self._tasks_cfg):
                if self._stop_event.is_set():
                    return
                cmd = (task.get("cmd") or "").strip()
                interval = task.get("update_interval", 3600)
                write_to_terminal = bool(task.get("write_to_terminal", True))
                log_on_run = bool(task.get("log_on_run", True))
                if not cmd:
                    continue
                if now - last_run[i] >= interval:
                    last_run[i] = now
                    self._log_task(cmd, write_to_terminal, log_on_run)
                    threading.Thread(target=self._run_task, args=(cmd, write_to_terminal), daemon=True).start()

            self._stop_event.wait(timeout=10)
