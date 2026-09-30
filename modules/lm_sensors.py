import subprocess
import json


def read_lm_sensors(flat_overrides=None, include=None):
    flat_overrides = flat_overrides or {}
    include_set = set(include) if include else None

    try:
        result = subprocess.run(
            ["sensors", "-j"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}

        data = json.loads(result.stdout)
    except Exception:
        return {}

    flat = {}

    for chip, chip_data in data.items():
        if not isinstance(chip_data, dict):
            continue

        for section, section_data in chip_data.items():
            if not isinstance(section_data, dict):
                continue

            for key, value in section_data.items():
                if not isinstance(value, (int, float)):
                    continue

                flat_key = f"{chip}_{section}_{key}".replace(" ", "_")

                if include_set is not None and flat_key not in include_set:
                    continue

                if flat_key in flat_overrides:
                    out_key = flat_overrides[flat_key]
                else:
                    out_key = flat_key

                flat[out_key] = value

    return flat