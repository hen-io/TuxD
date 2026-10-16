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
        actions = [Action("self_update", "Update TugBoat", "mdi:ferry", confirm=True)]
        stacks = list(getattr(self.agent, "_tugboat_known_stacks", []) or [])
        if stacks:
            actions += [Action(label.lower().replace(" ", "_"), label, _ICONS.get(label, "mdi:ferry"),
                               targets=stacks, target_name="Stack", confirm=label in _CONFIRM)
                        for label in ACTIONS if label not in _SKIP]
        return actions

    def run(self, action_id, target):
        if action_id == "self_update":
            if not self.agent.handle_tugboat_self_update(wait=True):
                raise RuntimeError("TugBoat is already running an action")
            return
        label = next(lab for lab in ACTIONS if lab.lower().replace(" ", "_") == action_id)
        agent = self.agent
        if not agent._tugboat_claim():
            raise RuntimeError("TugBoat is already running an action")
        agent._run_tugboat_action(target, agent.tr(label), False)
