#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import time
import threading
from pathlib import Path
import yaml
import select
import signal
import atexit
import json
import tempfile
import tarfile
import urllib.request
import urllib.error
import ast
import datetime
import re

VERSION = "2.0.16"

CONFIG_PATH = "config.yaml"
RELEASES_DIR = "/mnt/storage/tuxd/TuxD/releases"
LOG_FILE_PATH = "tuxd.log"

UPDATE_MODE = "github"

UPDATE_STATUS_FILE = "update_status.log"

WEB_MANIFEST_URL = "https://updates.k93.rehab:1443/tuxd/manifest.json"
WEB_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 60

GITHUB_REPO = "hen-io/TuxD"
GITHUB_ASSET_SUFFIX = ".tar.gz"

FAILED_UPDATE_RETRY_SECONDS = 3600

EXIT_CONFIG_ERROR = 78

RESET = "\033[0m"
BOLD = "\033[1m"
ITALIC = "\033[3m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GRAY = "\033[90m"
WHITE = "\033[37m"
BRIGHT_BLUE = "\033[94m"
BRIGHT_CYAN = "\033[96m"

ESC = "\033["

_TTY_ENABLED = False
_LOG_FILE = None
_LOG_ROTATE_THREAD = None
_CLEANUP_DONE = False
_STATUS = None
_AGENT = None


def _log_rotate_loop():
    global _LOG_FILE
    while True:
        time.sleep(300)
        if _LOG_FILE is not None:
            try:
                _LOG_FILE.close()
            except Exception:
                pass
            try:
                _LOG_FILE = open(LOG_FILE_PATH, "w", encoding="utf-8", buffering=1)
            except Exception:
                _LOG_FILE = None


def log_write(text: str):
    global _LOG_FILE
    if _LOG_FILE is not None:
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _LOG_FILE.write(f"[{ts}] {text}\n")
            _LOG_FILE.flush()
        except Exception:
            pass


def c(text, *styles):
    return "".join(styles) + str(text) + RESET


def _isatty():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def get_term_cols(default=80):
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def get_term_lines(default=24):
    try:
        return os.get_terminal_size().lines
    except OSError:
        return default


def term_flush():
    try:
        sys.stdout.flush()
    except Exception:
        pass


def term_clear(enabled):
    if not enabled or not _isatty():
        return
    sys.stdout.write(ESC + "2J" + ESC + "H")
    term_flush()


def term_hide_cursor(enabled):
    if not enabled or not _isatty():
        return
    sys.stdout.write(ESC + "?25l")
    term_flush()


def term_show_cursor(enabled):
    if not enabled or not _isatty():
        return
    sys.stdout.write(ESC + "?25h")
    term_flush()


def term_move(enabled, row, col=1):
    if not enabled or not _isatty():
        return
    sys.stdout.write(f"{ESC}{row};{col}H")


def term_clear_line(enabled):
    if not enabled or not _isatty():
        return
    sys.stdout.write(ESC + "2K")


def term_set_scroll_region(enabled, top, bottom):
    if not enabled or not _isatty():
        return
    sys.stdout.write(f"{ESC}{top};{bottom}r")


def term_reset_scroll_region(enabled):
    if not enabled or not _isatty():
        return
    sys.stdout.write(ESC + "r")


def center_text(text: str, width: int):
    text = str(text)
    if width <= 0:
        return text
    if len(text) >= width:
        return text[:width]
    left = (width - len(text)) // 2
    right = width - len(text) - left
    return (" " * left) + text + (" " * right)


def make_divider(width: int, ch="="):
    if width >= 6:
        inner = ch * (width - 4)
        return f"[]{inner}[]"
    return ch * max(width, 1)


def load_logo_lines(path: Path, solid=True):
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return []

    lines = raw.splitlines()
    if not lines:
        return []

    if solid:
        lines = [ln.replace("░", " ") for ln in lines]

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()

    return lines


def print_banner(enabled):
    if not enabled:
        return 0

    cols = get_term_cols(80)
    div = make_divider(cols, "=")

    base_dir = Path(__file__).resolve().parent
    logo_path = base_dir / "bin" / "logo.txt"

    logo_lines = load_logo_lines(logo_path, solid=True)

    logo_width = max((len(line) for line in logo_lines), default=0)
    show_logo = bool(logo_lines) and cols >= logo_width

    lines = 0

    print("")
    lines += 1

    print(c(div, BLUE, BOLD))
    lines += 1

    print("")
    lines += 1

    if show_logo:
        for line in logo_lines:
            print(c(center_text(line, cols), BRIGHT_BLUE, BOLD))
            lines += 1

    print(c(center_text("A lightweight agent to monitor and manage *nix systems via Home Assistant over MQTT.", cols), WHITE, BOLD))
    lines += 1
    print(c(center_text(f"Version {VERSION}", cols), BRIGHT_CYAN, BOLD))
    lines += 1
    print(c(center_text("", cols), YELLOW, BOLD))
    lines += 1
    print(c(center_text("By Henrik Isefjær Ludvigsen", cols), GRAY, ITALIC))
    lines += 1
    print(c(center_text("github.com/henriklud", cols), GRAY, ITALIC))
    lines += 1

    print("")
    lines += 1

    print(c(div, BLUE, BOLD))
    lines += 1

    print("")
    lines += 1

    return lines


def _normalize_update_mode(mode) -> str:
    mode = (mode or "none").lower().strip()
    aliases = {"git": "github"}
    return aliases.get(mode, mode)


def version_tuple(v: str):
    parts = re.findall(r'\d+', str(v))
    return tuple(int(p) for p in parts) if parts else (0,)


def _read_upgrade_info_flag(name: str) -> bool:
    try:
        path = Path(__file__).resolve().parent / "bin" / "upgrade" / "upgrade.info"
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip().lower() == name.lower():
                return value.strip().lower() in ("1", "true", "yes")
    except Exception:
        pass
    return False


def _select_update_target(current_version, available_versions, exclude=None, prefer_latest=False):
    exclude = exclude or (lambda v: False)
    pool = [v for v in available_versions if not exclude(v)]
    if not pool:
        return None

    cur = version_tuple(current_version)

    if prefer_latest:
        latest = max(pool, key=version_tuple)
        return latest if version_tuple(latest) != cur else None

    newer = sorted((v for v in pool if version_tuple(v) > cur), key=version_tuple)
    if newer:
        return newer[0]

    by_version = sorted(pool, key=version_tuple)
    latest = by_version[-1]
    if version_tuple(latest) != cur:
        return latest
    return None


def find_newer_release_local(prefer_latest=False):
    if not os.path.isdir(RELEASES_DIR):
        return None, None

    releases = []
    for entry in os.listdir(RELEASES_DIR):
        if re.match(r'^\d[\d.\-a-zA-Z ]*$', entry):
            releases.append(entry)

    target = _select_update_target(VERSION, releases, exclude=is_version_recently_failed, prefer_latest=prefer_latest)
    if target:
        return target, os.path.join(RELEASES_DIR, target)

    return None, None


def _fetch_json(url: str, timeout: int):
    sep = '&' if '?' in url else '?'
    url = f"{url}{sep}_={int(time.time())}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "TuxD-Updater",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8-sig", errors="replace"))


