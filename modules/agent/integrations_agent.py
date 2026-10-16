import json
import threading

from .integrations import INTEGRATIONS


class IntegrationsMixin:

    def init_integrations(self):
        self.integrations = [cls(self) for cls in INTEGRATIONS]
        self._actions_topic = f"{self.base_topic}/actions"
        self._action_placeholder = self.tr("Select action...")
        self._target_placeholder = self.tr("Select target...")
        self._picked_action = self._action_placeholder
        self._picked_target = self._target_placeholder

    def _active_integrations(self):
        active = []
        for integration in self.integrations:
            try:
                if integration.enabled():
                    active.append(integration)
            except Exception:
                pass
        return active

    def register_integrations(self):
        for integration in self._active_integrations():
            try:
                integration.register()
            except Exception as e:
                if self.tty_output:
                    print(self._gray(f"integration {integration.KEY} failed to register: {e!r}"))
        self._sensor_discovery(
            "actions",
            "Actions",
            self._actions_topic,
            icon="mdi:gesture-tap-button",
            attributes_topic=f"{self._actions_topic}/attributes",
            entity_category="diagnostic",
        )
        self.publish_action_catalog(force=True)

    def _action_labels(self):
        return {f"{a['integration_name']}: {a['name']}": a for a in self.action_catalog()}

    def _select_payload(self, object_id, name, topic, options, icon, entity_id):
        return json.dumps({
            "name": self.tr(name),
            "state_topic": topic,
            "command_topic": f"{topic}/set",
            "options": options,
            "unique_id": f"{self.config['device']['name']}_{object_id}",
            "device": self.device_info,
            "icon": icon,
            "entity_category": "config",
            "default_entity_id": entity_id,
        })

    def _register_action_entities(self):
        labels = self._action_labels()
        if self._picked_action not in labels:
            self._picked_action = self._action_placeholder
        picked = labels.get(self._picked_action)
        targets = (picked or {}).get("targets") or []
        if self._picked_target not in targets:
            self._picked_target = self._target_placeholder
        slug = self.device_slug
        self.publish(self._discovery_topic("select", "action_select"), self._select_payload(
            "action_select", "Action", f"{self._actions_topic}/select",
            [self._action_placeholder] + list(labels), "mdi:gesture-tap-button", f"select.{slug}_action"), retain=True)
        self.publish(f"{self._actions_topic}/select", self._picked_action, retain=True)
        self.publish(self._discovery_topic("select", "action_target"), self._select_payload(
            "action_target", "Action Target", f"{self._actions_topic}/target",
            [self._target_placeholder] + list(targets), "mdi:target", f"select.{slug}_action_target"), retain=True)
        self.publish(f"{self._actions_topic}/target", self._picked_target, retain=True)
        self.publish(self._discovery_topic("button", "action_execute"), json.dumps({
            "name": self.tr("Execute Action"),
            "command_topic": f"{self._actions_topic}/execute/set",
            "unique_id": f"{self.config['device']['name']}_action_execute",
            "device": self.device_info,
            "icon": "mdi:play-circle-outline",
            "entity_category": "config",
            "default_entity_id": f"button.{slug}_action_execute",
        }), retain=True)

    def handle_actions_command(self, topic, payload):
        value = (payload or "").strip()
        if topic.endswith("/select/set"):
            if value in self._action_labels() or value == self._action_placeholder:
                self._picked_action = value
                self._picked_target = self._target_placeholder
                self._register_action_entities()
        elif topic.endswith("/target/set"):
            picked = self._action_labels().get(self._picked_action) or {}
            if value in (picked.get("targets") or []) or value == self._target_placeholder:
                self._picked_target = value
                self.publish(f"{self._actions_topic}/target", value, retain=True)
        elif topic.endswith("/execute/set"):
            picked = self._action_labels().get(self._picked_action)
            if not picked:
                return
            needs_target = isinstance(picked.get("targets"), list)
            if needs_target and self._picked_target == self._target_placeholder:
                return
            request = {"integration": picked["integration"], "action": picked["id"]}
            if needs_target:
                request["target"] = self._picked_target
            error = self.run_integration_action(json.dumps(request))
            if error and self._terminal_output_enabled():
                self.publish(self.terminal_output_topic, f"[{error}]")
            self._picked_action = self._action_placeholder
            self._picked_target = self._target_placeholder
            self._register_action_entities()

    def action_catalog(self):
        catalog = []
        for integration in self._active_integrations():
            try:
                catalog += [action.to_dict(integration) for action in integration.actions()]
            except Exception:
                pass
        return catalog

    def publish_action_catalog(self, force=False):
        catalog = self.action_catalog()
        payload = json.dumps({"actions": catalog})
        if force or self.state_cache.get(f"{self._actions_topic}/attributes") != payload:
            self.publish(f"{self._actions_topic}/attributes", payload)
            self.publish(self._actions_topic, str(len(catalog)))
            self._register_action_entities()

    def start_integrations(self):
        for integration in self._active_integrations():
            if integration.has_poll():
                threading.Thread(target=self._integration_loop, args=(integration,), daemon=True).start()
        threading.Thread(target=self._catalog_loop, daemon=True).start()

    def _integration_loop(self, integration):
        while not self._stop_event.is_set():
            try:
                integration.poll()
            except Exception as e:
                if self.tty_output:
                    print(self._gray(f"integration {integration.KEY} poll failed: {e!r}"))
            self._stop_event.wait(timeout=integration.interval())

    def _catalog_loop(self):
        while not self._stop_event.wait(timeout=30):
            try:
                self.publish_action_catalog()
            except Exception:
                pass

    def before_power_off(self, kind):
        for integration in self._active_integrations():
            try:
                integration.before_power(kind)
            except Exception as e:
                if self._terminal_output_enabled():
                    self.publish(self.terminal_output_topic, f"[{integration.KEY} before {kind} failed: {e}]")

    def run_integration_action(self, content):
        try:
            request = json.loads(content or "{}")
        except ValueError:
            return "Not a valid action request"
        key, action_id, target = request.get("integration"), request.get("action"), request.get("target")
        integration = next((i for i in self._active_integrations() if i.KEY == key), None)
        if integration is None:
            return f"No integration {key!r} on this device"
        action = next((a for a in integration.actions() if a.id == action_id), None)
        if action is None:
            return f"No action {action_id!r} in {key}"
        if action.targets is not None and target not in action.targets:
            return f"{target!r} is not one of {integration.KEY}'s targets"

        def work():
            try:
                integration.run(action_id, target if action.targets is not None else None)
            except Exception as e:
                if self._terminal_output_enabled():
                    self.publish(self.terminal_output_topic, f"[{key} {action_id} failed: {e}]")
        threading.Thread(target=work, daemon=True).start()
        return None
