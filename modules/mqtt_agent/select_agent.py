import json
import datetime
import threading
from .base import run_cmd


class SelectMixin:
    def init_selects(self):
        self._selects_cfg = self.config.get("select", []) or []
        self._select_cmd_topics = {}

    def register_selects(self):
        self._select_cmd_topics = {}

        for s in self._selects_cfg:
            name = s.get("name", "")
            if not name:
                continue
            slug = name.replace(" ", "_").lower()
            obj_id = f"select_{slug}"
            state_topic = f"{self.base_topic}/select/{slug}"
            command_topic = f"{self.base_topic}/select/{slug}/set"

            option_names = [opt["name"] for opt in (s.get("options") or []) if opt.get("name")]

            payload = {
                "name": name,
                "state_topic": state_topic,
                "command_topic": command_topic,
                "options": option_names,
                "unique_id": f"{self.config['device']['name']}_{obj_id}",
                "device": self.device_info,
            }
            if s.get("icon"):
                payload["icon"] = s["icon"]

            self.publish(
                f"homeassistant/select/{self.config['device']['name']}/{obj_id}/config",
                json.dumps(payload),
                retain=True,
            )

            initial_option = (s.get("initial_option") or "").strip()
            state = None

            get_state_cmd = (s.get("get_state_cmd") or "").strip()
            if get_state_cmd:
                raw = run_cmd(get_state_cmd).strip()
                state = self._select_match_state(s, raw)

            if not state:
                state = initial_option

            if state:
                self.publish(state_topic, state, retain=True)

            self._select_cmd_topics[command_topic] = s

    def _select_match_state(self, s, raw):
        if not raw:
            return None
        for opt in (s.get("options") or []):
            opt_name = opt.get("name", "")
            state_value = opt.get("state_value", "")
            if state_value:
                if raw.strip() == state_value.strip():
                    return opt_name
            elif raw.strip().lower() == opt_name.strip().lower():
                return opt_name
        return None

    def handle_select_message(self, topic, payload):
        s = self._select_cmd_topics.get(topic)
        if s is None:
            return

        selected_name = payload.strip()
        write_to_terminal = bool(s.get("write_to_terminal", True))

        matched_opt = None
        for opt in (s.get("options") or []):
            if opt.get("name") == selected_name:
                matched_opt = opt
                break

        if matched_opt is None:
            return

        name = s["name"]
        slug = name.replace(" ", "_").lower()
        self.publish(f"{self.base_topic}/select/{slug}", selected_name, retain=True)

        if self._terminal_output_enabled():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.publish(self.terminal_output_topic, f'{ts}: Select "{name}" set to "{selected_name}"')

        cmd = (matched_opt.get("cmd") or "").strip()
        if cmd:
            def _run(c=cmd, wtt=write_to_terminal):
                with self.busy():
                    output = run_cmd(c)
                if wtt and output and self._terminal_output_enabled():
                    for line in output.split("\n"):
                        if line:
                            self.publish(self.terminal_output_topic, line)
            threading.Thread(target=_run, daemon=True).start()
