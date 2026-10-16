import os
import sys
import threading
import time

from modules import proxmox as pve

from ..shared import item_name
from .base import Action, Integration

_GUEST_VERBS = {
    "start": ("Start", "mdi:play", False),
    "shutdown": ("Shut down", "mdi:power", True),
    "reboot": ("Reboot", "mdi:restart", True),
    "stop": ("Stop (forced)", "mdi:stop", True),
    "snapshot": ("Snapshot", "mdi:camera-outline", False),
}


class ProxmoxIntegration(Integration):
    KEY = "proxmox"
    NAME = "Proxmox VE"
    DEFAULT_INTERVAL = 30

    def __init__(self, agent):
        super().__init__(agent)
        self._guests = {}
        self._announced = set()
        self._cancel = threading.Event()
        self._job = ""
        self._cancellable = False
        self._units = {}
        self._error = ""
        self._lock = threading.Lock()

    def enabled(self):
        return super().enabled() and pve.is_proxmox()

    def _guest_sensors(self):
        return bool(self.cfg.get("guest_sensors", True))

    def register(self):
        self.agent._sensor_discovery(
            "proxmox_guests",
            "Proxmox guests running",
            f"{self.base_topic}/guests",
            icon="mdi:server-network",
            attributes_topic=f"{self.base_topic}/guests/attributes",
            ha_object_id=f"{self.agent.device_slug}_proxmox_guests",
            state_class="measurement",
        )
        self.agent._sensor_discovery(
            "proxmox_job",
            "Proxmox bulk job",
            f"{self.base_topic}/job",
            icon="mdi:format-list-numbered",
            ha_object_id=f"{self.agent.device_slug}_proxmox_job",
            entity_category="diagnostic",
        )
        self._publish_job()
        self._announced = set()
        if hasattr(self.agent, "forget_stack_alerts"):
            self.agent.forget_stack_alerts("proxmox")
        try:
            self._units = pve.sync_units(self.cfg, sys.executable, os.getcwd())
        except Exception as e:
            self._units = {"start": f"error: {e}", "stop": f"error: {e}"}
        self.poll()

    def _guest_topic(self, vmid):
        return f"{self.base_topic}/guest/{vmid}"

    def _announce(self, g):
        vmid = g["vmid"]
        self.agent._sensor_discovery(
            f"proxmox_guest_{vmid}",
            item_name(self.agent.device_slug, g["name"], f"({vmid})", "Proxmox"),
            f"{self._guest_topic(vmid)}/state",
            icon="mdi:monitor" if g["kind"] == "qemu" else "mdi:cube-outline",
            attributes_topic=f"{self._guest_topic(vmid)}/attributes",
            ha_object_id=f"{self.agent.device_slug}_proxmox_{vmid}",
        )
        self._announced.add(vmid)

    def poll(self):
        try:
            guests = pve.read_guests()
            self._error = ""
        except Exception as e:
            self._error = str(e)[:250]
            guests = self._guests
        renamed = {v for v, g in guests.items() if v in self._guests and self._guests[v]["name"] != g["name"]}
        self._guests = guests

        wanted = set(guests) if self._guest_sensors() else set()
        for vmid in self._announced - wanted:
            self.agent.publish(self.agent._discovery_topic("sensor", f"proxmox_guest_{vmid}"), "", retain=True)
            for suffix in ("state", "attributes"):
                getattr(self.agent, "state_cache", {}).pop(f"{self._guest_topic(vmid)}/{suffix}", None)
        self._announced &= wanted
        for vmid in sorted(wanted):
            if vmid not in self._announced or vmid in renamed:
                self._announce(guests[vmid])
            g = guests[vmid]
            self.publish_changed(f"{self._guest_topic(vmid)}/state", g["status"])
            self.publish_changed(f"{self._guest_topic(vmid)}/attributes", {
                "vmid": vmid, "name": g["name"], "type": "VM" if g["kind"] == "qemu" else "container",
                "start_at_boot": g["onboot"], "startup_order": g["order"],
                "cpu_pct": round(float(g["cpu"] or 0) * 100, 1),
                "memory_mb": round((g["mem"] or 0) / 1048576), "memory_max_mb": round((g["maxmem"] or 0) / 1048576),
                "uptime_s": g["uptime"] or 0, "template": g["template"],
            })

        running = sum(1 for g in guests.values() if g["status"] == "running")
        self.publish_changed(f"{self.base_topic}/guests", str(running))
        self.publish_changed(f"{self.base_topic}/guests/attributes", {
            "total": len(guests),
            "guests": [{"vmid": v, "name": g["name"], "type": g["kind"], "status": g["status"], "start_at_boot": g["onboot"]}
                       for v, g in sorted(guests.items())],
            "bulk_start_order": self._order_state("start"),
            "bulk_stop_order": self._order_state("stop"),
            "error": self._error,
        })

        if not self._job and hasattr(self.agent, "sync_stack_alerts"):
            self.agent.sync_stack_alerts(
                "proxmox",
                {str(v): g["onboot"] and not g["template"] and g["status"] != "running" for v, g in guests.items()},
                labels={str(v): f"{g['name']} ({v})" for v, g in guests.items()},
            )

    def _order_state(self, job):
        if not pve.job_settings(self.cfg, job)[0]:
            return "Proxmox built-in"
        unit = self._units.get(job, "")
        return "on, at " + ("boot" if job == "start" else "shutdown") if unit == "installed" else f"on (unit: {unit or 'unknown'})"

    def _publish_job(self):
        self.agent.publish(f"{self.base_topic}/job", self._job or self.tr("Idle"))

    def _label(self, vmid):
        g = self._guests.get(vmid)
        return f"{vmid} {g['name']}" if g else str(vmid)

    def _vmid(self, target):
        try:
            return int(str(target).split()[0])
        except (ValueError, IndexError):
            raise ValueError("Unknown guest")

    def actions(self):
        actions = [
            Action("bulk_start", "Bulk start guests", "mdi:play-box-multiple-outline", confirm=True),
            Action("bulk_stop", "Bulk stop guests", "mdi:stop-circle-outline", confirm=True),
        ]
        if self._job and self._cancellable:
            actions.append(Action("cancel_job", "Cancel bulk job", "mdi:cancel"))
        targets = [self._label(v) for v, g in sorted(self._guests.items()) if not g["template"]]
        if targets:
            actions += [Action(verb, name, icon, targets=targets, target_name="Guest", confirm=confirm)
                        for verb, (name, icon, confirm) in _GUEST_VERBS.items()]
        return actions

    def run(self, action_id, target):
        if action_id == "cancel_job":
            self._cancel.set()
            return
        if action_id in ("bulk_start", "bulk_stop"):
            self._bulk(action_id[5:])
            return
        vmid = self._vmid(target)
        g = self._guests.get(vmid)
        if action_id not in _GUEST_VERBS or g is None:
            raise ValueError("Unknown action or guest")
        name = self.tr(_GUEST_VERBS[action_id][0])
        with self.agent.busy(f"proxmox: {name} {self._label(vmid)}"):
            rc, out = pve.guest_command(vmid, g["kind"], action_id)
        self._say(f"Proxmox {name} {self._label(vmid)}: " + ("done" if rc == 0 else f"failed - {out}"))
        self.poll()
        self.agent.publish_action_catalog()

    def _say(self, line):
        if self.agent._terminal_output_enabled():
            self.agent.publish(self.agent.terminal_output_topic, line)

    def _bulk(self, job, lock_wait=0):
        with self._lock:
            if self._job:
                raise RuntimeError("A Proxmox bulk job is already running")
            self._job = self.tr("Bulk start") if job == "start" else self.tr("Bulk stop")
        self._cancel.clear()
        self._cancellable = pve.job_settings(self.cfg, job)[0]
        self._publish_job()
        self.agent.publish_action_catalog()

        def progress(text):
            self._job = text or self._job
            self._publish_job()
        try:
            with self.agent.busy(f"proxmox: {self._job}"):
                pve.BulkJob(job, self.cfg, log=self._say, progress=progress, cancel=self._cancel).run(lock_wait=lock_wait)
        finally:
            self._job = ""
            self._publish_job()
            self.agent.publish_action_catalog()
            self.poll()

    def before_power(self, kind):
        if not pve.job_settings(self.cfg, "stop")[0]:
            return
        deadline = time.monotonic() + pve.max_runtime(self.cfg, "stop")
        while self._job and time.monotonic() < deadline:
            time.sleep(1)
        self._say(f"Proxmox: bulk stop before {kind}")
        try:
            self._bulk("stop", lock_wait=pve.max_runtime(self.cfg, "stop"))
        except RuntimeError as e:
            self._say(f"[{e}]")
