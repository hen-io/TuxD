import hashlib
import json
import yaml
from pathlib import Path

_SEP_BAR = '#' * 33


def _compute_samples_hash(section_samples: dict) -> str:
    payload = json.dumps({k: v for k, v in sorted(section_samples.items())})
    return hashlib.md5(payload.encode()).hexdigest()


def _read_samples_hash(config_path: str) -> str:
    meta = Path(config_path).with_suffix('.ksync')
    try:
        return json.loads(meta.read_text(encoding='utf-8')).get('samples_hash', '')
    except Exception:
        return ''


def _write_samples_hash(config_path: str, hash_val: str):
    meta = Path(config_path).with_suffix('.ksync')
    try:
        meta.write_text(json.dumps({'samples_hash': hash_val}), encoding='utf-8')
    except Exception:
        pass


def _make_separator(title: str) -> str:
    return f'{_SEP_BAR}\n# {title:<30}#\n{_SEP_BAR}'


def _parse_conf_vars(text: str) -> tuple:
    """
    Parse conf_vars_defaults.yaml raw text to extract section metadata.

    Returns:
      section_labels  – {yaml_key: separator_title}
      section_samples – {yaml_key: sample_comment_block_str}

    Separator boxes are detected as three consecutive lines:
      #################################
      # Title                         #
      #################################
    The first top-level (non-indented) non-comment line after each box is the
    YAML key for that section.  A contiguous run of '#'-prefixed lines starting
    with '# Example' (at column 0) is treated as that section's sample block.
    """
    section_labels = {}
    section_samples = {}
    lines = text.splitlines(keepends=True)
    n = len(lines)
    pending_title = None
    current_key = None
    i = 0

    while i < n:
        raw = lines[i].rstrip('\n').rstrip('\r')
        stripped = raw.strip()


        if stripped == _SEP_BAR and i + 2 < n:
            title_raw = lines[i + 1].rstrip('\n').rstrip('\r').strip()
            third_raw = lines[i + 2].rstrip('\n').rstrip('\r').strip()
            if third_raw == _SEP_BAR and title_raw.startswith('# ') and title_raw.endswith('#'):
                pending_title = title_raw[2:-1].strip()
                i += 3
                continue


        if pending_title is not None and raw and not raw[0].isspace() and not raw.startswith('#'):
            current_key = raw.split(':')[0].strip()
            section_labels[current_key] = pending_title
            pending_title = None


        if current_key is not None and raw.startswith('# Example'):
            sample_lines = []
            j = i
            while j < n:
                raw_j = lines[j].rstrip('\n').rstrip('\r')
                if raw_j.startswith('#'):
                    sample_lines.append(lines[j])
                    j += 1
                else:
                    break
            if sample_lines:
                section_samples[current_key] = ''.join(sample_lines)
            i = j
            continue

        i += 1

    return section_labels, section_samples


def _migrate_interval_keys(config: dict) -> bool:
    """
    One-time migration: rename 'interval' → 'update_interval' in every section
    that uses it as the delay-between-runs setting.  Only renames when 'interval'
    is present and 'update_interval' is not, so existing update_interval values
    are never overwritten.
    """
    changed = False

    def _rename(d: dict) -> bool:
        if isinstance(d, dict) and 'interval' in d and 'update_interval' not in d:
            d['update_interval'] = d.pop('interval')
            return True
        return False

    for sub in (config.get('components') or {}).values():
        if _rename(sub):
            changed = True

    if _rename(config.get('lag-monitor') or {}):
        changed = True

    for task in (config.get('tasks') or []):
        if isinstance(task, dict) and _rename(task):
            changed = True

    for entry in ((config.get('commands') or {}).get('status') or {}).values():
        if isinstance(entry, dict) and _rename(entry):
            changed = True

    for sensor in ((config.get('lm_sensors') or {}).get('sensors') or {}).values():
        if isinstance(sensor, dict) and _rename(sensor):
            changed = True

    return changed


def _deep_merge_defaults(config: dict, defaults: dict) -> bool:
    """
    Recursively add keys from `defaults` that are missing in `config`.
    Dict values are merged recursively; lists and scalars are only added when
    the key is absent entirely.  Returns True if any key was added.
    """
    changed = False
    for key, default_val in defaults.items():
        if key not in config:
            config[key] = default_val
            changed = True
        elif isinstance(default_val, dict) and isinstance(config.get(key), dict):
            if _deep_merge_defaults(config[key], default_val):
                changed = True
    return changed


