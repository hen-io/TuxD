import json
import os
import shutil
import subprocess


def _run(args):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except Exception:
        return None


def _split_bin(docker_bin):
    parts = str(docker_bin or "docker").split()
    return parts if parts else ["docker"]


def _parse_ps_output(raw):
    out = []
    if not raw:
        return out

    stripped = raw.strip()
    if not stripped:
        return out

    try:
        data = json.loads(stripped)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            return [data]
    except Exception:
        pass

    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue

    return out


def _normalize_ps_entry(entry, compose_file):
    health = str(entry.get("Health") or "").lower()
    state = str(entry.get("State") or "").lower()

    exit_code = entry.get("ExitCode")
    try:
        exit_code = int(exit_code)
    except Exception:
        exit_code = None

    return {
        "id": entry.get("ID") or entry.get("Id") or "",
        "name": entry.get("Name") or "",
        "service": entry.get("Service") or "",
        "image": entry.get("Image") or "",
        "state": state,
        "health": health,
        "exit_code": exit_code,
        "compose_file": compose_file,
    }


def compose_containers(compose_files, docker_bin="docker"):
    base = _split_bin(docker_bin)
    containers = []
    seen = set()

    for path in compose_files or []:
        if not path or not os.path.isfile(path):
            continue

        raw = _run(base + ["compose", "-f", path, "ps", "--format", "json", "--all"])
        for entry in _parse_ps_output(raw):
            norm = _normalize_ps_entry(entry, path)
            key = norm["id"] or norm["name"]
            if not key or key in seen:
                continue
            seen.add(key)
            containers.append(norm)

    return containers


def inspect_containers(ids, docker_bin="docker"):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {}

    base = _split_bin(docker_bin)
    raw = _run(base + ["inspect"] + ids)
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        return {}

    result = {}
    for item in data:
        if not isinstance(item, dict):
            continue

        name = str(item.get("Name") or "").lstrip("/")
        state = item.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        config = item.get("Config") or {}

        try:
            restart_count = int(item.get("RestartCount"))
        except Exception:
            restart_count = 0

        try:
            exit_code = int(state.get("ExitCode"))
        except Exception:
            exit_code = None

        result[name] = {
            "status": str(state.get("Status") or "").lower(),
            "restarting": bool(state.get("Restarting")),
            "health": str(health or "").lower(),
            "exit_code": exit_code,
            "oom_killed": bool(state.get("OOMKilled")),
            "restart_count": restart_count,
            "image_ref": config.get("Image") or "",
            "repo_digests": item.get("RepoDigests") or [],
        }

    return result


_SIZE_UNITS = {
    "B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4, "PB": 1000 ** 5,
    "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4, "PIB": 1024 ** 5,
    "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4,
}


def _parse_pct(s):
    try:
        return round(float(str(s).strip().rstrip("%")), 2)
    except Exception:
        return None


def _parse_size(s):
    s = str(s).strip()
    if not s or s.lower() in ("--", "n/a"):
        return 0.0

    num = ""
    unit = ""
    for ch in s:
        if ch.isdigit() or ch in ".-+":
            num += ch
        elif not ch.isspace():
            unit += ch

    try:
        val = float(num)
    except Exception:
        return 0.0

    return val * _SIZE_UNITS.get(unit.upper(), 1)


def _parse_pair(s):
    parts = str(s).split("/")
    if len(parts) != 2:
        return (0.0, 0.0)
    return (_parse_size(parts[0]), _parse_size(parts[1]))


def container_stats(ids, docker_bin="docker"):
    ids = [i for i in (ids or []) if i]
    if not ids:
        return []

    base = _split_bin(docker_bin)
    raw = _run(base + ["stats", "--no-stream", "--format", "{{json .}}"] + ids)

    out = []
    for entry in _parse_ps_output(raw):
        name = entry.get("Name") or entry.get("Container") or ""
        if not name:
            continue

        net_rx, net_tx = _parse_pair(entry.get("NetIO", ""))
        blk_read, blk_write = _parse_pair(entry.get("BlockIO", ""))
        mem_used, _mem_limit = _parse_pair(entry.get("MemUsage", ""))

        out.append({
            "name": name,
            "cpu_pct": _parse_pct(entry.get("CPUPerc")),
            "mem_used_bytes": mem_used,
            "mem_pct": _parse_pct(entry.get("MemPerc")),
            "net_rx_bytes": net_rx,
            "net_tx_bytes": net_tx,
            "blk_read_bytes": blk_read,
            "blk_write_bytes": blk_write,
        })

    return out


def _repo_of(ref):
    name = ref.rsplit("@", 1)[0]
    slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > slash:
        name = name[:colon]
    return name


def image_is_updatable(image_ref):
    ref = str(image_ref or "").strip()
    if not ref or ref == "<none>":
        return False
    if ref.startswith("sha256:"):
        return False
    if "@sha256:" in ref:
        return False
    return True


def local_image_digest(repo_digests, image_ref):
    if not repo_digests:
        return None

    target_repo = _repo_of(str(image_ref or ""))

    for entry in repo_digests:
        if "@" not in entry:
            continue
        repo, digest = entry.split("@", 1)
        if not digest.startswith("sha256:"):
            continue
        if target_repo and repo != target_repo:
            continue
        return digest

    first = repo_digests[0]
    if "@" in first:
        return first.split("@", 1)[1]

    return None


def remote_image_digest(image_ref, docker_bin="docker"):













    ref = str(image_ref or "").strip()
    if not ref or not shutil.which("skopeo"):
        return None

    raw = _run(["skopeo", "inspect", "--format", "{{.Digest}}", f"docker://{ref}"])
    if not raw:
        return None

    digest = raw.strip()
    return digest if digest.startswith("sha256:") else None