def find_newer_release_web(prefer_latest=False):
    if not WEB_MANIFEST_URL:
        return None, None

    try:
        manifest = _fetch_json(WEB_MANIFEST_URL, timeout=WEB_TIMEOUT)
    except Exception:
        return None, None

    versions = [str(v).strip() for v in (manifest.get("versions") or []) if str(v).strip()]
    latest = str(manifest.get("latest", "")).strip()
    if latest and latest.replace(".", "").isdigit() and latest not in versions:
        versions.append(latest)

    versions = [v for v in versions if v.replace(".", "").isdigit()]
    if not versions:
        return None, None

    target = _select_update_target(VERSION, versions, exclude=is_version_recently_failed, prefer_latest=prefer_latest)
    if not target:
        return None, None

    if target == latest:
        download_url = str(manifest.get("download_url", "")).strip()
        if download_url:
            return target, download_url

    base = WEB_MANIFEST_URL.rsplit("/", 1)[0]
    return target, f"{base}/releases/{target}.tar.gz"


def find_newer_release_github(prefer_latest=False):
    if not GITHUB_REPO:
        return None, None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100"
    try:
        releases = _fetch_json(url, timeout=WEB_TIMEOUT)
    except Exception:
        return None, None

    if not isinstance(releases, list):
        return None, None

    by_version = {}
    for rel in releases:
        tag = str(rel.get("tag_name", "")).strip()
        v = tag[1:] if tag[:1] in ("v", "V") else tag
        if not v or not v.replace(".", "").isdigit():
            continue

        download_url = ""
        for asset in rel.get("assets") or []:
            name = str(asset.get("name", ""))
            if name.endswith(GITHUB_ASSET_SUFFIX):
                download_url = str(asset.get("browser_download_url", "")).strip()
                break

        if not download_url:
            download_url = str(rel.get("tarball_url", "")).strip()

        if download_url:
            by_version[v] = download_url

    target = _select_update_target(VERSION, list(by_version.keys()), exclude=is_version_recently_failed, prefer_latest=prefer_latest)
    if target and by_version.get(target):
        return target, by_version[target]

    return None, None