def _extract_header_comments(text: str) -> str:
    """
    Return the leading ASCII-art / comment block that precedes any separator
    bar.  Stops at the first '#################################' line so that
    previously injected separators are never included in the header.
    """
    lines = text.splitlines(keepends=True)
    header = []
    for line in lines:
        stripped = line.strip()
        if stripped == _SEP_BAR:
            break
        if stripped.startswith('#') or stripped == '':
            header.append(line)
        else:
            break
    return ''.join(header)


def _separators_present(text: str, section_labels: dict) -> set:
    return {key for key, title in section_labels.items() if f'# {title}' in text}


def _samples_present(text: str, sample_markers: dict) -> set:
    return {key for key, marker in sample_markers.items() if marker in text}



def _inject_separators_and_samples(yaml_text: str, keys_in_config: set,
                                    section_labels: dict, section_samples: dict) -> str:
    """
    Walk yaml.dump output line by line:
    - Before each known top-level key: prepend a separator box.
    - After each section's content: append the section's sample comment.
    """
    lines = yaml_text.splitlines(keepends=True)
    output = []
    current_section = None

    def _flush_sample():
        if current_section and current_section in section_samples:
            while output and output[-1].strip() == '':
                output.pop()
            output.append('\n')
            output.append(section_samples[current_section].rstrip('\n') + '\n')

    for line in lines:
        rstripped = line.rstrip('\n').rstrip('\r')
        if rstripped and not rstripped[0].isspace() and ':' in rstripped:
            key = rstripped.split(':')[0]
            if key in section_labels and key in keys_in_config:
                _flush_sample()
                while output and output[-1].strip() == '':
                    output.pop()
                if output:
                    output.append('\n')
                output.append(_make_separator(section_labels[key]) + '\n\n')
                current_section = key
        output.append(line)

    _flush_sample()
    return ''.join(output)


def _apply_item_defaults(config: dict, item_defaults: dict) -> bool:
    changed = False
    for section, defaults in item_defaults.items():
        if not isinstance(defaults, dict):
            continue
        items = config.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                if _deep_merge_defaults(item, defaults):
                    changed = True
    return changed


def sync_config(config_path: str, conf_vars_path: str, item_defaults_path: str = None) -> bool:
    """
    Inject any entries missing from config_path that are defined in
    conf_vars_path, add section separator comments and sample entries where
    absent or outdated.  Existing values are never overwritten.

    Returns True if config_path was updated.
    """
    config_file = Path(config_path)
    conf_vars_file = Path(conf_vars_path)
    if not conf_vars_file.exists() or not config_file.exists():
        return False

    conf_vars_text = conf_vars_file.read_text(encoding='utf-8')
    section_labels, section_samples = _parse_conf_vars(conf_vars_text)
    sample_markers = {key: sample.splitlines()[0] for key, sample in section_samples.items()}

    original_text = config_file.read_text(encoding='utf-8')
    try:
        config = yaml.safe_load(original_text) or {}
    except yaml.YAMLError:
        return False
    try:
        defaults = yaml.safe_load(conf_vars_text) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(config, dict) or not isinstance(defaults, dict):
        return False

    item_defaults = {}
    if item_defaults_path:
        item_defaults_file = Path(item_defaults_path)
        if item_defaults_file.exists():
            try:
                item_defaults = yaml.safe_load(item_defaults_file.read_text(encoding='utf-8')) or {}
            except yaml.YAMLError:
                item_defaults = {}

    migration_changed = _migrate_interval_keys(config)
    yaml_changed = _deep_merge_defaults(config, defaults)
    item_defaults_changed = _apply_item_defaults(config, item_defaults)

    current_hash = _compute_samples_hash(section_samples)
    samples_changed = (_read_samples_hash(config_path) != current_hash)

    present_seps = _separators_present(original_text, section_labels)
    needed_seps = {k for k in config if k in section_labels} - present_seps

    present_samples = _samples_present(original_text, sample_markers)
    needed_samples = {k for k in config if k in section_samples} - present_samples

    if not migration_changed and not yaml_changed and not item_defaults_changed and not needed_seps and not needed_samples and not samples_changed:
        return False

    header = _extract_header_comments(original_text)
    body = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
    body = _inject_separators_and_samples(body, set(config.keys()), section_labels, section_samples)
    config_file.write_text(header + body, encoding='utf-8')
    _write_samples_hash(config_path, current_hash)
    return True


