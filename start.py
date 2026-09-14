#!/usr/bin/env python3


import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "upgrade.log"
FAIL_COUNT_FILE = PROJECT_DIR / ".start_fail_count"
BACKUP_DIR = PROJECT_DIR / ".update_backup"
REPAIR_COOLDOWN_FILE = PROJECT_DIR / ".last_repair_attempt"
CONFIG_ERROR_EXIT = 78
FAST_FAIL_SECONDS = 30
ROLLBACK_AFTER_FAILS = 2
REPAIR_COOLDOWN_SECONDS = 600


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _isatty():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


class _Spinner:
    FRAMES = "|/-\\"

    def __init__(self, label):
        self.label = label
        self.enabled = _isatty()
        self._stop = None
        self._thread = None

    def __enter__(self):
        if self.enabled:
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            log(self.label)
        return self

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r{self.label} {frame} ")
            sys.stdout.flush()
            i += 1
            time.sleep(0.15)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.enabled and self._stop is not None:
            self._stop.set()
            self._thread.join(timeout=1)
            sys.stdout.write("\r" + " " * (len(self.label) + 4) + "\r")
            sys.stdout.flush()
        return False


def read_fail_count():
    try:
        return int(FAIL_COUNT_FILE.read_text().strip())
    except Exception:
        return 0


def write_fail_count(n):
    try:
        FAIL_COUNT_FILE.write_text(str(n))
    except Exception:
        pass


def clear_fail_count():
    try:
        FAIL_COUNT_FILE.unlink()
    except Exception:
        pass


def restore_backup():
    if not BACKUP_DIR.is_dir():
        return False
    log(f"Downgrading: restoring pre-update backup from {BACKUP_DIR}...")
    helper = PROJECT_DIR / "bin" / "upgrade" / "restore_backup.py"
    with _Spinner("Restoring previous version"):
        subprocess.run([sys.executable, str(helper), str(BACKUP_DIR)])
    log("Backup restored.")
    return True


def _read_github_repo():
    try:
        text = (PROJECT_DIR / "TuxD.py").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^GITHUB_REPO\s*=\s*"([^"]*)"', text, re.MULTILINE)
        return m.group(1) if m and m.group(1) else None
    except Exception:
        return None


def _repair_on_cooldown():
    try:
        last = float(REPAIR_COOLDOWN_FILE.read_text().strip())
    except Exception:
        return False
    return (time.time() - last) < REPAIR_COOLDOWN_SECONDS


def full_repair(force=False):
    if not force and _repair_on_cooldown():
        log("Full repair attempted recently - waiting out the cooldown before trying again.")
        return False

    try:
        REPAIR_COOLDOWN_FILE.write_text(str(time.time()))
    except Exception:
        pass

    repo = _read_github_repo()
    if not repo:
        log("Full repair: could not find GITHUB_REPO in TuxD.py - skipping.")
        return False

    try:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TuxD-Updater"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8-sig", errors="replace"))

        version = str(release.get("tag_name", "")).strip()
        if version[:1] in ("v", "V"):
            version = version[1:]

        download_url = ""
        for asset in release.get("assets") or []:
            name = str(asset.get("name", ""))
            if name.endswith(".tar.gz"):
                download_url = str(asset.get("browser_download_url", "")).strip()
                break
        if not download_url:
            download_url = str(release.get("tarball_url", "")).strip()

        if not download_url:
            log("Full repair: release has no usable download - skipping.")
            return False

        log(f"Repairing: downloading {version or 'latest'} via github from {download_url}")

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            tar_path = td_path / "release.tar.gz"
            req = urllib.request.Request(download_url, headers={"User-Agent": "TuxD-Updater"})
            with _Spinner(f"Downloading {version or 'latest'} (github)"):
                with urllib.request.urlopen(req, timeout=60) as resp, open(tar_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

            extract_dir = td_path / "extract"
            extract_dir.mkdir()
            with _Spinner("Extracting release"):
                with tarfile.open(tar_path, "r:gz") as t:
                    try:
                        t.extractall(extract_dir, filter="data")
                    except TypeError:
                        t.extractall(extract_dir)

            release_root = extract_dir
            if not (release_root / "modules").exists():
                kids = [p for p in release_root.iterdir() if p.is_dir()]
                if len(kids) == 1:
                    release_root = kids[0]

            if not (release_root / "modules").exists():
                log("Full repair: downloaded release doesn't look valid (no modules/) - aborting.")
                return False

            with _Spinner("Installing files"):
                for root, _dirs, files in os.walk(release_root):
                    rel = os.path.relpath(root, release_root)
                    dest_root = PROJECT_DIR / rel
                    dest_root.mkdir(parents=True, exist_ok=True)
                    for fn in files:
                        if fn == "config.yaml":
                            continue
                        shutil.copy2(Path(root) / fn, dest_root / fn)

        log(f"Full repair complete - {version or 'latest'} installed fresh via github (config.yaml untouched).")
        return True
    except Exception as e:
        log(f"Full repair failed: {e}")
        return False


def main():
    tuxd_py = PROJECT_DIR / "TuxD.py"

    while True:
        start_time = time.time()
        result = subprocess.run([sys.executable, str(tuxd_py)] + sys.argv[1:])
        exit_code = result.returncode
        ran_for = time.time() - start_time

        if exit_code == 0:
            log("TuxD.py exited cleanly.")
            return 0

        if exit_code == CONFIG_ERROR_EXIT:
            log(f"TuxD.py reported a configuration error (exit {CONFIG_ERROR_EXIT}) - "
                f"fix config.yaml. Not touching the install; retrying in 60s.")
            clear_fail_count()
            time.sleep(60)
            continue

        if ran_for >= FAST_FAIL_SECONDS:
            clear_fail_count()
            log(f"TuxD.py exited (code {exit_code}) after running for {int(ran_for)}s - "
                f"not treated as a startup crash. Restarting.")
            time.sleep(5)
            continue

        fails = read_fail_count() + 1
        write_fail_count(fails)
        log(f"TuxD.py failed fast (code {exit_code}, ran {int(ran_for)}s) - "
            f"consecutive fast failures: {fails}")

        if fails >= ROLLBACK_AFTER_FAILS:
            if restore_backup():
                clear_fail_count()
                time.sleep(2)
                continue
            log("No pending update backup to restore - trying a full repair instead.")
            if full_repair():
                clear_fail_count()
                time.sleep(2)
                continue
            log("Full repair unavailable or failed - letting it keep retrying.")

        time.sleep(5)


if __name__ == "__main__":
    if "--fix" in sys.argv:
        log("Manual fix requested (--fix): running full repair...")
        sys.exit(0 if full_repair(force=True) else 1)

    sys.exit(main())