def find_release_by_version(target_version: str):
    mode = _normalize_update_mode(UPDATE_MODE)
    target = version_tuple(target_version)

    if mode in ("local", "both", "all") and os.path.isdir(RELEASES_DIR):
        candidate = os.path.join(RELEASES_DIR, target_version)
        if os.path.isdir(candidate):
            return target_version, "local", candidate
        for entry in os.listdir(RELEASES_DIR):
            if version_tuple(entry) == target:
                return entry, "local", os.path.join(RELEASES_DIR, entry)

    if mode in ("web", "both", "all") and WEB_MANIFEST_URL:
        base = WEB_MANIFEST_URL.rsplit("/", 1)[0]
        return target_version, "web", f"{base}/releases/{target_version}.tar.gz"

    if mode in ("github", "all") and GITHUB_REPO:
        for tag in (f"v{target_version}", target_version):
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
            try:
                manifest = _fetch_json(api_url, timeout=WEB_TIMEOUT)
            except Exception:
                continue
            download_url = ""
            for asset in manifest.get("assets") or []:
                name = str(asset.get("name", ""))
                if name.endswith(GITHUB_ASSET_SUFFIX):
                    download_url = str(asset.get("browser_download_url", "")).strip()
                    break
            if not download_url:
                download_url = str(manifest.get("tarball_url", "")).strip()
            if download_url:
                return target_version, "web", download_url

    return None, None, None


_DB_FILE = "database.json"


