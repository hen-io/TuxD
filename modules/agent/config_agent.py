import os
import json
import secrets
import tempfile
import yaml
import time
import datetime

from modules.config_sync import write_config
from modules.password import is_password_hash, verify_password


_CONFIG_FILE = "tuxd.conf"


class ConfigAgentMixin:
    _REQUEST_ACTIONS = ("get", "set", "set_password", "terminal", "terminal_stop", "run_action")

    def password_check(self, given):
        device = self.config.get("device", {}) or {}
        stored = str(device.get("password_hash") or "")
        plain = str(device.get("password") or "")
        if not stored and not plain:
            return None
        if not given:
            return "required"
        ok = verify_password(given, stored) if stored else secrets.compare_digest(str(given), plain)
        if ok:
            return None
        time.sleep(1.0)
        return "incorrect"

    def handle_config_request(self, payload):
        try:
            request = json.loads(payload or "{}")
            request_id = str(request.get("request_id") or "")
            action = request.get("action")
            if not request_id or action not in self._REQUEST_ACTIONS:
                return

            denied = self.password_check(request.get("password"))
            if denied:
                self._send_config_response(
                    request_id, False, auth=denied,
                    error="Incorrect password" if denied == "incorrect" else "Password required",
                )
                return

            if action == "set_password":
                self._set_password_hash(str(request.get("content") or ""))
                self._send_config_response(request_id, True)
                return

            if action == "run_action":
                error = self.run_integration_action(str(request.get("content") or ""))
                self._send_config_response(request_id, error is None, error=error)
                return

            if action in ("terminal", "terminal_stop"):
                if not self._terminal_input_enabled():
                    self._send_config_response(request_id, False, error="Terminal input is disabled on this device")
                    return
                if action == "terminal_stop":
                    self.handle_terminal_stop_message()
                else:
                    self.handle_terminal_message(str(request.get("content") or ""))
                self._send_config_response(request_id, True)
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

    def _set_password_hash(self, password_hash):
        if password_hash and not is_password_hash(password_hash):
            raise ValueError("Not a password hash")
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        device = cfg.setdefault("device", {})
        device.pop("password", None)
        device["password_hash"] = password_hash
        write_config(_CONFIG_FILE, cfg)
        self._config_own_write_mtime = os.path.getmtime(_CONFIG_FILE)
        live = self.config.setdefault("device", {})
        live.pop("password", None)
        live["password_hash"] = password_hash
        try:
            self.publish_terminal_lock()
        except Exception:
            pass

    def _send_config_response(self, request_id, ok, content=None, error=None, auth=None):
        sender = getattr(self, "_send_wait", None)
        if not callable(sender):
            return
        message = {"type": "config_response", "request_id": request_id, "ok": bool(ok)}
        if content is not None:
            message["content"] = content
        if error:
            message["error"] = error[:500]
        if auth:
            message["auth"] = auth
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
