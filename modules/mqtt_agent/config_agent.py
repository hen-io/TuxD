import os
import sys
import json
import yaml
import threading
import time
import datetime

from .base import slugify as _slug


_FLOAT_OPTS = {"min": 0.1, "max": 86400.0, "step": 0.1}

_CONFIG_FILE = "tuxd.conf"


class ConfigAgentMixin:
    def _config_entity_id(self, domain, key):
        return f"{domain}.{self.device_slug}_config_{key}"

    def init_config_numbers(self):
        self._config_numbers = {}
        self._config_num_cmd_topics = {}
        self._cfgnum_restart_timer = None
        self._cfgnum_restart_lock = threading.Lock()

    def _collect_update_intervals(self, cfg):
        result = {}

        def add(key, name, value, path_info, opts=None):
            oid = f"cfgnum_{key}"
            merged_opts = {**_FLOAT_OPTS, **(opts or {})}
            is_float = isinstance(merged_opts["step"], float) and merged_opts["step"] < 1
            stored = round(float(value), 2) if is_float else int(value)
            result[oid] = {
                "name": name,
                "value": stored,
                "is_float": is_float,
                "opts": merged_opts,
                "path_info": path_info,
            }

        for cname, cval in (cfg.get("components") or {}).items():
            if isinstance(cval, dict) and cval.get("enabled", False) and "update_interval" in cval:
                add(
                    f"components_{_slug(cname)}",
                    f"{cname.replace('_', ' ').title()} Update Interval",
                    cval["update_interval"],
                    {"type": "dict", "keys": ["components", cname, "update_interval"]},
                )

        lag = cfg.get("lag-monitor") or {}
        if lag.get("enabled", False) and "update_interval" in lag:
            add(
                "lag_monitor",
                "Lag Monitor Update Interval",
                lag["update_interval"],
                {"type": "dict", "keys": ["lag-monitor", "update_interval"]},
            )

        dock = cfg.get("docker") or {}
        if dock.get("enabled", False) and "update_interval" in dock:
            add(
                "docker",
                "Docker Monitoring Update Interval",
                dock["update_interval"],
                {"type": "dict", "keys": ["docker", "update_interval"]},
            )

        dstats = dock.get("stats") or {}
        if dstats.get("enabled", False) and "update_interval" in dstats:
            add(
                "docker_stats",
                "Docker Stats Update Interval",
                dstats["update_interval"],
                {"type": "dict", "keys": ["docker", "stats", "update_interval"]},
            )

        hup = cfg.get("host_update") or {}
        if hup.get("enabled", False) and "update_interval" in hup:
            add(
                "host_update",
                "Host Update Check Interval",
                hup["update_interval"],
                {"type": "dict", "keys": ["host_update", "update_interval"]},
            )

        for skey, sval in ((cfg.get("commands") or {}).get("status") or {}).items():
            if isinstance(sval, dict) and sval.get("enabled", False) and "update_interval" in sval:
                add(
                    f"status_{_slug(skey)}",
                    f"{skey.replace('_', ' ').title()} Status Update Interval",
                    sval["update_interval"],
                    {"type": "dict", "keys": ["commands", "status", skey, "update_interval"]},
                )

        for s in (cfg.get("sensor") or []):
            if not isinstance(s, dict) or "update_interval" not in s:
                continue
            name = str(s.get("name") or "")
            if not name:
                continue
            add(
                f"sensor_{_slug(name)}",
                f"{name} Update Interval",
                s["update_interval"],
                {"type": "list", "section": "sensor", "field": "name", "match": name, "key": "update_interval"},
            )

        for n in (cfg.get("network") or []):
            if not isinstance(n, dict) or "update_interval" not in n:
                continue
            iface = str(n.get("iface") or "")
            if not iface:
                continue
            add(
                f"network_{_slug(iface)}",
                f"{iface} Update Interval",
                n["update_interval"],
                {"type": "list", "section": "network", "field": "iface", "match": iface, "key": "update_interval"},
            )

        for d in (cfg.get("disk") or []):
            if not isinstance(d, dict) or "update_interval" not in d:
                continue
            name = str(d.get("name") or "")
            if not name:
                continue
            add(
                f"disk_{_slug(name)}",
                f"{name} Disk Update Interval",
                d["update_interval"],
                {"type": "list", "section": "disk", "field": "name", "match": name, "key": "update_interval"},
            )

        for i, t in enumerate(cfg.get("tasks") or []):
            if not isinstance(t, dict) or "update_interval" not in t:
                continue
            cmd = str(t.get("cmd") or "")
            add(
                f"task_{i}",
                f"Task \"{cmd}\" Update Interval",
                t["update_interval"],
                {"type": "list_by_index", "section": "tasks", "index": i, "key": "update_interval"},
            )

        for lkey, lval in ((cfg.get("lm_sensors") or {}).get("sensors") or {}).items():
            if isinstance(lval, dict) and lval.get("enabled", False) and "update_interval" in lval:
                add(
                    f"lm_{_slug(lkey)}",
                    f"{lkey.replace('_', ' ').title()} Update Interval",
                    lval["update_interval"],
                    {"type": "dict", "keys": ["lm_sensors", "sensors", lkey, "update_interval"]},
                )

        term_out = (cfg.get("terminal") or {}).get("terminal_output") or {}
        if isinstance(term_out, dict) and term_out.get("enabled", True) and "post_interval" in term_out:
            add(
                "terminal_post_interval",
                "Terminal Output Post Interval",
                term_out["post_interval"],
                {"type": "dict", "keys": ["terminal", "terminal_output", "post_interval"]},
                opts={"min": 0.1, "max": 3.0, "step": 0.1},
            )

        return result

    def register_config_numbers(self):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                fresh_cfg = yaml.safe_load(f) or {}
        except Exception:
            fresh_cfg = self.config

        self._config_numbers = self._collect_update_intervals(fresh_cfg)
        self._config_num_cmd_topics = {}

        for oid, entry in self._config_numbers.items():
            key = oid[len("cfgnum_"):]
            state_topic = f"{self.base_topic}/cfgnum/{key}"
            command_topic = f"{self.base_topic}/cfgnum/{key}/set"

            opts = entry["opts"]
            payload = {
                "name": entry["name"],
                "default_entity_id": self._config_entity_id("number", key),
                "state_topic": state_topic,
                "command_topic": command_topic,
                "unique_id": f"{self.config['device']['name']}_{oid}",
                "device": self.device_info,
                "entity_category": "config",
                "min": opts["min"],
                "max": opts["max"],
                "step": opts["step"],
                "mode": "box",
                "unit_of_measurement": "s",
                "icon": "mdi:timer-cog-outline",
            }

            self.publish(
                f"homeassistant/number/{self.config['device']['name']}/{oid}/config",
                json.dumps(payload),
                retain=True,
            )

            val = entry["value"]
            self.publish(state_topic, f"{val:.1f}" if entry["is_float"] else str(val), retain=True)
            self._config_num_cmd_topics[command_topic] = oid

    def handle_config_number(self, topic, payload):
        oid = self._config_num_cmd_topics.get(topic)
        if oid is None:
            return

        entry = self._config_numbers.get(oid)
        if entry is None:
            return

        try:
            raw = float(payload.strip())
        except (ValueError, TypeError):
            return

        min_val = entry["opts"]["min"]
        if raw < min_val:
            return

        new_value = round(raw, 2) if entry["is_float"] else int(raw)

        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            self._cfgnum_apply(cfg, entry["path_info"], new_value)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception:
            return

        key = oid[len("cfgnum_"):]
        val_str = f"{new_value:.1f}" if entry["is_float"] else str(new_value)
        self.publish(f"{self.base_topic}/cfgnum/{key}", val_str, retain=True)

        self._cfgnum_schedule_restart()

    def _cfgnum_apply(self, cfg, path_info, value):
        if path_info["type"] == "dict":
            d = cfg
            for k in path_info["keys"][:-1]:
                d = d[k]
            d[path_info["keys"][-1]] = value

        elif path_info["type"] == "list":
            for item in (cfg.get(path_info["section"]) or []):
                if isinstance(item, dict) and item.get(path_info["field"]) == path_info["match"]:
                    item[path_info["key"]] = value
                    return

        elif path_info["type"] == "list_by_index":
            lst = cfg.get(path_info["section"]) or []
            idx = path_info["index"]
            if idx < len(lst) and isinstance(lst[idx], dict):
                lst[idx][path_info["key"]] = value

    def _cfgnum_restart(self):
        try:
            if self._terminal_output_enabled():
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self.publish(self.terminal_output_topic, f"{ts}: Restarting TuxD...")
                time.sleep(0.5)
            self.set_error(True, "Configuration changed")
        except Exception:
            pass
        try:
            self.clear_discovery(timeout=5.0)
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _cfgnum_schedule_restart(self):
        with self._cfgnum_restart_lock:
            if self._cfgnum_restart_timer is not None:
                self._cfgnum_restart_timer.cancel()
            self._cfgnum_restart_timer = threading.Timer(5.0, self._cfgnum_restart)
            self._cfgnum_restart_timer.daemon = True
            self._cfgnum_restart_timer.start()

    def config_file_watch_loop(self):
        try:
            last_mtime = os.path.getmtime(_CONFIG_FILE)
        except OSError:
            last_mtime = None
        while not self._stop_event.wait(timeout=5.0):
            try:
                mtime = os.path.getmtime(_CONFIG_FILE)
            except OSError:
                continue
            if last_mtime is not None and mtime != last_mtime:
                self._cfgnum_schedule_restart()
            last_mtime = mtime

    def init_config_texts(self):
        self._config_texts = {}
        self._config_txt_cmd_topics = {}

    def _collect_config_texts(self, cfg):
        result = {}

        def add(key, name, value, path_info):
            oid = f"cfgtxt_{key}"
            result[oid] = {
                "name": name,
                "value": str(value),
                "path_info": path_info,
            }

        device = cfg.get("device") or {}
        if "extra_config_path" in device:
            add(
                "device_extra_config_path",
                "Extra Config Path",
                device["extra_config_path"],
                {"type": "dict", "keys": ["device", "extra_config_path"]},
            )

        hup = cfg.get("host_update") or {}
        if "install_button_name" in hup:
            add(
                "host_update_install_button_name",
                "Host Update Install Button Name",
                hup["install_button_name"],
                {"type": "dict", "keys": ["host_update", "install_button_name"]},
            )

        if "install_cmd" in hup:
            add(
                "host_update_install_cmd",
                "Host Update Install Command",
                hup["install_cmd"],
                {"type": "dict", "keys": ["host_update", "install_cmd"]},
            )

        for skey, sval in ((cfg.get("commands") or {}).get("status") or {}).items():
            if isinstance(sval, dict) and "cmd" in sval:
                add(
                    f"status_{_slug(skey)}_cmd",
                    f"{skey.replace('_', ' ').title()} Command",
                    sval["cmd"],
                    {"type": "dict", "keys": ["commands", "status", skey, "cmd"]},
                )

        return result

    def register_config_texts(self):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                fresh_cfg = yaml.safe_load(f) or {}
        except Exception:
            fresh_cfg = self.config

        self._config_texts = self._collect_config_texts(fresh_cfg)
        self._config_txt_cmd_topics = {}

        for oid, entry in self._config_texts.items():
            key = oid[len("cfgtxt_"):]
            state_topic = f"{self.base_topic}/cfgtxt/{key}"
            command_topic = f"{self.base_topic}/cfgtxt/{key}/set"

            self._text_discovery(
                oid,
                entry["name"],
                command_topic,
                icon="mdi:form-textbox",
                state_topic=state_topic,
                entity_category="config",
                ha_object_id=f"{self.device_slug}_config_{key}",
            )

            self.publish(state_topic, entry["value"], retain=True)
            self._config_txt_cmd_topics[command_topic] = oid

    def handle_config_text(self, topic, payload):
        oid = self._config_txt_cmd_topics.get(topic)
        if oid is None:
            return
        entry = self._config_texts.get(oid)
        if entry is None:
            return

        new_value = payload.strip()
        if not new_value:
            return

        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            self._cfgnum_apply(cfg, entry["path_info"], new_value)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception:
            return

        key = oid[len("cfgtxt_"):]
        self.publish(f"{self.base_topic}/cfgtxt/{key}", new_value, retain=True)

        self._cfgnum_schedule_restart()

    def init_config_switches(self):
        self._config_switches = {}
        self._config_sw_cmd_topics = {}

    def _collect_enabled_flags(self, cfg):
        result = {}

        def add(key, name, value, path_info):
            oid = f"cfgsw_{key}"
            result[oid] = {
                "name": name,
                "value": bool(value),
                "path_info": path_info,
            }

        for cname, cval in (cfg.get("components") or {}).items():
            if isinstance(cval, dict) and "enabled" in cval:
                add(
                    f"components_{_slug(cname)}",
                    f"{cname.replace('_', ' ').title()} State",
                    cval["enabled"],
                    {"type": "dict", "keys": ["components", cname, "enabled"]},
                )

        lag = cfg.get("lag-monitor") or {}
        if "enabled" in lag:
            add(
                "lag_monitor",
                "Lag Monitor State",
                lag["enabled"],
                {"type": "dict", "keys": ["lag-monitor", "enabled"]},
            )

        lm = cfg.get("lm_sensors") or {}
        if "enabled" in lm:
            add(
                "lm_sensors",
                "LM Sensors State",
                lm["enabled"],
                {"type": "dict", "keys": ["lm_sensors", "enabled"]},
            )

        dock = cfg.get("docker") or {}
        if "enabled" in dock:
            add(
                "docker",
                "Docker Monitoring State",
                dock["enabled"],
                {"type": "dict", "keys": ["docker", "enabled"]},
            )

        dstats = dock.get("stats") or {}
        if "enabled" in dstats:
            add(
                "docker_stats",
                "Docker Stats State",
                dstats["enabled"],
                {"type": "dict", "keys": ["docker", "stats", "enabled"]},
            )

        for metric, label in (
            ("cpu", "CPU"),
            ("memory", "Memory"),
            ("network", "Network"),
            ("disk", "Disk"),
        ):
            if metric in dstats:
                add(
                    f"docker_stats_{metric}",
                    f"Docker Stats {label} State",
                    dstats[metric],
                    {"type": "dict", "keys": ["docker", "stats", metric]},
                )

        hup = cfg.get("host_update") or {}
        if "enabled" in hup:
            add(
                "host_update",
                "Host Update State",
                hup["enabled"],
                {"type": "dict", "keys": ["host_update", "enabled"]},
            )

        if "allow_install" in dock:
            add(
                "docker_allow_install",
                "Docker Allow Install",
                dock["allow_install"],
                {"type": "dict", "keys": ["docker", "allow_install"]},
            )

        if "allow_install" in hup:
            add(
                "host_update_allow_install",
                "Host Update Allow Install",
                hup["allow_install"],
                {"type": "dict", "keys": ["host_update", "allow_install"]},
            )

        if "use_custom_install_cmd" in hup:
            add(
                "host_update_use_custom_install_cmd",
                "Host Update Use Custom Install Command",
                hup["use_custom_install_cmd"],
                {"type": "dict", "keys": ["host_update", "use_custom_install_cmd"]},
            )

        dev = cfg.get("device") or {}
        if "self_update_allow_install" in dev:
            add(
                "self_update_allow_install",
                "TuxD Allow Install",
                dev["self_update_allow_install"],
                {"type": "dict", "keys": ["device", "self_update_allow_install"]},
            )

        for skey, sval in (lm.get("sensors") or {}).items():
            if isinstance(sval, dict) and "enabled" in sval:
                add(
                    f"lm_{_slug(skey)}",
                    f"{skey.replace('_', ' ').title()} State",
                    sval["enabled"],
                    {"type": "dict", "keys": ["lm_sensors", "sensors", skey, "enabled"]},
                )

        for skey, sval in ((cfg.get("commands") or {}).get("status") or {}).items():
            if isinstance(sval, dict) and "enabled" in sval:
                add(
                    f"status_{_slug(skey)}",
                    f"{skey.replace('_', ' ').title()} State",
                    sval["enabled"],
                    {"type": "dict", "keys": ["commands", "status", skey, "enabled"]},
                )

        t_in = (cfg.get("terminal") or {}).get("terminal_input")
        if isinstance(t_in, dict) and "enabled" in t_in:
            add(
                "terminal_input",
                "Terminal Input State",
                t_in["enabled"],
                {"type": "dict", "keys": ["terminal", "terminal_input", "enabled"]},
            )

        t_out = (cfg.get("terminal") or {}).get("terminal_output")
        if isinstance(t_out, dict) and "enabled" in t_out:
            add(
                "terminal_output",
                "Terminal Output State",
                t_out["enabled"],
                {"type": "dict", "keys": ["terminal", "terminal_output", "enabled"]},
            )

        for i, t in enumerate(cfg.get("tasks") or []):
            if not isinstance(t, dict):
                continue
            cmd = str(t.get("cmd") or "")
            if "write_to_terminal" in t:
                add(
                    f"task_{i}_write_to_terminal",
                    f'Task "{cmd}" Write To Terminal',
                    t["write_to_terminal"],
                    {"type": "list_by_index", "section": "tasks", "index": i, "key": "write_to_terminal"},
                )
            if "log_on_run" in t:
                add(
                    f"task_{i}_log_on_run",
                    f'Task "{cmd}" Log On Run',
                    t["log_on_run"],
                    {"type": "list_by_index", "section": "tasks", "index": i, "key": "log_on_run"},
                )

        for b in (cfg.get("button") or []):
            if not isinstance(b, dict) or "write_to_terminal" not in b:
                continue
            name = str(b.get("name") or "")
            if not name:
                continue
            add(
                f"button_{_slug(name)}_write_to_terminal",
                f'"{name}" Write To Terminal',
                b["write_to_terminal"],
                {"type": "list", "section": "button", "field": "name", "match": name, "key": "write_to_terminal"},
            )

        return result

    def register_config_switches(self):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                fresh_cfg = yaml.safe_load(f) or {}
        except Exception:
            fresh_cfg = self.config

        self._config_switches = self._collect_enabled_flags(fresh_cfg)
        self._config_sw_cmd_topics = {}

        for oid, entry in self._config_switches.items():
            key = oid[len("cfgsw_"):]
            state_topic = f"{self.base_topic}/cfgsw/{key}"
            command_topic = f"{self.base_topic}/cfgsw/{key}/set"

            payload = {
                "name": entry["name"],
                "default_entity_id": self._config_entity_id("switch", key),
                "state_topic": state_topic,
                "command_topic": command_topic,
                "unique_id": f"{self.config['device']['name']}_{oid}",
                "device": self.device_info,
                "entity_category": "config",
                "icon": "mdi:toggle-switch-outline",
                "payload_on": "ON",
                "payload_off": "OFF",
            }

            self.publish(
                f"homeassistant/switch/{self.config['device']['name']}/{oid}/config",
                json.dumps(payload),
                retain=True,
            )

            state = "ON" if entry["value"] else "OFF"
            self.publish(state_topic, state, retain=True)
            self._config_sw_cmd_topics[command_topic] = oid

    def handle_config_switch(self, topic, payload):
        oid = self._config_sw_cmd_topics.get(topic)
        if oid is None:
            return
        entry = self._config_switches.get(oid)
        if entry is None:
            return

        payload = payload.strip().upper()
        if payload not in ("ON", "OFF"):
            return

        new_value = payload == "ON"

        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            self._cfgnum_apply(cfg, entry["path_info"], new_value)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception:
            return

        key = oid[len("cfgsw_"):]
        self.publish(f"{self.base_topic}/cfgsw/{key}", "ON" if new_value else "OFF", retain=True)

        self._cfgnum_schedule_restart()
