import json
import re
import subprocess
import time

_KINDS = (
    ("temp", "°C", "temperature", "mdi:thermometer"),
    ("fan", "RPM", None, "mdi:fan"),
    ("in", "V", "voltage", "mdi:sine-wave"),
    ("power", "W", "power", "mdi:flash"),
    ("curr", "A", "current", "mdi:current-ac"),
    ("humidity", "%", "humidity", "mdi:water-percent"),
)
_INPUT = re.compile(r"^([a-z]+)\d*_input$")


def _sensors_json():
    try:
        result = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _flatten(data):
    flat = {}
    for chip, chip_data in data.items():
        if not isinstance(chip_data, dict):
            continue
        for section, section_data in chip_data.items():
            if not isinstance(section_data, dict):
                continue
            for key, value in section_data.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    flat[f"{chip}_{section}_{key}".replace(" ", "_")] = (chip, section, key, value)
    return flat


def read_lm_sensors(flat_overrides=None, include=None):
    flat_overrides = flat_overrides or {}
    include_set = set(include) if include else None
    out = {}
    for flat_key, (_chip, _section, _key, value) in _flatten(_sensors_json()).items():
        if include_set is not None and flat_key not in include_set:
            continue
        out[flat_overrides.get(flat_key, flat_key)] = value
    return out


def describe(chip, section, key):
    m = _INPUT.match(key)
    prefix = m.group(1) if m else ""
    for kind, unit, device_class, icon in _KINDS:
        if prefix == kind:
            return unit, device_class, icon
    return None, None, "mdi:gauge"


def discover_active(window=10.0, interval=2.0, stop_event=None, sample=None):
    sample = sample or _sensors_json
    first = _flatten(sample())
    seen = {k: {v[3]} for k, v in first.items() if _INPUT.match(v[2])}
    deadline = time.monotonic() + max(0.0, float(window))
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.wait(timeout=interval):
            return {}
        if stop_event is None:
            time.sleep(interval)
        for k, v in _flatten(sample()).items():
            if k in seen:
                seen[k].add(v[3])
    active = [k for k, values in seen.items() if len(values) > 1]
    sections = {}
    for k in active:
        sections.setdefault(first[k][1], []).append(k)
    names = {}
    for k in active:
        chip, section, key, _value = first[k]
        names[k] = section if len(sections[section]) == 1 else f"{section} ({chip})"
    counts = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    found = {}
    for k in sorted(active):
        chip, section, key, _value = first[k]
        unit, device_class, icon = describe(chip, section, key)
        name = names[k] if counts[names[k]] == 1 else f"{names[k]} {key}"
        found[k] = {"name": name, "unit_of_measurement": unit, "device_class": device_class, "icon": icon}
    return found
