from modules.tugboat import ACTIONS

from .base import Action, Integration

_ICONS = {"Update": "mdi:update", "Start": "mdi:play", "Stop": "mdi:stop", "Restart": "mdi:restart",
          "Remove": "mdi:delete-outline", "Health Check": "mdi:heart-pulse"}
_SKIP = {"Rollback"}
_CONFIRM = {"Stop", "Remove", "Update"}


class TugboatActionsIntegration(Integration):
    KEY = "tugboat"
    NAME = "TugBoat"

    def enabled(self):
        return bool(getattr(self.agent, "_tugboat_active", lambda: False)())

    def actions(self):
        stacks = list(getattr(self.agent, "_tugboat_known_stacks", []) or [])
        if not stacks:
            return []
        return [Action(label.lower().replace(" ", "_"), label, _ICONS.get(label, "mdi:ferry"),
                       targets=stacks, target_name="Stack", confirm=label in _CONFIRM)
                for label in ACTIONS if label not in _SKIP]

    def run(self, action_id, target):
        label = next(lab for lab in ACTIONS if lab.lower().replace(" ", "_") == action_id)
        agent = self.agent
        if agent._tugboat_running:
            raise RuntimeError("TugBoat is already running an action")
        agent._tugboat_running = True
        agent._run_tugboat_action(target, agent.tr(label), False)
