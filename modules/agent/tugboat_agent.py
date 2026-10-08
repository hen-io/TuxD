import json
import os
import threading

from .shared import slugify as _slug
from modules.tugboat import ACTIONS, read_status, run_command, stack_names


class TugboatMixin:

    def init_tugboat(self):
        cfg = self.config.get("tugboat", {}) or {}
        self._tugboat_enabled = bool(cfg.get("enabled", False))
        self._tugboat_path = str(cfg.get("path") or "").strip()
        self._tugboat_python_bin = str(cfg.get("python_bin") or "python3").strip() or "python3"
        self._tugboat_interval = float(cfg.get("update_interval", 30))
        self._tugboat_known_stacks = []
        self._tugboat_stack_placeholder = self.tr("Select stack...")
        self._tugboat_action_placeholder = self.tr("Select action...")
        self._tugboat_all_stacks = self.tr("All stacks")
        self._tugboat_actions = {self.tr(label): flag for label, flag in ACTIONS.items()}
        self._tugboat_selected_stack = self._tugboat_stack_placeholder
        self._tugboat_selected_action = self._tugboat_action_placeholder
        self._tugboat_running = False
        self._tugboat_images = {}

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
            ha_object_id=f"{self.device_slug}_tugboat_enabled",
        )
        self.publish(f"{self.base_topic}/tugboat/enabled", "ON" if active else "OFF")

        if not active:
            return

        status = read_status(self._tugboat_path)
        self._tugboat_known_stacks = stack_names(status)
        for name in self._tugboat_known_stacks:
            self._register_tugboat_stack(name)
        self._register_tugboat_selects()
        self._tugboat_images = {}
        if status:
            self._publish_tugboat_status(status)

    def _register_tugboat_stack(self, name):
        slug = _slug(name)
        self._sensor_discovery(
            f"tugboat_{slug}_health",
            f"{name} {self.tr('Health')}",
            f"{self.base_topic}/tugboat/{slug}/health",
            icon="mdi:ferry",
            attributes_topic=f"{self.base_topic}/tugboat/{slug}/health_attributes",
            entity_category="diagnostic",
        )

    def _tugboat_stack_options(self):
        options = [self._tugboat_stack_placeholder]
        if len(self._tugboat_known_stacks) > 1:
            options.append(self._tugboat_all_stacks)
        return options + self._tugboat_known_stacks

    def _register_tugboat_selects(self):
        stack_options = self._tugboat_stack_options()
        if self._tugboat_selected_stack not in stack_options:
            self._tugboat_selected_stack = self._tugboat_stack_placeholder
        stack_state_topic = f"{self.base_topic}/tugboat/select_stack"
        self.publish(self._discovery_topic("select", "tugboat_select_stack"), json.dumps({
            "name": self.tr("TugBoat Stack"),
            "state_topic": stack_state_topic,
            "command_topic": f"{stack_state_topic}/set",
            "json_attributes_topic": f"{stack_state_topic}/attributes",
            "options": stack_options,
            "unique_id": f"{self.config['device']['name']}_tugboat_select_stack",
            "device": self.device_info,
            "icon": "mdi:ferry",
            "entity_category": "config",
            "default_entity_id": f"select.{self.device_slug}_tugboat_stack",
        }), retain=True)
        self.publish(stack_state_topic, self._tugboat_selected_stack, retain=True)
        self.publish(f"{stack_state_topic}/attributes", json.dumps({"stacks": self._tugboat_known_stacks}), retain=True)

        action_options = [self._tugboat_action_placeholder] + list(self._tugboat_actions)
        action_state_topic = f"{self.base_topic}/tugboat/select_action"
        self.publish(self._discovery_topic("select", "tugboat_select_action"), json.dumps({
            "name": self.tr("TugBoat Action"),
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
            "name": self.tr("TugBoat Execute"),
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
            if not isinstance(info, dict):
                continue
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
                "updates_available": info.get("updates_available", 0),
                "images_checked_at": info.get("images_checked_at", ""),
            }))

        self._publish_tugboat_images(stacks)

    def _publish_tugboat_images(self, stacks):
        seen = {}
        for name, info in stacks.items():
            if not isinstance(info, dict):
                continue
            stack_slug = _slug(name)
            for img in info.get("images") or []:
                if not isinstance(img, dict) or not img.get("image"):
                    continue
                ref = str(img["image"]).split("@", 1)[0]
                image_slug = _slug(ref)
                oid = f"tugboat_image_{stack_slug}_{image_slug}"
                topic = f"{self.base_topic}/tugboat/image/{stack_slug}/{image_slug}"
                if oid not in self._tugboat_images:
                    self._update_discovery(
                        oid,
                        f"{name}: {ref}",
                        f"{topic}/state",
                        command_topic=f"{topic}/set",
                        icon="mdi:ferry",
                        entity_category="diagnostic",
                        ha_object_id=f"{self.device_slug}_tugboat_{stack_slug}_{image_slug}",
                    )
                state = self._tugboat_image_state(ref, img)
                seen[oid] = (name, f"{topic}/state", state)
                self.publish(f"{topic}/state", json.dumps(state))

        for oid in set(self._tugboat_images) - set(seen):
            self.publish(self._discovery_topic("update", oid), "", retain=True)
        self._tugboat_images = seen

    @staticmethod
    def _tugboat_image_state(ref, img):
        status = str(img.get("status") or "unknown")
        detail = str(img.get("detail") or "")
        source = str(img.get("source_url") or "").strip()
        outdated = status in ("update_available", "not_pulled")

        tail = ref.rsplit("/", 1)[-1]
        tag = tail.split(":", 1)[1] if ":" in tail else "latest"
        installed = str(img.get("local_version") or "").strip() or tag
        latest = str(img.get("remote_version") or "").strip() or tag

        state = {
            "installed_version": installed,
            "latest_version": installed,
            "title": ref,
            "in_progress": False,
        }
        if source:
            github = source.startswith("https://github.com/") and source.rstrip("/").count("/") == 4
            state["release_url"] = source.rstrip("/") + "/releases" if github else source
        if outdated:
            state["latest_version"] = latest if latest != installed else f"{installed} (new image)"
            lines = [f"Current: {installed}", f"New: {state['latest_version']}"]
            if detail:
                lines.append(detail)
            if source:
                lines.append(f"Changelog / source: {state['release_url']}")
            state["release_summary"] = "\n".join(lines)
        elif detail:
            state["release_summary"] = detail
        return state

    def handle_tugboat_image_install(self, topic):
        try:
            stack_slug = topic.split("/tugboat/image/", 1)[1].split("/", 1)[0]
        except Exception:
            return
        stack = next((n for n in self._tugboat_known_stacks if _slug(n) == stack_slug), None)
        action = next((label for label, flag in self._tugboat_actions.items() if flag == "--update"), None)
        if not stack or not action or self._tugboat_running:
            return
        self._tugboat_running = True
        for name, state_topic, state in list(self._tugboat_images.values()):
            if name == stack:
                self.publish(state_topic, json.dumps(dict(state, in_progress=True)))
        threading.Thread(
            target=self._run_tugboat_action,
            args=(stack, action, False),
            daemon=True,
        ).start()

    def handle_tugboat_stack_select(self, payload):
        value = payload.strip()
        if value not in self._tugboat_stack_options():
            return
        self._tugboat_selected_stack = value
        self.publish(f"{self.base_topic}/tugboat/select_stack", value, retain=True)

    def handle_tugboat_action_select(self, payload):
        value = payload.strip()
        if value not in ([self._tugboat_action_placeholder] + list(self._tugboat_actions)):
            return
        self._tugboat_selected_action = value
        self.publish(f"{self.base_topic}/tugboat/select_action", value, retain=True)

    def handle_tugboat_execute(self):
        if self._tugboat_running:
            return
        if self._tugboat_selected_stack == self._tugboat_stack_placeholder:
            return
        if self._tugboat_selected_action == self._tugboat_action_placeholder:
            return
        self._tugboat_running = True
        threading.Thread(
            target=self._run_tugboat_action,
            args=(self._tugboat_selected_stack, self._tugboat_selected_action),
            daemon=True,
        ).start()

    def _run_tugboat_action(self, stack, action, reset_selects=True):
        flag = self._tugboat_actions.get(action)
        targets = list(self._tugboat_known_stacks) if stack == self._tugboat_all_stacks else [stack]
        try:
            outputs = []
            for target in targets:
                with self.busy(f"tugboat: {action} {target}"):
                    outputs.append(run_command(self._tugboat_python_bin, self._tugboat_path, flag, target))
                    if flag != "--healthcheck":
                        run_command(self._tugboat_python_bin, self._tugboat_path, "--healthcheck", target)
            output = "\n".join(o for o in outputs if o)

            if output and self._terminal_output_enabled():
                for line in output.splitlines():
                    if line:
                        self.publish(self.terminal_output_topic, line)

            status = read_status(self._tugboat_path)
            if status:
                self._publish_tugboat_status(status)
            else:
                for _name, state_topic, state in list(self._tugboat_images.values()):
                    self.publish(state_topic, json.dumps(state))
        finally:
            if reset_selects:
                self._tugboat_selected_stack = self._tugboat_stack_placeholder
                self._tugboat_selected_action = self._tugboat_action_placeholder
                self.publish(f"{self.base_topic}/tugboat/select_stack", self._tugboat_stack_placeholder, retain=True)
                self.publish(f"{self.base_topic}/tugboat/select_action", self._tugboat_action_placeholder, retain=True)
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
