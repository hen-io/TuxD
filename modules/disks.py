import os


def read_diskstats(dev):
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if parts[2] == dev:
                    read_sectors = int(parts[5])
                    write_sectors = int(parts[9])
                    return read_sectors * 512, write_sectors * 512
    except:
        pass

    return 0, 0

def disk_space(mount):
    try:
        st = os.statvfs(mount)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free

        used_gb = round(used / 1_000_000_000, 2)
        free_gb = round(free / 1_000_000_000, 2)
        used_pct = round((used / total) * 100, 2) if total > 0 else 0

        return {
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": used_pct,
        }
    except:
        return {
            "used_gb": 0,
            "free_gb": 0,
            "used_pct": 0,
        }

def smart_errors(dev_path):
    try:
        import subprocess

        result = subprocess.run(
            ["smartctl", "-A", dev_path],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return 0

        errors = 0
        for line in result.stdout.split("\n"):
            if "Reallocated_Sector_Ct" in line:
                parts = line.split()
                errors += int(parts[-1])
            if "Current_Pending_Sector" in line:
                parts = line.split()
                errors += int(parts[-1])
            if "Offline_Uncorrectable" in line:
                parts = line.split()
                errors += int(parts[-1])

        return errors

    except:
        return 0
