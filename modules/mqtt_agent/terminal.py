import getpass
import select
import subprocess
import threading
import time
from collections import deque

TERMINAL_STOP_SENTINEL = "__tuxd_stop__"


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

    def _cmd_filter_cfg(self):
        val = self._terminal_input_cfg().get("command_filter", {})
        return val if isinstance(val, dict) else {}

    def _cmd_allowed(self, cmd):
        cfg = self._cmd_filter_cfg()
        mode = str(cfg.get("mode", "off")).strip().lower()
        if mode not in ("blacklist", "allowlist"):
            return True, None

        normalized = " ".join(cmd.lower().split())

        if mode == "blacklist":
            for pattern in (cfg.get("blacklist") or []):
                needle = " ".join(str(pattern).lower().split())
                if needle and needle in normalized:
                    return False, f"blocked by command blacklist rule: {pattern}"
            return True, None

        for pattern in (cfg.get("allowlist") or []):
            prefix = " ".join(str(pattern).lower().split())
            if prefix and normalized.startswith(prefix):
                return True, None
        return False, "command not in allowlist"

    def init_terminal(self):
        self.terminal_input_topic = f"{self.base_topic}/terminal_input"
        self.terminal_output_topic = f"{self.base_topic}/terminal_output"
        self.terminal_stop_topic = f"{self.base_topic}/terminal_stop"

        if not hasattr(self, "_terminal_queue"):
            self._terminal_queue = deque()
            self._terminal_queue_lock = threading.Lock()

        if not hasattr(self, "_terminal_cancel_event"):
            self._terminal_cancel_event = threading.Event()

        if not hasattr(self, "_terminal_username"):
            try:
                self._terminal_username = getpass.getuser()
            except Exception:
                self._terminal_username = "user"

        if self._terminal_input_enabled():
            self._text_discovery(
                object_id="terminal_input",
                name="Terminal Input",
                command_topic=f"{self.terminal_input_topic}/set",
                icon="mdi:console-line"
            )
            self.publish(self.terminal_input_topic, "")
            self._button_discovery(
                "terminal_stop",
                "Stop Terminal Command",
                f"{self.terminal_stop_topic}/set",
                icon="mdi:stop-circle-outline"
            )
        else:
            self.publish(self._discovery_topic("text", "terminal_input"), "", retain=True)
            self.publish(self._discovery_topic("button", "terminal_stop"), "", retain=True)

        if self._terminal_output_enabled():
            self._sensor_discovery(
                object_id="terminal_output",
                name="Terminal Output",
                state_topic=self.terminal_output_topic,
                icon="mdi:console"
            )
            self.publish(self.terminal_output_topic, "")

    def _terminal_prompt(self, cmd):
        return f"{self._terminal_username}:~$ {cmd}"

    def handle_terminal_message(self, payload: str):
        if payload == TERMINAL_STOP_SENTINEL:
            self.handle_terminal_stop_message()
            self.publish(self.terminal_input_topic, "")
            return

        cmd = (payload or "").strip()
        if not cmd:
            return

        allowed, reason = self._cmd_allowed(cmd)
        if not allowed:
            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, self._terminal_prompt(cmd))
                self.publish(self.terminal_output_topic, f"[{reason}]")
            self.publish(self.terminal_input_topic, "")
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

    def handle_terminal_stop_message(self):
        self._terminal_cancel_event.set()

    def _stream_cmd(self, cmd, on_line, timeout):
        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            on_line(f"ERR: {e}")
            return

        start = time.monotonic()
        stop_reason = None
        try:
            while True:
                if self._stop_event.is_set():
                    break
                if self._terminal_cancel_event.is_set():
                    stop_reason = "stopped by user"
                    break
                if time.monotonic() - start > timeout:
                    stop_reason = f"stopped after {int(timeout)}s"
                    break

                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue

                line = proc.stdout.readline()
                if line == "":
                    break
                if not on_line(line.rstrip("\n")):
                    break
        finally:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except Exception:
                    pass
            try:
                proc.stdout.close()
            except Exception:
                pass

        if stop_reason:
            on_line(f"[{stop_reason}]")

    def terminal_loop(self):
        if not self._terminal_input_enabled():
            return

        out_cfg = self._terminal_output_cfg()
        max_queue = int(out_cfg.get("max_queue", 50))
        post_interval = float(out_cfg.get("post_interval", 0.25))
        max_runtime = float(out_cfg.get("max_runtime", 60))

        while not self._stop_event.is_set():
            cmd = None
            with self._terminal_queue_lock:
                if self._terminal_queue:
                    cmd = self._terminal_queue.popleft()

            if cmd is None:
                self._stop_event.wait(timeout=0.5)
                continue

            self._terminal_cancel_event.clear()

            if self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, self._terminal_prompt(cmd))

            published = 0
            last_publish = 0.0

            def _on_line(line):
                nonlocal published, last_publish
                if self._stop_event.is_set():
                    return False
                if published >= max_queue:
                    return False
                wait_left = post_interval - (time.monotonic() - last_publish)
                if wait_left > 0:
                    self._stop_event.wait(timeout=wait_left)
                if self._terminal_output_enabled():
                    self.publish(self.terminal_output_topic, line)
                last_publish = time.monotonic()
                published += 1
                return True

            with self.busy(f"terminal: {cmd}"):
                self._stream_cmd(cmd, _on_line, max_runtime)

            if published == 0 and self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, "")

            self.publish(self.terminal_input_topic, "")
