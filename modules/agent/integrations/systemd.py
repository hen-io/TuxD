import json
import os
import re
import shlex

from ..shared import SENSOR_CMD_TIMEOUT, run_cmd, slugify
from .base import Action, Integration

_UNIT = re.compile(r"^[A-Za-z0-9@._:\\-]+$")
_PROPS = "Id,LoadState,ActiveState,SubState,UnitFileState,Description,ActiveEnterTimestamp"
_VERBS = {
    "start": ("Start", "mdi:play", False),
    "stop": ("Stop", "mdi:stop", True),
    "restart": ("Restart", "mdi:restart", False),
    "enable": ("Enable", "mdi:check-circle-outline", False),
    "disable": ("Disable", "mdi:close-circle-outline", True),
}


def parse_service(entry):
    text = str(entry or "").strip()
    scope = "system"
    if text.startswith("user:"):
        scope, text = "user", text[5:].strip()
    if not text or not _UNIT.match(text) or text.startswith("-"):
        return None
    if "." not in text:
        text += ".service"
    return scope, text


def parse_show(output):
    units, current = {}, {}
    for line in (output or "").splitlines() + [""]:
        if not line.strip():
            if current.get("Id"):
                units[current["Id"]] = current
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = value.strip()
    return units


class SystemdIntegration(Integration):
    KEY = "systemd"
    NAME = "Systemd services"
    DEFAULT_INTERVAL = 30

    def services(self):
        found, seen = [], set()
        for entry in self.cfg.get("services") or []:
            parsed = parse_service(entry)
            if parsed and parsed not in seen:
                seen.add(parsed)
                found.append((self._label(*parsed), *parsed))
        return found

    @staticmethod
    def _label(scope, unit):
        return f"user:{unit}" if scope == "user" else unit

    def _slug(self, scope, unit):
        name = unit[:-8] if unit.endswith(".service") else unit
        return slugify(f"user_{name}" if scope == "user" else name)

    @staticmethod
    def _systemctl(scope, *args):
        if scope == "user":
            return "systemctl --user " + " ".join(args)
        sudo = "" if os.geteuid() == 0 else "sudo "
        return f"{sudo}systemctl " + " ".join(args)

    def register(self):
        device_slug = self.agent.device_slug
        for label, scope, unit in self.services():
            slug = self._slug(scope, unit)
            base = f"{self.base_topic}/{slug}"
            self.agent._sensor_discovery(
                f"systemd_{slug}",
                f"{label} service",
                f"{base}/state",
                icon="mdi:cog-outline",
                attributes_topic=f"{base}/attributes",
                ha_object_id=f"{device_slug}_systemd_{slug}",
            )
        self.poll()

    def poll(self):
        services = self.services()
        failed = []
        for scope in ("system", "user"):
            units = [unit for _, s, unit in services if s == scope]
            if not units:
                continue
            out = run_cmd(self._systemctl(scope, "show", "--no-pager", "-p", _PROPS, "--",
                                          *map(shlex.quote, units)), timeout=SENSOR_CMD_TIMEOUT)
            shown = parse_show(out)
            for unit in units:
                info = shown.get(unit, {})
                loaded = info.get("LoadState", "") != "not-found"
                state = info.get("ActiveState") if info and loaded else "not-found"
                slug = self._slug(scope, unit)
                self.publish_changed(f"{self.base_topic}/{slug}/state", state or "unknown")
                self.publish_changed(f"{self.base_topic}/{slug}/attributes", json.dumps({
                    "unit": unit,
                    "scope": scope,
                    "sub_state": info.get("SubState", ""),
                    "enabled": info.get("UnitFileState", ""),
                    "description": info.get("Description", ""),
                    "since": info.get("ActiveEnterTimestamp", ""),
                }))
                if state in ("failed", "not-found"):
                    failed.append(f"{self._label(scope, unit)} {state}")
        self._alert(failed)

    def _alert(self, failed):
        level = str(self.cfg.get("failed_alert", "warning")).strip().lower()
        reason = "Service: " + ", ".join(failed) if failed else ""
        agent = self.agent
        if hasattr(agent, "set_standing_warning"):
            agent.set_standing_warning("systemd", reason if level == "warning" else "")
            agent.set_standing_error("systemd", reason if level == "error" else "")

    def actions(self):
        targets = [label for label, _, _ in self.services()]
        if not targets:
            return []
        return [Action(verb, name, icon, targets=targets, target_name="Service", confirm=confirm)
                for verb, (name, icon, confirm) in _VERBS.items()]

    def run(self, action_id, target):
        parsed = parse_service(target)
        if action_id not in _VERBS or not parsed:
            raise ValueError("Unknown action or service")
        scope, unit = parsed
        with self.agent.busy(f"systemd: {action_id} {unit}"):
            self.agent.run_to_terminal(self._systemctl(scope, action_id, "--", shlex.quote(unit)),
                                       f"systemd {action_id} {self._label(scope, unit)}", timeout=300)
        self.poll()
