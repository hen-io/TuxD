import base64
import fcntl
import os
import pty
import signal
import struct
import termios
import threading


class LiveTtyMixin:

    _MAX_TTY_SESSIONS = 2

    def init_live_tty(self):
        self._tty_sessions = {}
        self._tty_sessions_lock = threading.Lock()


    def open_tty_session(self, session_id, cols, rows, password=None):
        if not self._terminal_input_enabled():
            self.publish_tty_exit(session_id, -1)
            return

        required_password = self.config.get("device", {}).get("password") or ""
        if required_password and password != required_password:
            self.publish_tty_exit(
                session_id, -2,
                reason="Incorrect password" if password else "Password required"
            )
            return

        with self._tty_sessions_lock:
            if session_id in self._tty_sessions:
                return
            if len(self._tty_sessions) >= self._MAX_TTY_SESSIONS:
                self.publish_tty_exit(session_id, -1)
                return

        shell = os.environ.get("SHELL") or "/bin/bash"

        home_dir = os.path.expanduser("~")
        term_value = self._tty_pick_term()
        child_env = dict(os.environ)
        child_env["TERM"] = term_value

        try:
            pid, master_fd = pty.fork()
        except Exception:
            self.publish_tty_exit(session_id, -1)
            return

        if pid == 0:
            try:
                os.chdir(home_dir)
                os.execvpe(shell, [shell], child_env)
            except Exception:
                os._exit(1)
            return

        self._tty_set_size(master_fd, cols, rows)

        with self._tty_sessions_lock:
            self._tty_sessions[session_id] = {"master_fd": master_fd, "pid": pid}

        threading.Thread(
            target=self._tty_reader_loop, args=(session_id, master_fd, pid), daemon=True
        ).start()

    def _tty_reader_loop(self, session_id, master_fd, pid):
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.publish_tty_data(session_id, chunk)
        finally:
            self._tty_finish_session(session_id, master_fd, pid)

    def _tty_finish_session(self, session_id, master_fd, pid):
        with self._tty_sessions_lock:
            self._tty_sessions.pop(session_id, None)
        try:
            os.close(master_fd)
        except Exception:
            pass
        code = -1
        try:
            _, status = os.waitpid(pid, 0)
            code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        except Exception:
            pass
        self.publish_tty_exit(session_id, code)

    def close_tty_session(self, session_id):
        with self._tty_sessions_lock:
            info = self._tty_sessions.get(session_id)
        if not info:
            return
        try:
            os.killpg(info["pid"], signal.SIGTERM)
        except Exception:
            try:
                os.kill(info["pid"], signal.SIGTERM)
            except Exception:
                return

        def _force_kill():
            with self._tty_sessions_lock:
                still_open = session_id in self._tty_sessions
            if still_open:
                try:
                    os.killpg(info["pid"], signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(info["pid"], signal.SIGKILL)
                    except Exception:
                        pass

        threading.Timer(3.0, _force_kill).start()

    def close_all_tty_sessions(self):
        with self._tty_sessions_lock:
            session_ids = list(self._tty_sessions.keys())
        for session_id in session_ids:
            self.close_tty_session(session_id)

    def write_tty_session(self, session_id, data_b64):
        with self._tty_sessions_lock:
            info = self._tty_sessions.get(session_id)
        if not info:
            return
        try:
            os.write(info["master_fd"], base64.b64decode(data_b64))
        except Exception:
            pass

    def resize_tty_session(self, session_id, cols, rows):
        with self._tty_sessions_lock:
            info = self._tty_sessions.get(session_id)
        if not info:
            return
        self._tty_set_size(info["master_fd"], cols, rows)

    def _tty_pick_term(self):
        try:
            import curses
            curses.setupterm("xterm-256color")
            return "xterm-256color"
        except Exception:
            return "xterm"

    def _tty_set_size(self, master_fd, cols, rows):
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(cols), 0, 0))
        except Exception:
            pass


    def publish_tty_data(self, session_id, chunk: bytes):
        self._send_wait({
            "type": "tty_data",
            "session": session_id,
            "data": base64.b64encode(chunk).decode("ascii"),
        }, timeout=2.0)

    def publish_tty_exit(self, session_id, code, reason=None):
        payload = {"type": "tty_exit", "session": session_id, "code": code}
        if reason:
            payload["reason"] = reason
        self._send_wait(payload, timeout=2.0)
