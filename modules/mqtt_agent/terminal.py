import threading
from collections import deque
from .base import run_cmd


class TerminalMixin:
    def _terminal_input_enabled(self):
        val = (self.config.get("terminal") or {}).get("terminal_input", True)
        if isinstance(val, dict):
            return bool(val.get("enabled", True))
        return str(val).lower() in ("enabled", "true", "1", "yes")

    def _terminal_input_cfg(self):
        val = (self.config.get("terminal") or {}).get("terminal_input", {})
        return val if isinstance(val, dict) else {}

    def _terminal_output_cfg(self):
        val = (self.config.get("terminal") or {}).get("terminal_output", {})
        return val if isinstance(val, dict) else {}

    def _terminal_output_enabled(self):
        if getattr(self, "_interactive", False):
            return True
        return bool(self._terminal_output_cfg().get("enabled", True))

    def init_terminal(self):
        self.terminal_input_topic = f"{self.base_topic}/terminal_input"
        self.terminal_output_topic = f"{self.base_topic}/terminal_output"

        if not hasattr(self, "_terminal_queue"):
            self._terminal_queue = deque()
            self._terminal_queue_lock = threading.Lock()

        if self._terminal_input_enabled():
            self._text_discovery(
                object_id="terminal_input",
                name="Terminal Input",
                command_topic=f"{self.terminal_input_topic}/set",
                icon="mdi:console-line"
            )
            self.publish(self.terminal_input_topic, "")
        else:
            self.publish(self._discovery_topic("text", "terminal_input"), "", retain=True)

        if self._terminal_output_enabled():
            self._sensor_discovery(
                object_id="terminal_output",
                name="Terminal Output",
                state_topic=self.terminal_output_topic,
                icon="mdi:console"
            )
            self.publish(self.terminal_output_topic, "")

    def handle_terminal_message(self, payload: str):
        cmd = (payload or "").strip()
        if not cmd:
            return

        limit = int(self._terminal_input_cfg().get("input_queue_limit", 25))

        dropped = None
        with self._terminal_queue_lock:
            if limit > 0:
                while len(self._terminal_queue) >= limit:
                    dropped = self._terminal_queue.popleft()
            self._terminal_queue.append(cmd)
            depth = len(self._terminal_queue)

        if self._terminal_output_enabled():
            if dropped is not None:
                self.publish(self.terminal_output_topic, f"queue full, dropped: {dropped}")
            if depth > 1:
                self.publish(self.terminal_output_topic, f"queued ({depth}): {cmd}")

        self.publish(self.terminal_input_topic, "")

    def terminal_loop(self):
        if not self._terminal_input_enabled():
            return

        out_cfg = self._terminal_output_cfg()
        max_queue = int(out_cfg.get("max_queue", 50))
        post_interval = float(out_cfg.get("post_interval", 0.25))

        while not self._stop_event.is_set():
            cmd = None
            with self._terminal_queue_lock:
                if self._terminal_queue:
                    cmd = self._terminal_queue.popleft()

            if cmd is None:
                self._stop_event.wait(timeout=0.5)
                continue

            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, f"$ {cmd}")

            with self.busy(f"terminal: {cmd}"):
                output = run_cmd(cmd)
            lines = output.split("\n") if output else [""]

            if len(lines) > max_queue:
                lines = lines[-max_queue:]

            for line in lines:
                if self._stop_event.is_set():
                    return
                if self._terminal_output_enabled():
                    self.publish(self.terminal_output_topic, line)
                self._stop_event.wait(timeout=post_interval)

            self.publish(self.terminal_input_topic, "")
