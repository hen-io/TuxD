from modules.tugboat import ACTIONS, HOST_ACTIONS

from .base import Action, Integration

_ICONS = {"Update": "mdi:update", "Start": "mdi:play", "Stop": "mdi:stop", "Restart": "mdi:restart",
          "Remove": "mdi:delete-outline", "Health Check": "mdi:heart-pulse",
          "Check images": "mdi:image-search-outline", "Update outdated stacks": "mdi:update",
          "Check for TugBoat update": "mdi:cloud-search-outline", "Update TugBoat": "mdi:ferry",
          "Soft reset": "mdi:backup-restore", "Run fixes": "mdi:wrench-outline"}
_SKIP = {"Rollback"}
_CONFIRM = {"Stop", "Remove", "Update", "Update outdated stacks", "Update TugBoat", "Soft reset", "Run fixes"}
_HOST_IDS = {"Update TugBoat": "self_update"}


def _action_id(label):
    return _HOST_IDS.get(label) or label.lower().replace(" ", "_")


class TugboatActionsIntegration(Integration):
    KEY = "tugboat"
    NAME = "TugBoat"

    def enabled(self):
        return bool(getattr(self.agent, "_tugboat_active", lambda: False)())

    def actions(self):
        actions = [Action(_action_id(label), label, _ICONS.get(label, "mdi:ferry"), confirm=label in _CONFIRM)
                   for label in HOST_ACTIONS]
        stacks = list(getattr(self.agent, "_tugboat_known_stacks", []) or [])
        if stacks:
            actions += [Action(_action_id(label), label, _ICONS.get(label, "mdi:ferry"),
                               targets=stacks, target_name="Stack", confirm=label in _CONFIRM)
                        for label in ACTIONS if label not in _SKIP]
        return actions

    def run(self, action_id, target):
        host = next((lab for lab in HOST_ACTIONS if _action_id(lab) == action_id), None)
        if host:
            if not self.agent.run_tugboat_host_action(host, wait=True):
                raise RuntimeError("TugBoat is already running an action")
            return
        label = next(lab for lab in ACTIONS if _action_id(lab) == action_id)
        agent = self.agent
        if not agent._tugboat_claim():
            raise RuntimeError("TugBoat is already running an action")
        agent._run_tugboat_action(target, agent.tr(label), False)
