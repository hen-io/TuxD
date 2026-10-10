import os
import json
import tempfile
import yaml
import time
import datetime


_CONFIG_FILE = "tuxd.conf"


class ConfigAgentMixin:
    def handle_config_request(self, payload):
        try:
            request = json.loads(payload or "{}")
            request_id = str(request.get("request_id") or "")
            action = request.get("action")
            if not request_id or action not in ("get", "set"):
                return

            if action == "get":
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                self._send_config_response(request_id, True, content=content)
                return

            content = request.get("content")
            if not isinstance(content, str) or len(content) > 1024 * 1024:
                self._send_config_response(request_id, False, error="Configuration is missing or too large")
                return
            parsed = yaml.safe_load(content)
            if not isinstance(parsed, dict):
                self._send_config_response(request_id, False, error="tuxd.conf must contain a YAML mapping")
                return

            mode = os.stat(_CONFIG_FILE).st_mode if os.path.exists(_CONFIG_FILE) else 0o600
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=".", prefix=".tuxd.conf.", delete=False
            ) as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                temp_path = tmp.name
            os.chmod(temp_path, mode & 0o777)
            os.replace(temp_path, _CONFIG_FILE)
            self._send_config_response(request_id, True)
        except Exception as e:
            try:
                if "temp_path" in locals():
                    os.unlink(temp_path)
            except Exception:
                pass
            self._send_config_response(request_id, False, error=str(e))

    def _send_config_response(self, request_id, ok, content=None, error=None):
        sender = getattr(self, "_send_wait", None)
        if not callable(sender):
            return
        message = {"type": "config_response", "request_id": request_id, "ok": bool(ok)}
        if content is not None:
            message["content"] = content
        if error:
            message["error"] = error[:500]
        sender(message, timeout=5.0)

    def _config_restart(self):
        print("TuxD: restarting now due to a configuration change...")
        try:
            if self._terminal_output_enabled():
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self.publish(self.terminal_output_topic, f"{ts}: Restarting TuxD...")
                time.sleep(0.5)
            self.set_warning(True, "Configuration changed")
        except Exception:
            pass
        try:
            self.clear_discovery(timeout=5.0)
        except Exception:
            pass
        self._hard_restart()

    def config_file_watch_loop(self):
        extra_config_path = self.config.get("device", {}).get("extra_config_path", "")

        def _mtime(path):
            try:
                return os.path.getmtime(path)
            except OSError:
                return None

        last_mtime = _mtime(_CONFIG_FILE)
        if last_mtime is None:
            print(f"TuxD: config_file_watch_loop could not read {_CONFIG_FILE}")
        last_extra_mtime = _mtime(extra_config_path) if extra_config_path else None

        while not self._stop_event.wait(timeout=5.0):
            mtime = _mtime(_CONFIG_FILE)
            extra_mtime = _mtime(extra_config_path) if extra_config_path else None

            changed_path = None
            if (
                mtime is not None and last_mtime is not None and mtime != last_mtime
                and mtime != getattr(self, "_config_own_write_mtime", None)
            ):
                changed_path = _CONFIG_FILE
            elif (
                extra_config_path and extra_mtime is not None
                and last_extra_mtime is not None and extra_mtime != last_extra_mtime
            ):
                changed_path = extra_config_path

            if changed_path:
                msg = f"TuxD: detected external change to {changed_path} - restarting now"
                print(msg)
                try:
                    if self._terminal_output_enabled():
                        self.publish(self.terminal_output_topic, msg)
                except Exception:
                    pass
                try:
                    self._config_restart()
                except Exception as e:
                    print(f"TuxD: restart after config change FAILED: {e!r}")

            last_mtime = mtime
            last_extra_mtime = extra_mtime
