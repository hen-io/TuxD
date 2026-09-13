#!/usr/bin/env python3


import json
import shutil
import sys
import time
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("usage: restore_backup.py <backup_dir>", file=sys.stderr)
        sys.exit(1)

    backup_dir = Path(sys.argv[1])
    manifest_path = backup_dir / "manifest.json"

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

        for p in manifest.get("created", []):
            try:
                Path(p).unlink()
            except Exception:
                pass

        for dest_str, backup_str in manifest.get("map", {}).items():
            try:
                dest = Path(dest_str)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_str, dest)
            except Exception:
                pass

    shutil.rmtree(backup_dir, ignore_errors=True)

    db_path = Path("database.json")
    try:
        db = json.loads(db_path.read_text(encoding="utf-8")) if db_path.exists() else {}
    except Exception:
        db = {}

    version = db.get("pending_update_version")
    if version:
        db.setdefault("failed_updates", {})[str(version)] = time.time()

    db.pop("pending_update_backup", None)
    db.pop("pending_update_version", None)
    db.pop("pending_update_bounced", None)
    db["process_status"] = "running"

    try:
        db_path.write_text(json.dumps(db, indent=2), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    main()
