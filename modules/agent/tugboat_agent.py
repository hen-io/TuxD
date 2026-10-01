import json
import os
import threading

from .shared import slugify as _slug
from modules.tugboat import ACTIONS, read_status, run_command, stack_names

_STACK_PLACEHOLDER = "Select stack..."
_ACTION_PLACEHOLDER = "Select action..."


class TugboatMixin:

    def init_tugboat(self):
        cfg = self.config.get("tugboat", {}) or {}
        self._tugboat_enabled = bool(cfg.get("enabled", False))
        self._tugboat_path = str(cfg.get("path") or "").strip()
        self._tugboat_python_bin = str(cfg.get("python_bin") or "python3").strip() or "python3"
        self._tugboat_interval = float(cfg.get("update_interval", 30))
        self._tugboat_known_stacks = []
        self._tugboat_selected_stack = _STACK_PLACEHOLDER
        self._tugboat_selected_action = _ACTION_PLACEHOLDER
        self._tugboat_running = False

    def _tugboat_active(self):
        if not self._tugboat_enabled or not self._tugboat_path:
            return False
        return os.path.isfile(os.path.join(self._tugboat_path, "TugBoat.py"))

    def register_tugboat(self):
        active = self._tugboat_active()

        self._binary_sensor_discovery(
            "tugboat_enabled",
            "TugBoat enabled",
            f"{self.base_topic}/tugboat/enabled",
            icon="mdi:ferry",
            entity_category="diagnostic",
        )
        self.publish(f"{self.base_topic}/tugboat/enabled", "ON" if active else "OFF")

        if not active:
            return

        status = read_status(self._tugboat_path)
        self._tugboat_known_stacks = stack_names(status)
        for name in self._tugboat_known_stacks:
            self._register_tugboat_stack(name)
        self._register_tugboat_selects()
        if status:
            self._publish_tugboat_status(status)

    def _register_tugboat_stack(self, name):
        slug = _slug(name)
        self._sensor_discovery(
            f"tugboat_{slug}_health",
            f"{name} Health",
            f"{self.base_topic}/tugboat/{slug}/health",
            icon="mdi:ferry",
            attributes_topic=f"{self.base_topic}/tugboat/{slug}/health_attributes",
            entity_category="diagnostic",
        )

    def _register_tugboat_selects(self):
        stack_options = [_STACK_PLACEHOLDER] + self._tugboat_known_stacks
        if self._tugboat_selected_stack not in stack_options:
            self._tugboat_selected_stack = _STACK_PLACEHOLDER
        stack_state_topic = f"{self.base_topic}/tugboat/select_stack"
        self.publish(self._discovery_topic("select", "tugboat_select_stack"), json.dumps({
            "name": "TugBoat Stack",
            "state_topic": stack_state_topic,
            "command_topic": f"{stack_state_topic}/set",
            "options": stack_options,
            "unique_id": f"{self.config['device']['name']}_tugboat_select_stack",
            "device": self.device_info,
            "icon": "mdi:ferry",
            "entity_category": "config",
            "default_entity_id": f"select.{self.device_slug}_tugboat_stack",
        }), retain=True)
        self.publish(stack_state_topic, self._tugboat_selected_stack, retain=True)

        action_options = [_ACTION_PLACEHOLDER] + list(ACTIONS.keys())
        action_state_topic = f"{self.base_topic}/tugboat/select_action"
        self.publish(self._discovery_topic("select", "tugboat_select_action"), json.dumps({
            "name": "TugBoat Action",
            "state_topic": action_state_topic,
            "command_topic": f"{action_state_topic}/set",
            "options": action_options,
            "unique_id": f"{self.config['device']['name']}_tugboat_select_action",
            "device": self.device_info,
            "icon": "mdi:cog-play-outline",
            "entity_category": "config",
            "default_entity_id": f"select.{self.device_slug}_tugboat_action",
        }), retain=True)
        self.publish(action_state_topic, self._tugboat_selected_action, retain=True)

        self.publish(self._discovery_topic("button", "tugboat_execute"), json.dumps({
            "name": "TugBoat Execute",
            "command_topic": f"{self.base_topic}/tugboat/execute/set",
            "unique_id": f"{self.config['device']['name']}_tugboat_execute",
            "device": self.device_info,
            "icon": "mdi:play-circle-outline",
            "entity_category": "config",
            "default_entity_id": f"button.{self.device_slug}_tugboat_execute",
        }), retain=True)

    def _publish_tugboat_status(self, status):
        stacks = status.get("stacks", {})
        new_names = sorted(stacks.keys())
        if new_names != self._tugboat_known_stacks:
            added = [n for n in new_names if n not in self._tugboat_known_stacks]
            self._tugboat_known_stacks = new_names
            for name in added:
                self._register_tugboat_stack(name)
            self._register_tugboat_selects()

        for name, info in stacks.items():
            slug = _slug(name)
            base = f"{self.base_topic}/tugboat/{slug}"
            self.publish(f"{base}/health", str(info.get("health") or "unknown"))
            self.publish(f"{base}/health_attributes", json.dumps({
                "summary": info.get("summary", ""),
                "problems": info.get("problems", []),
                "checked_at": info.get("checked_at", ""),
                "containers": [
                    {
                        "name": c.get("name"),
                        "state": c.get("state"),
                        "health": c.get("health"),
                        "status": c.get("status"),
                    }
                    for c in (info.get("containers") or [])
                ],
                "last_action": info.get("last_action", {}),
                "last_update": info.get("last_update", ""),
                "last_backup": info.get("last_backup", ""),
            }))

    def handle_tugboat_stack_select(self, payload):
        value = payload.strip()
        if value not in ([_STACK_PLACEHOLDER] + self._tugboat_known_stacks):
            return
        self._tugboat_selected_stack = value
        self.publish(f"{self.base_topic}/tugboat/select_stack", value, retain=True)

    def handle_tugboat_action_select(self, payload):
        value = payload.strip()
        if value not in ([_ACTION_PLACEHOLDER] + list(ACTIONS.keys())):
            return
        self._tugboat_selected_action = value
        self.publish(f"{self.base_topic}/tugboat/select_action", value, retain=True)

    def handle_tugboat_execute(self):
        if self._tugboat_running:
            return
        if self._tugboat_selected_stack == _STACK_PLACEHOLDER:
            return
        if self._tugboat_selected_action == _ACTION_PLACEHOLDER:
            return
        self._tugboat_running = True
        threading.Thread(
            target=self._run_tugboat_action,
            args=(self._tugboat_selected_stack, self._tugboat_selected_action),
            daemon=True,
        ).start()

    def _run_tugboat_action(self, stack, action):
        flag = ACTIONS.get(action)
        try:
            with self.busy(f"tugboat: {action} {stack}"):
                output = run_command(self._tugboat_python_bin, self._tugboat_path, flag, stack)
                if flag != "--healthcheck":
                    run_command(self._tugboat_python_bin, self._tugboat_path, "--healthcheck", stack)

            if output and self._terminal_output_enabled():
                for line in output.splitlines():
                    if line:
                        self.publish(self.terminal_output_topic, line)

            status = read_status(self._tugboat_path)
            if status:
                self._publish_tugboat_status(status)
        finally:
            self._tugboat_selected_stack = _STACK_PLACEHOLDER
            self._tugboat_selected_action = _ACTION_PLACEHOLDER
            self.publish(f"{self.base_topic}/tugboat/select_stack", _STACK_PLACEHOLDER, retain=True)
            self.publish(f"{self.base_topic}/tugboat/select_action", _ACTION_PLACEHOLDER, retain=True)
            self._tugboat_running = False

    def tugboat_loop(self):
        if not self._tugboat_active():
            return
        while not self._stop_event.is_set():
            try:
                if not self._tugboat_running:
                    status = read_status(self._tugboat_path)
                    if status:
                        self._publish_tugboat_status(status)
            except Exception:
                pass
            self._stop_event.wait(timeout=self._tugboat_interval)