def _db_read() -> dict:
    try:
        with open(_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def _db_write(data: dict):
    try:
        with open(_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _write_update_status(status: str):
    try:
        with open(UPDATE_STATUS_FILE, "w", encoding="utf-8") as f:
            f.write(status)
    except Exception:
        pass


def _read_update_status() -> str:
    try:
        with open(UPDATE_STATUS_FILE, "r", encoding="utf-8") as f:
            s = f.read().strip()
        return s if s else "Up to date"
    except FileNotFoundError:
        return "Up to date"
    except Exception:
        return "Up to date"


def is_version_recently_failed(version: str) -> bool:
    ts = _db_read().get("failed_updates", {}).get(str(version), 0)
    return bool(ts) and (time.time() - ts) < FAILED_UPDATE_RETRY_SECONDS


def mark_version_failed(version: str):
    data = _db_read()
    data.setdefault("failed_updates", {})[str(version)] = time.time()
    _db_write(data)


def clear_version_failed(version: str):
    data = _db_read()
    if str(version) in data.get("failed_updates", {}):
        del data["failed_updates"][str(version)]
        _db_write(data)


def clear_all_failed_markers():
    data = _db_read()
    if "failed_updates" in data:
        data["failed_updates"] = {}
        _db_write(data)


def choose_update():
    mode = _normalize_update_mode(UPDATE_MODE)
    candidates = []
    prefer_latest = _read_upgrade_info_flag("extra_deps")

    if mode in ("local", "both", "all"):
        v, path = find_newer_release_local(prefer_latest=prefer_latest)
        if v and path and not is_version_recently_failed(v):
            candidates.append((v, "local", path))

    if mode in ("web", "both", "all"):
        v, url = find_newer_release_web(prefer_latest=prefer_latest)
        if v and url and not is_version_recently_failed(v):
            candidates.append((v, "web", url))

    if mode in ("github", "all"):
        v, url = find_newer_release_github(prefer_latest=prefer_latest)
        if v and url and not is_version_recently_failed(v):
            candidates.append((v, "web", url))

    if not candidates:
        return None, None, None

    candidates.sort(key=lambda x: version_tuple(x[0]))
    return candidates[-1]


def _download_to_file(url: str, dest_path: Path, timeout: int):
    req = urllib.request.Request(url, headers={"User-Agent": "TuxD-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as f:
        shutil.copyfileobj(resp, f)


def _extract_tar(tar_path: Path, dest_dir: Path):
    with tarfile.open(tar_path, "r:gz") as t:
        try:
            t.extractall(dest_dir, filter="data")
        except TypeError:
            t.extractall(dest_dir)


def _find_release_root(extracted_dir: Path):
    if (extracted_dir / "modules").exists() or (extracted_dir / "TuxD.py").exists():
        return extracted_dir

    kids = [p for p in extracted_dir.iterdir() if p.is_dir()]
    if len(kids) == 1:
        return kids[0]

    return extracted_dir


def _integrity_check_dir_compileall(target_dir: Path) -> bool:
    for path in Path(target_dir).rglob("*.py"):
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                ast.parse(f.read(), filename=str(path))
        except SyntaxError:
            return False
    return True


def _safe_copy(src: Path, dest: Path) -> None:
    try:
        shutil.copy2(src, dest)
    except PermissionError:
        r = subprocess.run(['sudo', 'cp', '-p', str(src), str(dest)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise PermissionError(f"Cannot copy {src} → {dest}: {r.stderr.strip()}")
        subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(dest)],
                       check=True)


def _safe_mkdir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        r = subprocess.run(['sudo', 'mkdir', '-p', str(path)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise PermissionError(f"Cannot mkdir {path}: {r.stderr.strip()}")
        subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(path)],
                       check=True)


def _safe_replace(src: Path, dest: Path) -> None:
    try:
        os.replace(src, dest)
    except PermissionError:
        r = subprocess.run(['sudo', 'mv', str(src), str(dest)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise PermissionError(f"Cannot move {src} → {dest}: {r.stderr.strip()}")
        subprocess.run(['sudo', 'chown', f'{os.getuid()}:{os.getgid()}', str(dest)],
                       check=True)


class _Rollback:
    def __init__(self, backup_dir=None):
        if backup_dir is not None:
            self.backup_dir = Path(backup_dir)
            self.backup_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.backup_dir = Path(tempfile.mkdtemp(prefix="tuxd-backup-"))
        self.map = {}
        self.created = []

    def backup_file_if_exists(self, dest: Path):
        if dest.exists():
            rel = str(dest).lstrip("/").replace("/", "__")
            b = self.backup_dir / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, b)
            self.map[str(dest)] = str(b)

    def mark_created(self, dest: Path):
        self.created.append(str(dest))

    def save_manifest(self):
        try:
            manifest = {"map": self.map, "created": self.created}
            (self.backup_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
        except Exception:
            pass

    def restore(self):
        for p in self.created:
            try:
                dp = Path(p)
                if dp.exists():
                    dp.unlink()
            except Exception:
                pass

        for dest_str, backup_str in self.map.items():
            try:
                dest = Path(dest_str)
                backup = Path(backup_str)
                _safe_mkdir(dest.parent)
                _safe_copy(backup, dest)
            except Exception:
                pass

    def cleanup(self):
        try:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
        except Exception:
            pass



def _apply_update_from_dir(src_root: Path, enabled: bool, rb: _Rollback):
    project_root = Path(__file__).resolve().parent

    if not (src_root / "modules").exists():
        raise RuntimeError("Release missing 'modules' directory")

    new_launcher = src_root / "TuxD.py"
    diag = f"Applying update from {src_root} - launcher found: {new_launcher.exists()} - contents: {sorted(p.name for p in src_root.iterdir())}"
    if enabled:
        print(c(diag, DIM))
    log_write(diag)
    if new_launcher.exists():
        with open(new_launcher, "r", encoding="utf-8-sig", errors="replace") as f:
            ast.parse(f.read(), filename=str(new_launcher))

    if not _integrity_check_dir_compileall(src_root):
        raise RuntimeError("Release integrity check failed (compileall)")

    for root, dirs, files in os.walk(src_root):
        rel = os.path.relpath(root, src_root)
        dest_root = project_root / rel
        _safe_mkdir(dest_root)

        for fn in files:
            if fn in ("config.yaml", "TuxD.py"):
                continue

            src_file = Path(root) / fn
            dest_file = dest_root / fn
            _safe_mkdir(dest_file.parent)

            existed_before = dest_file.exists()
            if existed_before:
                rb.backup_file_if_exists(dest_file)

            _safe_copy(src_file, dest_file)

            if not existed_before:
                rb.mark_created(dest_file)

            if enabled:
                print(c("Successfully updated file:", GREEN), str(dest_file))
                time.sleep(0.33)

    if new_launcher.exists():
        dest_launcher = Path(__file__).resolve()
        if dest_launcher.exists():
            rb.backup_file_if_exists(dest_launcher)

        tmp_launcher = dest_launcher.parent / f".{dest_launcher.name}.tmp"
        _safe_copy(new_launcher, tmp_launcher)
        _safe_replace(tmp_launcher, dest_launcher)

        if enabled:
            time.sleep(3)
            print(c("Successfully updated application:", GREEN), str(dest_launcher))
            time.sleep(1)


def _run_upgrade_hook(project_root: Path, enabled: bool):
    hook = project_root / "bin" / "upgrade" / "preperations.sh"
    if not hook.exists():
        return
    try:
        if enabled:
            print(c("Running upgrade hook...", WHITE, DIM, BOLD))
        unit = f"tuxd-upgrade-{os.getpid()}"
        if os.getuid() == 0:
            cmd = ["systemd-run", f"--unit={unit}", "--collect", "bash", str(hook)]
        else:
            cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect", "bash", str(hook)]
        subprocess.run(cmd, cwd=str(project_root), timeout=60)
    except Exception:
        pass


def safe_apply_update_any(new_version, source_type, source_value, enabled=True, restart_argv=None):
    project_root = Path(__file__).resolve().parent
    restart_argv = list(restart_argv) if restart_argv is not None else list(sys.argv)

    backup_dir = project_root / ".update_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    rb = _Rollback(backup_dir=backup_dir)

    try:
        if enabled:
            print(c(f"Installing version {new_version}...", YELLOW, BOLD))
            print("")
        time.sleep(1)

        diag = f"safe_apply_update_any: version={new_version} source_type={source_type} source_value={source_value}"
        if enabled:
            print(c(diag, DIM))
        log_write(diag)

        if source_type == "local":
            _apply_update_from_dir(Path(source_value), enabled, rb)

        elif source_type == "web":
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                tar_path = td_path / f"{new_version}.tar.gz"
                extract_dir = td_path / "extract"
                extract_dir.mkdir(parents=True, exist_ok=True)

                _download_to_file(source_value, tar_path, timeout=DOWNLOAD_TIMEOUT)
                _extract_tar(tar_path, extract_dir)

                release_root = _find_release_root(extract_dir)
                diag = f"_find_release_root: extract_dir contents={sorted(p.name for p in extract_dir.iterdir())} -> release_root={release_root}"
                if enabled:
                    print(c(diag, DIM))
                log_write(diag)
                _apply_update_from_dir(release_root, enabled, rb)

        else:
            raise RuntimeError("Unknown update source")

        if enabled:
            time.sleep(1)
            print(c("Checking update integrity...", WHITE, DIM, BOLD))
            time.sleep(3)

        ok = _integrity_check_dir_compileall(project_root)
        if not ok:
            raise RuntimeError("Installed integrity check failed (compileall)")

        clear_version_failed(new_version)

        _run_upgrade_hook(project_root, enabled)

        rb.save_manifest()
        _db = _db_read()
        _db["pending_update_backup"] = str(rb.backup_dir)
        _db["pending_update_version"] = str(new_version)
        _db_write(_db)

        if enabled:
            print(c("Update applied successfully", GREEN, BOLD))
            time.sleep(1)
            print(c("Restarting...", YELLOW, BOLD))
            print("")
        time.sleep(1)

    except Exception as e:
        mark_version_failed(str(new_version))
        _write_update_status("Update failed")

        if enabled:
            print(c("Update FAILED:", RED, BOLD), c(repr(e), RED))
            print(c("Rolling back to previous version...", YELLOW, BOLD))
        try:
            rb.restore()
        finally:
            rb.cleanup()

        if enabled:
            print(c("Rollback complete.", GREEN, BOLD))
            print(c("Restarting...", YELLOW, BOLD))
            print("")
        time.sleep(2)
        os.execv(sys.executable, [sys.executable] + restart_argv)

    self_path = Path(restart_argv[0]) if restart_argv else None
    if self_path is not None:
        if not self_path.is_absolute():
            self_path = Path.cwd() / self_path
        if not self_path.exists():
            sys.exit(0)

    os.execv(sys.executable, [sys.executable] + restart_argv)


def ask_install_update(new_version: str, timeout: int = 10, enabled=True) -> bool:
    if not enabled:
        return False

    is_upgrade = version_tuple(new_version) > version_tuple(VERSION)
    label = "Found new update:" if is_upgrade else "Found rollback target:"
    verb = "update" if is_upgrade else "rollback"

    prompt = (
        f"{c(label, YELLOW, BOLD)} {c(new_version, YELLOW, BOLD)}\n"
        f"Install {verb} now? {c('[y/N]', CYAN, BOLD)} {c(f'(auto-skip in {timeout}s)', DIM)}: "
    )

    if not sys.stdin.isatty():
        print(prompt + c("N (non-interactive)", DIM))
        return False

    sys.stdout.write(prompt)
    sys.stdout.flush()

    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
    if not rlist:
        print("")
        print(c("No answer received. Continuing with installed version.", DIM))
        return False

    ans = sys.stdin.readline().strip().lower()
    return ans in ("y", "yes")


def mqtt_broker_ok(cfg, timeout=5):
    import paho.mqtt.client as mqtt

    mqtt_cfg = (cfg or {}).get("mqtt", {}) or {}
    host = mqtt_cfg.get("broker")
    port = int(mqtt_cfg.get("port", 1883))
    user = mqtt_cfg.get("username")
    pw = mqtt_cfg.get("password")

    if not host:
        return False

    state = {"done": False, "ok": False}

    def on_connect(client, userdata, flags, *args, **kwargs):
        rc = args[0] if args else 1
        try:
            rc_int = int(rc)
        except Exception:
            rc_int = rc
        state["ok"] = (rc_int == 0)
        state["done"] = True
        try:
            client.disconnect()
        except Exception:
            pass

    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        client = mqtt.Client()

    client.on_connect = on_connect

    if user is not None:
        client.username_pw_set(user, pw)

    try:
        client.connect(host, port, 60)
    except Exception:
        return False

    start = time.time()
    while time.time() - start < timeout and not state["done"]:
        client.loop(timeout=0.2)

    try:
        client.disconnect()
        client.loop(timeout=0.2)
    except Exception:
        pass

    return bool(state["ok"])


def _publish_emergency_error(cfg, reason):
    try:
        import paho.mqtt.client as mqtt

        mqtt_cfg = (cfg or {}).get("mqtt", {}) or {}
        device_cfg = (cfg or {}).get("device", {}) or {}
        device_name = device_cfg.get("name")
        broker = mqtt_cfg.get("broker")
        if not device_name or not broker:
            return

        port = int(mqtt_cfg.get("port", 1883))
        base_topic = f"TuxD{device_name}"
        device_info = {
            "identifiers": [device_name],
            "name": device_name,
            "manufacturer": "Henrik Isefjær Olsen",
            "model": f"TuxD Linux Agent Version {VERSION}",
            "sw_version": VERSION,
        }

        try:
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        except Exception:
            client = mqtt.Client()

        if mqtt_cfg.get("username") is not None:
            client.username_pw_set(mqtt_cfg.get("username"), mqtt_cfg.get("password"))

        client.connect(broker, port, 10)
        client.loop_start()
        time.sleep(0.5)

        discovery_payload = {
            "name": "Error",
            "state_topic": f"{base_topic}/error",
            "unique_id": f"{device_name}_system_error",
            "device": device_info,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "problem",
            "icon": "mdi:alert-circle",
            "json_attributes_topic": f"{base_topic}/error_attributes",
            "entity_category": "diagnostic",
        }
        client.publish(
            f"homeassistant/binary_sensor/{device_name}/system_error/config",
            json.dumps(discovery_payload), retain=True,
        )
        client.publish(f"{base_topic}/error", "ON", retain=True)
        client.publish(f"{base_topic}/error_attributes", json.dumps({"reason": reason}), retain=True)
        time.sleep(0.5)

        client.loop_stop()
        client.disconnect()
    except Exception:
        pass


def restart_in(seconds=10):
    time.sleep(seconds)
    os.execv(sys.executable, [sys.executable] + sys.argv)


class TopStatus:
    def __init__(self, enabled, start_row):
        self.enabled = enabled and _isatty()
        self.start_row = start_row
        self._msgs = []
        self._divider_row = None
        self._divider_text = None

    def write(self, text):
        if not self.enabled:
            return
        next_row = self.start_row + len(self._msgs)
        if self._divider_row is not None and next_row >= self._divider_row:
            self._divider_row += 1
            term_move(True, self._divider_row, 1)
            term_clear_line(True)
            sys.stdout.write(self._divider_text)
            height = get_term_lines(24)
            scroll_top = self._divider_row + 1
            term_set_scroll_region(True, scroll_top, max(height, scroll_top))
        self._msgs.append(text)
        row = self.start_row + len(self._msgs) - 1
        term_move(True, row, 1)
        term_clear_line(True)
        sys.stdout.write(text)
        term_flush()

    def overwrite_last(self, text):
        if not self.enabled:
            return
        if not self._msgs:
            self.write(text)
            return
        self._msgs[-1] = text
        row = self.start_row + len(self._msgs) - 1
        term_move(True, row, 1)
        term_clear_line(True)
        sys.stdout.write(text)
        term_flush()

    def set_divider(self, text):
        if not self.enabled:
            return 0
        self._divider_row = self.start_row + max(5, len(self._msgs))
        self._divider_text = text
        term_move(True, self._divider_row, 1)
        term_clear_line(True)
        sys.stdout.write(text)
        term_flush()
        return self._divider_row

    def next_row(self):
        return self.start_row + len(self._msgs)


def _cleanup_terminal():
    global _LOG_FILE, _LOG_ROTATE_THREAD, _CLEANUP_DONE, _STATUS, _AGENT
    if _CLEANUP_DONE:
        return
    _CLEANUP_DONE = True
    enabled = bool(_TTY_ENABLED) and _isatty()

    if _STATUS is not None and _STATUS.enabled:
        try:
            _STATUS.write(c("Shutting down TuxD...", YELLOW, BOLD))
        except Exception:
            pass
    log_write("Shutting down TuxD.")

    if _AGENT is not None:
        try:
            _AGENT.stop()
        except Exception:
            pass

    time.sleep(0.3)

    if _STATUS is not None and _STATUS.enabled:
        try:
            _STATUS.write(c("Shutdown successful!", GREEN, BOLD))
        except Exception:
            pass
    log_write("Shutdown successful.")

    if enabled:
        time.sleep(1)
        term_reset_scroll_region(True)
        term_clear(True)
        term_show_cursor(True)
        term_flush()
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.close()
        except Exception:
            pass
        _LOG_FILE = None
    if _LOG_ROTATE_THREAD is not None:
        try:
            _LOG_ROTATE_THREAD.join(timeout=1)
        except Exception:
            pass

    try:
        _data = _db_read()
        _data["process_status"] = "Clean shutdown"
        _db_write(_data)
    except Exception:
        pass


def _signal_handler(signum, frame):
    _cleanup_terminal()
    raise SystemExit(0)


def _migrate_legacy_folder_name():
    project_root = Path(__file__).resolve().parent
    if project_root.name.lower() != "py-k93sys":
        return

    new_root = project_root.parent / "TuxD"
    if new_root.exists():
        return

    try:
        os.rename(str(project_root), str(new_root))
    except Exception:
        return

    new_script = new_root / "TuxD.py"
    try:
        os.chdir(str(new_root))
    except Exception:
        pass

    os.execv(sys.executable, [sys.executable, str(new_script)] + sys.argv[1:])


def _restore_pending_update(db: dict):
    backup_dir = db.get("pending_update_backup")
    version = db.get("pending_update_version")

    if backup_dir:
        try:
            bdir = Path(backup_dir)
            manifest_path = bdir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for p in manifest.get("created", []):
                    try:
                        Path(p).unlink()
                    except Exception:
                        pass
                for dest_str, backup_str in manifest.get("map", {}).items():
                    try:
                        _safe_mkdir(Path(dest_str).parent)
                        _safe_copy(Path(backup_str), Path(dest_str))
                    except Exception:
                        pass
            shutil.rmtree(bdir, ignore_errors=True)
        except Exception:
            pass

        if version:
            mark_version_failed(str(version))
            _write_update_status(f"Update to {version} failed to start - rolled back")

    db.pop("pending_update_backup", None)
    db.pop("pending_update_version", None)
    db.pop("pending_update_bounced", None)
    db["process_status"] = "running"
    _db_write(db)


def _confirm_pending_update():
    try:
        db = _db_read()
        backup_dir = db.get("pending_update_backup")
        if backup_dir:
            shutil.rmtree(Path(backup_dir), ignore_errors=True)
        db.pop("pending_update_backup", None)
        db.pop("pending_update_version", None)
        db.pop("pending_update_bounced", None)
        _db_write(db)
        log_write("Update confirmed stable - rollback point cleared.")
    except Exception:
        pass


def _handle_rollback_arg():
    if len(sys.argv) < 3 or sys.argv[1] != "__rollback":
        return

    target_version = sys.argv[2]
    tty = _isatty()

    version, source_type, source_value = find_release_by_version(target_version)
    if not version:
        print(f"Version {target_version} was not found via the configured update source (UPDATE_MODE={UPDATE_MODE}).")
        sys.exit(1)

    print(f"Manual rollback: installing version {version} via UPDATE_MODE={UPDATE_MODE}...")
    safe_apply_update_any(version, source_type, source_value, enabled=tty, restart_argv=[sys.argv[0]])


def main():
    global _TTY_ENABLED, _LOG_FILE, _LOG_ROTATE_THREAD, _STATUS, _AGENT

    _handle_rollback_arg()

    _migrate_legacy_folder_name()

    _db = _db_read()
    _last_status = _db.get("process_status")
    pending_backup = _db.get("pending_update_backup")

    if _last_status is not None and _last_status != "Clean shutdown":
        if pending_backup and _db.get("pending_update_bounced"):
            _restore_pending_update(_db)
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return

        if pending_backup:
            _db["pending_update_bounced"] = True
            _db["process_status"] = "running"
            _db_write(_db)
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return

        try:
            os.remove(_DB_FILE)
        except FileNotFoundError:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
        return

    _db["process_status"] = "running"
    _db_write(_db)

    if pending_backup:
        threading.Timer(60.0, _confirm_pending_update).start()

    clear_all_failed_markers()

    try:
        from modules.config_sync import sync_config
        _bin = Path(__file__).resolve().parent / "bin"
        sync_config(CONFIG_PATH, str(_bin / "conf_vars_defaults.yaml"), str(_bin / "conf_object_defaults.yaml"))
    except Exception:
        pass

    try:
        cfg = yaml.safe_load(Path(CONFIG_PATH).read_text(encoding="utf-8"))
    except Exception:
        print(c("Failed to load configuration!", RED, BOLD))
        sys.exit(EXIT_CONFIG_ERROR)

    device_cfg = (cfg or {}).get("device", {}) or {}
    tty_output = bool(device_cfg.get("tty_output", False))
    log_to_file = bool(device_cfg.get("log_to_file", False))
    log_level = str(device_cfg.get("log_level", "all")).lower().strip()
    if log_level not in ("all", "error"):
        log_level = "all"
    retry_delay = int(((cfg or {}).get("mqtt") or {}).get("retry_delay", 30))
    enabled = tty_output and _isatty()
    _TTY_ENABLED = enabled

    if log_to_file:
        try:
            _LOG_FILE = open(LOG_FILE_PATH, "w", encoding="utf-8", buffering=1)
            _LOG_ROTATE_THREAD = threading.Thread(target=_log_rotate_loop, daemon=True)
            _LOG_ROTATE_THREAD.start()
        except Exception:
            _LOG_FILE = None

    log_write("Configuration loaded successfully.")

    atexit.register(_cleanup_terminal)
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        pass

    term_clear(enabled)
    term_hide_cursor(enabled)

    try:
        banner_lines = print_banner(enabled)

        STATUS_GAP_LINES = 0
        status_start_row = (banner_lines + 1 + STATUS_GAP_LINES) if banner_lines else 1

        status = TopStatus(enabled, status_start_row)
        _STATUS = status

        if enabled:
            status.write(c("Initializing...", WHITE, BOLD))
            time.sleep(1)
            status.write(c("Trying to parse configuration...", WHITE, BOLD))
            time.sleep(.5)
            status.write(c("Configuration loaded.", GREEN, BOLD))
            time.sleep(.5)
            status.write(c("Checking for updates...", WHITE, BOLD))
            time.sleep(.5)

        new_version, src_type, src_val = choose_update()
        if new_version and src_type and src_val:
            if enabled:
                prompt_row = status.next_row() + 1
                term_move(True, prompt_row, 1)
                term_clear_line(True)
                term_flush()
                if ask_install_update(new_version, timeout=10, enabled=True):
                    safe_apply_update_any(new_version, src_type, src_val, enabled=True)
                else:
                    _write_update_status("Update available")
                    status.write(c("Update skipped.", YELLOW))
                    time.sleep(1)
            else:
                safe_apply_update_any(new_version, src_type, src_val, enabled=False)
        else:
            _write_update_status("Up to date")
            if enabled:
                status.write(c("No new update is available!", WHITE, BOLD))
                time.sleep(1)

        if enabled:
            status.write(c("Testing connecting to MQTT broker...", WHITE))
            time.sleep(1)

        if not mqtt_broker_ok(cfg, timeout=5):
            if enabled:
                mqtt_cfg = (cfg or {}).get("mqtt", {}) or {}
                host = mqtt_cfg.get("broker", "<missing>")
                port = mqtt_cfg.get("port", 1883)
                status.write(c(f"MQTT broker not ready: {host}:{port}", RED, BOLD))
                status.write(c(f"Retrying in {retry_delay}s...", YELLOW, BOLD))
                time.sleep(0.5)
            restart_in(retry_delay)
            return

        if enabled:
            status.write(c("Successfully tested connection to MQTT broker!", GREEN, BOLD))
            time.sleep(.5)

        try:
            from modules.mqtt_agent import HAMQTTAgent
        except Exception as e:
            if enabled:
                status.write(c(f"Import error: {e}", RED, BOLD))
                time.sleep(5)
            log_write(f"Import error: {repr(e)}")
            _publish_emergency_error(cfg, f"Startup failed: import error - {e}")
            sys.exit(1)

        if enabled:
            status.write(c("Starting agent...", WHITE, BOLD))
            time.sleep(.25)

            cols = get_term_cols(80)
            divider_row = status.set_divider(c(make_divider(cols, "="), BLUE, BOLD))

            height = get_term_lines(24)
            scroll_top = divider_row + 1
            if height < scroll_top:
                height = scroll_top

            term_set_scroll_region(True, scroll_top, height)
            term_move(True, scroll_top, 1)
            term_clear_line(True)
            term_flush()

        first_start = True

        while True:
            try:
                agent = HAMQTTAgent(
                    cfg, VERSION, log_file=_LOG_FILE, log_level=log_level,
                    update_status=_read_update_status(),
                    update_checker=choose_update,
                    update_applier=lambda nv, st, sv: safe_apply_update_any(nv, st, sv, enabled=False),
                )
            except Exception as e:
                if enabled:
                    status.write(c(f"Agent init failed: {e}", RED, BOLD))
                    time.sleep(5)
                log_write(f"Agent init failed: {repr(e)}")
                _publish_emergency_error(cfg, f"Startup failed: {e}")
                sys.exit(EXIT_CONFIG_ERROR)
            _AGENT = agent

            def _on_agent_ready():
                label = "Agent started." if first_start else "Reconnected to MQTT broker!"
                status.write(c(label, GREEN, BOLD))
                term_move(True, status._divider_row + 1, 1)
                term_flush()

            try:
                broker_lost = agent.start(on_ready=_on_agent_ready if enabled else None)
            except Exception as e:
                broker_lost = True
                if enabled:
                    status.write(c(f"MQTT connection failed: {e}", RED, BOLD))
                log_write(f"MQTT connection failed: {repr(e)}")

            first_start = False

            if not broker_lost:
                break

            if enabled:
                status.write(c(f"MQTT broker lost — retrying in {retry_delay}s...", YELLOW, BOLD))
            log_write(f"MQTT broker lost. Retrying in {retry_delay}s.")

            time.sleep(retry_delay)

            if enabled:
                status.write(c("Reconnecting to MQTT broker...", WHITE))
                if status._divider_row is not None:
                    term_move(True, status._divider_row + 1, 1)
                    term_flush()
            log_write("Reconnecting to MQTT broker.")

    finally:
        _cleanup_terminal()


if __name__ == "__main__":
    main()