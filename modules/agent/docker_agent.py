import json
import os
import threading
import time

from .shared import run_cmd, slugify as _slug
from modules.docker_monitor import (
    compose_containers,
    inspect_containers,
    container_stats,
    image_is_updatable,
    local_image_digest,
    remote_image_digest,
)


def _short_digest(digest):
    if not digest:
        return "unknown"
    d = str(digest)
    if d.startswith("sha256:"):
        d = d[7:]
    return d[:12]


class DockerMixin:
    def init_docker(self):
        cfg = self.config.get("docker", {}) or {}

        compose_files = cfg.get("compose_files")
        if isinstance(compose_files, str):
            compose_files = [compose_files]
        elif not isinstance(compose_files, list):
            compose_files = ["/container-data/compose.yaml"]

        self._docker_compose_files = [str(p) for p in compose_files if p]
        self._docker_prev_restart = {}
        self._docker_known_images = set()
        self._docker_image_meta = {}
        self._docker_image_update_state = {}
        self._docker_image_last_digests = {}
        self._docker_installing = set()
        self._docker_last_image_check = 0.0
        self._docker_stat_prev = {}
        self._docker_stat_known = set()
        self._docker_stat_meta = {}
        self._docker_last_stats = 0.0

    def _docker_has_compose_file(self):
        return any(os.path.isfile(p) for p in getattr(self, "_docker_compose_files", []))

    def register_docker(self):
        cfg = self.config.get("docker", {}) or {}
        active = bool(cfg.get("enabled", False)) and self._docker_has_compose_file()

        self._binary_sensor_discovery(
            "docker_enabled",
            cfg.get("enabled_sensor_name", "Docker monitoring enabled"),
            f"{self.base_topic}/docker/enabled",
            icon="mdi:docker",
            entity_category="diagnostic",
        )
        self.publish(f"{self.base_topic}/docker/enabled", "ON" if active else "OFF")

        if not active:
            return

        base = f"{self.base_topic}/docker"

        self._sensor_discovery(
            "docker_containers_up",
            cfg.get("containers_up_sensor_name", "Docker containers up"),
            f"{base}/containers_up",
            unit="containers",
            icon="mdi:docker",
            state_class="measurement",
            entity_category="diagnostic",
        )

        self._binary_sensor_discovery(
            "docker_containers_error",
            cfg.get("containers_error_sensor_name", "Docker containers with errors"),
            f"{base}/containers_error",
            device_class="problem",
            icon="mdi:docker",
            attributes_topic=f"{base}/containers_error_attributes",
            entity_category="diagnostic",
        )

        for image_ref in sorted(self._docker_known_images):
            self._register_docker_image(image_ref)

        scfg = cfg.get("stats", {}) or {}
        for cname in sorted(self._docker_stat_meta.values()):
            self._stat_discovery_for(cname, scfg)

    def _remember_docker_image(self, image_ref, compose_file, services):
        slug = _slug(image_ref)
        meta = self._docker_image_meta.setdefault(slug, {})
        meta["ref"] = image_ref
        if compose_file:
            meta["compose_file"] = compose_file
        if services:
            meta["services"] = services

    def _register_docker_image(self, image_ref):
        cfg = self.config.get("docker", {}) or {}
        slug = _slug(image_ref)
        self._docker_known_images.add(image_ref)

        prefix = cfg.get("image_update_prefix", "") or ""
        base = f"{self.base_topic}/docker/update/{slug}"

        command_topic = None
        if cfg.get("allow_install", False):
            command_topic = f"{base}/set"

        self._update_discovery(
            f"docker_image_{slug}_update",
            f"{prefix}{image_ref}",
            f"{base}/state",
            command_topic=command_topic,
            icon="mdi:docker",
            entity_category="diagnostic",
        )

        if slug not in self._docker_installing:
            self._set_docker_image_progress(f"{self.base_topic}/docker", slug, False)

    def _docker_container_errors(self, name, info):
        cfg = self.config.get("docker", {}) or {}
        threshold = int(cfg.get("restart_loop_threshold", 3))

        reasons = []

        health = info.get("health", "")
        status = info.get("status", "") or info.get("state", "")
        exit_code = info.get("exit_code")
        restart_count = int(info.get("restart_count", 0) or 0)

        if health == "unhealthy":
            reasons.append("unhealthy")

        if status == "restarting" or info.get("restarting"):
            reasons.append("restarting")

        if info.get("oom_killed"):
            reasons.append("oom_killed")

        if status == "exited" and exit_code not in (0, None):
            reasons.append("bad_exit")

        prev = self._docker_prev_restart.get(name, restart_count)
        looped = (restart_count - prev >= threshold) or (
            status == "restarting" and restart_count >= threshold
        )
        if looped:
            reasons.append("restart_loop")

        self._docker_prev_restart[name] = restart_count

        return reasons

    def docker_loop(self):
        cfg = self.config.get("docker", {}) or {}
        if not cfg.get("enabled", False):
            return

        interval = float(cfg.get("update_interval", 60))
        image_interval = float(cfg.get("image_check_interval", 3600))
        docker_bin = cfg.get("docker_binary", "docker")
        base = f"{self.base_topic}/docker"

        while not self._stop_event.is_set():
            if not self._docker_has_compose_file():
                self._stop_event.wait(timeout=interval)
                continue

            merged_conts = []
            try:
                conts = compose_containers(self._docker_compose_files, docker_bin)
                info = inspect_containers([c["id"] for c in conts], docker_bin)

                merged_conts = [dict(c, **info.get(c["name"], {})) for c in conts]

                running = sum(
                    1 for c in merged_conts
                    if (c.get("status") or c.get("state")) == "running"
                )

                bad = {}
                for c in merged_conts:
                    reasons = self._docker_container_errors(c["name"], c)
                    if reasons:
                        bad[c["name"]] = reasons

                self.publish(f"{base}/containers_up", running)
                self.publish(f"{base}/containers_error", "ON" if bad else "OFF")
                self.publish(
                    f"{base}/containers_error_attributes",
                    json.dumps({
                        "count": len(bad),
                        "total": len(merged_conts),
                        "containers": [{"name": n, "reasons": r} for n, r in bad.items()],
                    }),
                )
            except Exception:
                pass

            now = time.time()
            if now - self._docker_last_image_check >= image_interval:
                found = False
                try:
                    found = self._docker_check_images(merged_conts, docker_bin, base)
                except Exception:
                    found = False
                self._docker_last_image_check = now if found else now - image_interval + min(image_interval, 30.0)

            scfg = cfg.get("stats", {}) or {}
            if self._docker_stats_active(scfg):
                stats_interval = float(scfg.get("update_interval", 30))
                if now - self._docker_last_stats >= stats_interval:
                    self._docker_last_stats = now
                    try:
                        self._docker_publish_stats(merged_conts, scfg, docker_bin, base)
                    except Exception:
                        pass

            self._stop_event.wait(timeout=interval)

    def _docker_stats_active(self, scfg):
        if scfg.get("enabled", False):
            return True
        return any(
            isinstance(v, dict) and v.get("enabled", False)
            for v in (scfg.get("containers", {}) or {}).values()
        )

    def _docker_stats_enabled_for(self, names, scfg):
        overrides = scfg.get("containers", {}) or {}
        for n in names:
            ov = overrides.get(n)
            if isinstance(ov, dict) and "enabled" in ov:
                return bool(ov["enabled"])
        return bool(scfg.get("enabled", False))

    def _stat_discovery_for(self, cname, scfg):
        slug = _slug(cname)
        sbase = f"{self.base_topic}/docker/stats/{slug}"

        metrics = []
        if scfg.get("cpu", True):
            metrics.append(("cpu", "CPU", "%", "mdi:chip"))
        if scfg.get("memory", True):
            metrics.append(("mem_mb", "Memory", "MB", "mdi:memory"))
            metrics.append(("mem_pct", "Memory %", "%", "mdi:memory"))
        if scfg.get("network", True):
            metrics.append(("net_rx", "Net in", "MB/s", "mdi:download-network"))
            metrics.append(("net_tx", "Net out", "MB/s", "mdi:upload-network"))
        if scfg.get("disk", True):
            metrics.append(("blk_read", "Disk read", "MB/s", "mdi:harddisk"))
            metrics.append(("blk_write", "Disk write", "MB/s", "mdi:harddisk"))

        for key, label, unit, icon in metrics:
            self._sensor_discovery(
                f"docker_stat_{slug}_{key}",
                f"{cname} {label}",
                f"{sbase}/{key}",
                unit=unit,
                icon=icon,
                state_class="measurement",
            )

    def _register_docker_stat_container(self, cname, scfg):
        slug = _slug(cname)
        if slug in self._docker_stat_known:
            return
        self._docker_stat_known.add(slug)
        self._docker_stat_meta[slug] = cname
        self._stat_discovery_for(cname, scfg)

    def _mbps(self, cur, old, dt):
        rate = (cur - old) / dt / 1_000_000
        return round(rate, 3) if rate > 0 else 0.0

    def _docker_publish_stats(self, merged_conts, scfg, docker_bin, base):
        targets = {}
        for c in merged_conts:
            cname = c.get("name") or c.get("service") or ""
            if not cname or not c.get("id"):
                continue
            if (c.get("status") or c.get("state")) != "running":
                continue
            if not self._docker_stats_enabled_for([cname, c.get("service", "")], scfg):
                continue
            targets[c["id"]] = cname

        if not targets:
            return

        rows = container_stats(list(targets), docker_bin)
        now = time.time()

        fam_cpu = bool(scfg.get("cpu", True))
        fam_mem = bool(scfg.get("memory", True))
        fam_net = bool(scfg.get("network", True))
        fam_disk = bool(scfg.get("disk", True))

        for st in rows:
            cname = st["name"]
            slug = _slug(cname)
            self._register_docker_stat_container(cname, scfg)
            sbase = f"{base}/stats/{slug}"

            if fam_cpu and st["cpu_pct"] is not None:
                self.publish(f"{sbase}/cpu", st["cpu_pct"])

            if fam_mem:
                self.publish(f"{sbase}/mem_mb", round(st["mem_used_bytes"] / 1_000_000, 2))
                if st["mem_pct"] is not None:
                    self.publish(f"{sbase}/mem_pct", st["mem_pct"])

            if fam_net or fam_disk:
                prev = self._docker_stat_prev.get(cname)
                self._docker_stat_prev[cname] = (
                    st["net_rx_bytes"], st["net_tx_bytes"],
                    st["blk_read_bytes"], st["blk_write_bytes"], now,
                )
                if prev:
                    dt = max(now - prev[4], 0.001)
                    if fam_net:
                        self.publish(f"{sbase}/net_rx", self._mbps(st["net_rx_bytes"], prev[0], dt))
                        self.publish(f"{sbase}/net_tx", self._mbps(st["net_tx_bytes"], prev[1], dt))
                    if fam_disk:
                        self.publish(f"{sbase}/blk_read", self._mbps(st["blk_read_bytes"], prev[2], dt))
                        self.publish(f"{sbase}/blk_write", self._mbps(st["blk_write_bytes"], prev[3], dt))

    def _docker_check_images(self, merged_conts, docker_bin, base):
        refs = {}
        for c in merged_conts:
            ref = c.get("image_ref") or c.get("image", "")
            if not ref:
                continue
            entry = refs.setdefault(ref, {"repo_digests": [], "compose_file": "", "services": []})
            if not entry["repo_digests"] and c.get("repo_digests"):
                entry["repo_digests"] = c.get("repo_digests", [])
            if not entry["compose_file"] and c.get("compose_file"):
                entry["compose_file"] = c.get("compose_file", "")
            svc = c.get("service", "")
            if svc and svc not in entry["services"]:
                entry["services"].append(svc)

        for ref in sorted(refs):
            if not image_is_updatable(ref):
                continue

            info = refs[ref]
            self._remember_docker_image(ref, info["compose_file"], info["services"])

            if ref not in self._docker_known_images:
                self._register_docker_image(ref)

            slug = _slug(ref)
            local = local_image_digest(info["repo_digests"], ref)
            remote = remote_image_digest(ref, docker_bin)

            if local and remote and local != remote:
                remote = remote_image_digest(ref, docker_bin) or remote

            self._publish_docker_image_state(base, slug, ref, local, remote)

        return bool(refs)

    def _publish_docker_image_state(self, base, slug, ref, local, remote, in_progress=None):
        self._docker_image_last_digests[slug] = (ref, local, remote)

        installed = _short_digest(local)
        update_available = bool(local and remote and local != remote)

        if local and remote:
            latest = _short_digest(remote) if update_available else installed
        else:
            latest = installed

        state = {
            "installed_version": installed,
            "latest_version": latest,
            "title": ref,
        }

        if update_available:
            state["release_summary"] = (
                f"Newer image available in registry.\nLocal: {local}\nRegistry: {remote}"
            )
        elif not (local and remote):
            state["release_summary"] = "Registry version check unavailable."

        if in_progress is not None:
            state["in_progress"] = in_progress

        self._docker_image_update_state[slug] = update_available
        self.publish(f"{base}/update/{slug}/state", json.dumps(state))

    def _set_docker_image_progress(self, base, slug, in_progress):
        cached = self._docker_image_last_digests.get(slug)
        if not cached:
            return
        ref, local, remote = cached
        self._publish_docker_image_state(base, slug, ref, local, remote, in_progress=in_progress)

    def docker_update_count(self):
        return sum(1 for v in self._docker_image_update_state.values() if v)

    def handle_docker_update_install(self, topic):
        cfg = self.config.get("docker", {}) or {}
        if not cfg.get("enabled", False) or not cfg.get("allow_install", False):
            return

        try:
            slug = topic.split("/docker/update/", 1)[1].rsplit("/set", 1)[0]
        except Exception:
            return

        meta = self._docker_image_meta.get(slug)
        if not meta:
            return

        compose_file = meta.get("compose_file", "")
        if not compose_file or slug in self._docker_installing:
            return

        docker_bin = cfg.get("docker_binary", "docker")
        services = " ".join(meta.get("services") or [])
        if services:
            cmd = (
                f'{docker_bin} compose -f "{compose_file}" pull {services} && '
                f'{docker_bin} compose -f "{compose_file}" up -d {services}'
            )
        else:
            cmd = (
                f'{docker_bin} compose -f "{compose_file}" pull && '
                f'{docker_bin} compose -f "{compose_file}" up -d'
            )

        self._docker_installing.add(slug)
        threading.Thread(
            target=self._run_docker_install,
            args=(cmd, slug, meta.get("ref", ""), docker_bin),
            daemon=True,
        ).start()

    def _run_docker_install(self, cmd, slug, ref, docker_bin):
        base = f"{self.base_topic}/docker"
        self._set_docker_image_progress(base, slug, True)
        try:
            with self.busy(f"docker update: {ref or slug}"):
                out = run_cmd(cmd)
            if self._terminal_output_enabled() and out:
                for line in out.splitlines():
                    if line:
                        self.publish(self.terminal_output_topic, line)

            conts = compose_containers(self._docker_compose_files, docker_bin)
            info = inspect_containers([c["id"] for c in conts], docker_bin)
            merged = [dict(c, **info.get(c["name"], {})) for c in conts]

            digests = []
            for c in merged:
                if (c.get("image_ref") or c.get("image", "")) == ref:
                    digests = c.get("repo_digests", [])
                    break

            local = local_image_digest(digests, ref)
            remote = remote_image_digest(ref, docker_bin)
            self._publish_docker_image_state(base, slug, ref, local, remote, in_progress=False)
        except Exception:
            self._set_docker_image_progress(base, slug, False)
        finally:
            self._docker_installing.discard(slug)
