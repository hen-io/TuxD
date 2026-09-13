import datetime
import json
import os
import select
import struct
import ctypes
import ctypes.util
import threading
import time
from collections import deque

_INOTIFY_AVAILABLE = False
_libc = None

try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    _libc.inotify_init.restype = ctypes.c_int
    _libc.inotify_add_watch.restype = ctypes.c_int
    _libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    _libc.inotify_rm_watch.restype = ctypes.c_int
    _INOTIFY_AVAILABLE = True
except Exception:
    pass

IN_MODIFY = 0x00000002
IN_CREATE = 0x00000100
IN_MOVED_TO = 0x00000080

_EVT_HDR_FMT = "iIII"
_EVT_HDR_SIZE = struct.calcsize(_EVT_HDR_FMT)

_1H  = 3600
_15M = 900
_JSON_DB = "database.json"


def _midnight_ts():
    now = datetime.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _read_last_line(path, log_type="json"):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None, "file is empty"
            f.seek(0)
            full_data = f.read()

        text = full_data.decode("utf-8", errors="replace")

        if log_type == "json":
            for line in reversed(text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line), None
                except json.JSONDecodeError:
                    continue
            try:
                return json.loads(text), None
            except json.JSONDecodeError as e:
                return None, f"json parse error: {e}"

        for line in reversed(text.splitlines()):
            line = line.strip()
            if line:
                return line, None
        return None, f"no valid content found in {path}"
    except Exception as e:
        return None, str(e)


