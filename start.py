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
REPAIR_COOLDOWN_FILE = PROJECT_DIR / ".last_repair_attempt"
CONFIG_ERROR_EXIT = 78
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


def _find_release(repo, version=None):
    if version:
        for tag in (f"v{version}", version):
            api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
            try:
                req = urllib.request.Request(api_url, headers={"User-Agent": "TuxD-Updater"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8-sig", errors="replace"))
            except Exception:
                continue
        return None

    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(api_url, headers={"User-Agent": "TuxD-Updater"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8-sig", errors="replace"))


def full_repair(version=None, force=False):
    if not force and _repair_on_cooldown():
        log("Repair attempted recently - waiting out the cooldown before trying again.")
        return False

    try:
        REPAIR_COOLDOWN_FILE.write_text(str(time.time()))
    except Exception:
        pass

    repo = _read_github_repo()
    if not repo:
        log("Repair: could not find GITHUB_REPO in TuxD.py - skipping.")
        return False

    try:
        release = _find_release(repo, version)
        if not release:
            log(f"Repair: {'version ' + version if version else 'latest release'} not found on GitHub - skipping.")
            return False

        found_version = str(release.get("tag_name", "")).strip()
        if found_version[:1] in ("v", "V"):
            found_version = found_version[1:]

        download_url = ""
        for asset in release.get("assets") or []:
            name = str(asset.get("name", ""))
            if name.endswith(".tar.gz"):
                download_url = str(asset.get("browser_download_url", "")).strip()
                break
        if not download_url:
            download_url = str(release.get("tarball_url", "")).strip()

        if not download_url:
            log("Repair: release has no usable download - skipping.")
            return False

        log(f"Repairing: downloading {found_version or version or 'latest'} via github from {download_url}")

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            tar_path = td_path / "release.tar.gz"
            req = urllib.request.Request(download_url, headers={"User-Agent": "TuxD-Updater"})
            with _Spinner(f"Downloading {found_version or version or 'latest'} (github)"):
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
                log("Repair: downloaded release doesn't look valid (no modules/) - aborting.")
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

        log(f"Repair complete - {found_version or version or 'latest'} installed (config.yaml untouched).")
        return True
    except Exception as e:
        log(f"Repair failed: {e}")
        return False


def main():
    tuxd_py = PROJECT_DIR / "TuxD.py"

    while True:
        result = subprocess.run([sys.executable, str(tuxd_py)] + sys.argv[1:])
        exit_code = result.returncode

        if exit_code == 0:
            log("TuxD.py exited cleanly.")
            return 0

        if exit_code == CONFIG_ERROR_EXIT:
            log(f"TuxD.py reported a configuration error (exit {CONFIG_ERROR_EXIT}) - "
                f"fix config.yaml. Retrying in 60s.")
            time.sleep(60)
            continue

        log(f"TuxD.py exited (code {exit_code}). Restarting in 5s.")
        time.sleep(5)


if __name__ == "__main__":
    if "--fix" in sys.argv:
        idx = sys.argv.index("--fix")
        target_version = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        log(f"Manual fix requested (--fix{' ' + target_version if target_version else ''})...")
        sys.exit(0 if full_repair(version=target_version, force=True) else 1)

    sys.exit(main())
