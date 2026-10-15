import shlex

from ..shared import slugify
from .base import Action, Integration


class CustomActionsIntegration(Integration):
    KEY = "actions"
    NAME = "Actions"
    ALWAYS_ON = True

    def _entries(self):
        found, seen = [], set()
        for entry in self.agent.config.get("actions") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            cmd = str(entry.get("cmd") or entry.get("command") or "").strip()
            action_id = slugify(name)
            if not name or not cmd or not action_id or action_id in seen:
                continue
            seen.add(action_id)
            found.append((action_id, name, cmd, entry))
        return found

    def actions(self):
        actions = []
        for action_id, name, _cmd, entry in self._entries():
            targets = entry.get("targets")
            targets = [str(t) for t in targets if str(t).strip()] if isinstance(targets, list) else None
            actions.append(Action(action_id, name, str(entry.get("icon") or "mdi:play"), targets=targets,
                                  target_name=str(entry.get("target_name") or "Target"),
                                  confirm=bool(entry.get("confirm", False))))
        return actions

    def run(self, action_id, target):
        entry = next((e for e in self._entries() if e[0] == action_id), None)
        if entry is None:
            raise ValueError("Unknown action")
        _id, name, cmd, _entry = entry
        if target is not None:
            cmd = cmd.replace("{target}", shlex.quote(target))
        title = f'Action "{name}"' + (f" ({target})" if target is not None else "")
        with self.agent.busy(f"action: {name}"):
            self.agent.run_to_terminal(cmd, title)