class LogSensorMixin:
    def init_log_sensors(self):
        self.log_sensors = self.config.get("logreader", []) or []

        self._log_hits = {}
        self._last_log_value = {}
        self._hits_lock = threading.Lock()

        has_any = False

        with self._hits_lock:
            data = self._db_read()
            midnight_today = _midnight_ts()
            stored = data.get("logreader_hits", {})

            for s in self.log_sensors:
                slug = s["name"].replace(" ", "_").lower()

                has_today = (
                    s.get("log_hits_sensor_today") == "enabled"
                    or s.get("log_hits_sensor") == "enabled"
                )
                has_1h  = s.get("log_hits_sensor_1h")  == "enabled"
                has_15m = s.get("log_hits_sensor_15m") == "enabled"

                if not (has_today or has_1h or has_15m):
                    continue

                has_any = True
                entry = stored.get(slug, {})


                last_reset  = entry.get("last_reset") or time.time()
                count_today = entry.get("count_today", 0)


                if last_reset < midnight_today:
                    count_today = 0
                    last_reset  = time.time()


                cutoff_1h = time.time() - _1H
                timestamps = deque(
                    ts for ts in entry.get("timestamps", [])
                    if isinstance(ts, (int, float)) and ts >= cutoff_1h
                )

                self._log_hits[slug] = {
                    "has_today":   has_today,
                    "has_1h":      has_1h,
                    "has_15m":     has_15m,
                    "count_today": count_today,
                    "last_reset":  last_reset,
                    "timestamps":  timestamps,
                }
                self._last_log_value[slug] = entry.get("last_value", "")

            data["last_start"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            data["logreader_hits"] = self._hits_to_db_dict()
            self._db_write(data)



    def _db_read(self):
        try:
            with open(_JSON_DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        except Exception as e:
            if self.tty_output:
                print(self._gray(f"logreader: db read error: {e}"))
            return {}

    def _db_write(self, data):
        try:
            with open(_JSON_DB, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            if self.tty_output:
                print(self._gray(f"logreader: db write error: {e}"))

    def _hits_to_db_dict(self):
        now = time.time()
        result = {}
        for slug, h in self._log_hits.items():
            result[slug] = {
                "count_today": h.get("count_today", 0),
                "last_reset":  h.get("last_reset") or now,
                "last_value":  self._last_log_value.get(slug, ""),
            }
            ts = h.get("timestamps")
            if ts:
                result[slug]["timestamps"] = list(ts)
        return result

    def _hits_db_save(self):
        data = self._db_read()
        data["logreader_hits"] = self._hits_to_db_dict()
        self._db_write(data)



    def _do_midnight_reset(self):
        now = time.time()
        with self._hits_lock:
            for slug, h in self._log_hits.items():
                if h["has_today"]:
                    h["count_today"] = 0
                    h["last_reset"]  = now
                    self.publish(
                        f"{self.base_topic}/log_sensor/{slug}_hits_today", "0"
                    )
            self._hits_db_save()

    def _check_midnight_rollover(self, last_midnight):
        current = _midnight_ts()
        if current > last_midnight:
            self._do_midnight_reset()
            return current
        return last_midnight



    def _prune_rolling_windows(self):
        now = time.time()
        cutoff_1h  = now - _1H
        cutoff_15m = now - _15M
        changed = False

        for slug, h in self._log_hits.items():
            if not (h["has_1h"] or h["has_15m"]):
                continue
            old_len = len(h["timestamps"])
            while h["timestamps"] and h["timestamps"][0] < cutoff_1h:
                h["timestamps"].popleft()
            if len(h["timestamps"]) != old_len:
                changed = True

            if h["has_1h"]:
                self.publish(
                    f"{self.base_topic}/log_sensor/{slug}_hits_1h",
                    str(len(h["timestamps"]))
                )
            if h["has_15m"]:
                count_15m = sum(1 for ts in h["timestamps"] if ts >= cutoff_15m)
                self.publish(
                    f"{self.base_topic}/log_sensor/{slug}_hits_15m",
                    str(count_15m)
                )

        if changed:
            with self._hits_lock:
                self._hits_db_save()



    def register_log_sensors(self):
        for s in self.log_sensors:
            slug = s["name"].replace(" ", "_").lower()
            self._sensor_discovery(
                object_id=f"log_sensor_{slug}",
                name=s["name"],
                state_topic=f"{self.base_topic}/log_sensor/{slug}",
                unit=s.get("unit_of_measurement"),
                icon=s.get("icon"),
                device_class=s.get("device_class"),
            )
            result, err = _read_last_line(s["file"], s.get("log_type", "json"))
            if result is not None:
                self._publish_value(s, result)
            else:
                self.publish(f"{self.base_topic}/log_sensor/{slug}", "")
                if err and self.tty_output:
                    print(self._gray(f"logreader [{s['name']}]: {err}"))

            h = self._log_hits.get(slug)
            if h is None:
                continue

            if h["has_today"]:
                self._sensor_discovery(
                    object_id=f"log_sensor_{slug}_hits_today",
                    name=s.get("log_hits_sensor_name") or f"{s['name']} hits today",
                    state_topic=f"{self.base_topic}/log_sensor/{slug}_hits_today",
                    unit="hits",
                    icon=s.get("log_hits_sensor_today_icon") or s.get("icon"),
                    state_class="measurement",
                )
            if h["has_1h"]:
                self._sensor_discovery(
                    object_id=f"log_sensor_{slug}_hits_1h",
                    name=s.get("log_hits_sensor_1h_name") or f"{s['name']} hits (1h)",
                    state_topic=f"{self.base_topic}/log_sensor/{slug}_hits_1h",
                    unit="hits",
                    icon=s.get("log_hits_sensor_1h_icon") or s.get("icon"),
                    state_class="measurement",
                )
            if h["has_15m"]:
                self._sensor_discovery(
                    object_id=f"log_sensor_{slug}_hits_15m",
                    name=s.get("log_hits_sensor_15m_name") or f"{s['name']} hits (15m)",
                    state_topic=f"{self.base_topic}/log_sensor/{slug}_hits_15m",
                    unit="hits",
                    icon=s.get("log_hits_sensor_15m_icon") or s.get("icon"),
                    state_class="measurement",
                )
            self._publish_hits(slug)



    def _json_extract(self, result, json_value):
        if not isinstance(result, dict) or not json_value:
            return result
        node = result
        for key in json_value.split("."):
            if not isinstance(node, dict):
                return ""
            node = node.get(key, "")
        return node

    def _publish_value(self, sensor, result):
        slug = sensor["name"].replace(" ", "_").lower()
        topic = f"{self.base_topic}/log_sensor/{slug}"
        if isinstance(result, dict):
            value = self._json_extract(result, sensor.get("json_value", ""))
        else:
            value = result
        self.publish(topic, str(value) if value is not None else "")

    def _publish_hits(self, slug):
        h = self._log_hits[slug]
        now = time.time()
        if h["has_today"]:
            self.publish(
                f"{self.base_topic}/log_sensor/{slug}_hits_today",
                str(h["count_today"])
            )
        if h["has_1h"]:
            count = sum(1 for ts in h["timestamps"] if ts >= now - _1H)
            self.publish(
                f"{self.base_topic}/log_sensor/{slug}_hits_1h",
                str(count)
            )
        if h["has_15m"]:
            count = sum(1 for ts in h["timestamps"] if ts >= now - _15M)
            self.publish(
                f"{self.base_topic}/log_sensor/{slug}_hits_15m",
                str(count)
            )

    def _publish_log_sensor(self, sensor, result):
        slug = sensor["name"].replace(" ", "_").lower()
        topic = f"{self.base_topic}/log_sensor/{slug}"

        if isinstance(result, dict):
            json_key = sensor.get("json_value")
            value = self._json_extract(result, json_key) if json_key else json.dumps(result, sort_keys=True)
        else:
            value = result if result is not None else ""

        value_str = value if isinstance(value, str) else str(value)
        self.publish(topic, value_str)

        if not value_str:
            return

        self._last_log_value[slug] = value_str

        h = self._log_hits.get(slug)
        if h is None:
            return

        now = time.time()
        if h["has_today"]:
            h["count_today"] += 1
        if h["has_1h"] or h["has_15m"]:
            h["timestamps"].append(now)

        with self._hits_lock:
            self._hits_db_save()

        self._publish_hits(slug)



    def log_sensor_loop(self):
        if not self.log_sensors:
            return

        files_to_sensors = {}
        for s in self.log_sensors:
            files_to_sensors.setdefault(s["file"], []).append(s)

        if _INOTIFY_AVAILABLE:
            self._log_sensor_inotify(files_to_sensors)
        else:
            self._log_sensor_poll(files_to_sensors)

    def _log_sensor_inotify(self, files_to_sensors):
        try:
            ifd = _libc.inotify_init()
            if ifd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init failed")
        except Exception:
            self._log_sensor_poll(files_to_sensors)
            return

        wd_to_path = {}
        path_to_wd = {}
        dir_wd_to_files = {}
        last_content = {}

        def _add_file_watch(path):
            try:
                wd = _libc.inotify_add_watch(ifd, path.encode(), IN_MODIFY)
                if wd >= 0:
                    wd_to_path[wd] = path
                    path_to_wd[path] = wd
            except Exception:
                pass

        def _add_dir_watch(path):
            parent = os.path.dirname(os.path.abspath(path))
            try:
                dwd = _libc.inotify_add_watch(ifd, parent.encode(), IN_CREATE | IN_MOVED_TO)
                if dwd >= 0:
                    dir_wd_to_files.setdefault(dwd, set()).add(path)
            except Exception:
                pass

        for path in files_to_sensors:
            _add_file_watch(path)
            _add_dir_watch(path)

        last_midnight = _midnight_ts()
        last_prune    = time.time()

        try:
            while not self._stop_event.is_set():
                rlist, _, _ = select.select([ifd], [], [], 1.0)

                now = time.time()
                last_midnight = self._check_midnight_rollover(last_midnight)
                if now - last_prune >= 60:
                    self._prune_rolling_windows()
                    last_prune = now

                if not rlist:
                    continue

                raw = os.read(ifd, 65536)
                offset = 0
                triggered = set()

                while offset + _EVT_HDR_SIZE <= len(raw):
                    wd, _mask, _cookie, name_len = struct.unpack_from(_EVT_HDR_FMT, raw, offset)
                    offset += _EVT_HDR_SIZE
                    name = raw[offset:offset + name_len].rstrip(b"\x00").decode("utf-8", errors="replace")
                    offset += name_len

                    if wd in wd_to_path:
                        triggered.add(wd_to_path[wd])
                    elif wd in dir_wd_to_files:
                        for fp in dir_wd_to_files[wd]:
                            if os.path.basename(fp) == name:
                                triggered.add(fp)
                                old_wd = path_to_wd.pop(fp, None)
                                if old_wd is not None:
                                    wd_to_path.pop(old_wd, None)
                                    try:
                                        _libc.inotify_rm_watch(ifd, old_wd)
                                    except Exception:
                                        pass
                                _add_file_watch(fp)

                for path in triggered:
                    try:
                        sensors = files_to_sensors.get(path, [])
                        log_type = sensors[0].get("log_type", "json") if sensors else "json"
                        read_delay = float(sensors[0].get("read_delay", 0)) if sensors else 0
                        if read_delay > 0:
                            time.sleep(read_delay)
                        result, err = _read_last_line(path, log_type)
                        if result is not None:


                            ck = json.dumps(result, sort_keys=True) if isinstance(result, dict) else str(result)
                            prev = last_content.get(path)
                            now_ts = time.time()
                            if prev and prev[0] == ck and now_ts - prev[1] < 0.2:
                                continue
                            last_content[path] = (ck, now_ts)
                            for s in sensors:
                                self._publish_log_sensor(s, result)
                        elif err and self.tty_output:
                            print(self._gray(f"logreader [{path}]: {err}"))
                    except Exception as e:
                        if self.tty_output:
                            print(self._gray(f"logreader error ({path}): {e}"))
        finally:
            try:
                os.close(ifd)
            except Exception:
                pass

    def _log_sensor_poll(self, files_to_sensors):
        mtimes      = {}
        last_midnight = _midnight_ts()
        last_prune    = time.time()

        while not self._stop_event.is_set():
            now = time.time()
            last_midnight = self._check_midnight_rollover(last_midnight)
            if now - last_prune >= 60:
                self._prune_rolling_windows()
                last_prune = now

            for path, sensors in files_to_sensors.items():
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtimes.get(path) != mtime:
                    mtimes[path] = mtime
                    try:
                        log_type = sensors[0].get("log_type", "json") if sensors else "json"
                        read_delay = float(sensors[0].get("read_delay", 0)) if sensors else 0
                        if read_delay > 0:
                            time.sleep(read_delay)
                        result, err = _read_last_line(path, log_type)
                        if result is not None:
                            for s in sensors:
                                self._publish_log_sensor(s, result)
                        elif err and self.tty_output:
                            print(self._gray(f"logreader [{path}]: {err}"))
                    except Exception as e:
                        if self.tty_output:
                            print(self._gray(f"logreader error ({path}): {e}"))
            self._stop_event.wait(timeout=1.0)
