from .base import Action, Integration


class CoreIntegration(Integration):
    KEY = "tuxd"
    NAME = "TuxD"
    ALWAYS_ON = True

    def _host_update(self):
        cfg = self.agent.config.get("host_update") or {}
        return cfg if cfg.get("enabled", False) else None

    def actions(self):
        actions = [
            Action("refresh", "Refresh sensors", "mdi:sync"),
            Action("restart_agent", "Restart agent", "mdi:cog-refresh", confirm=True),
            Action("check_agent_update", "Check for agent updates", "mdi:cloud-refresh"),
        ]
        host_update = self._host_update()
        if host_update:
            actions.append(Action("check_host_updates", "Check host updates", "mdi:package-variant"))
            if host_update.get("allow_install", False):
                actions += [
                    Action("update", "Update", "mdi:package-up", confirm=True),
                    Action("update_and_reboot_if_needed", "Update and reboot if needed", "mdi:restart", confirm=True),
                    Action("update_and_reboot", "Update and reboot", "mdi:restart-alert", confirm=True),
                ]
        if (self.agent.config.get("lm_sensors") or {}).get("enabled", False):
            actions.append(Action("lm_discover", "Discover lm-sensors", "mdi:thermometer-auto"))
        actions += [
            Action("reboot", "Reboot", "mdi:restart", confirm=True),
            Action("shutdown", "Shutdown", "mdi:power", confirm=True),
        ]
        return actions

    def run(self, action_id, target):
        agent = self.agent
        if action_id == "refresh":
            agent.handle_force_poll_message()
        elif action_id == "restart_agent":
            agent.handle_restart_message()
        elif action_id == "check_agent_update":
            agent.handle_self_update_check()
        elif action_id == "check_host_updates":
            agent.handle_host_update_check()
        elif action_id in ("update", "update_and_reboot_if_needed", "update_and_reboot"):
            agent.handle_host_update_button(action_id)
        elif action_id == "lm_discover":
            agent.run_lm_discovery()
        elif action_id in ("reboot", "shutdown"):
            agent.handle_custom_button(f"{agent.base_topic}/custom_button/{action_id}/set")
