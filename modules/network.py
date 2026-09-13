import time
import os


class NetworkMonitor:

    def __init__(self, iface):
        self.iface = iface
        self.last = None

    def _read_stat(self, name):
        path = f"/sys/class/net/{self.iface}/statistics/{name}"
        try:
            with open(path) as f:
                return int(f.read().strip())
        except:
            return 0

    def _read_state(self):
        operstate = f"/sys/class/net/{self.iface}/operstate"
        try:
            with open(operstate) as f:
                return f.read().strip()
        except:
            return "down"

    def sample(self):
        now = time.time()

        rx_bytes = self._read_stat("rx_bytes")
        tx_bytes = self._read_stat("tx_bytes")
        state = self._read_state()

        if self.last is None:
            self.last = (rx_bytes, tx_bytes, now)
            return {
                "state": state,
                "rx_mbps": 0.0,
                "tx_mbps": 0.0,
            }

        last_rx, last_tx, last_t = self.last
        dt = max(now - last_t, 0.001)

        rx_mbps = (rx_bytes - last_rx) * 8 / dt / 1_000_000
        tx_mbps = (tx_bytes - last_tx) * 8 / dt / 1_000_000

        self.last = (rx_bytes, tx_bytes, now)

        return {
            "state": state,
            "rx_mbps": round(rx_mbps, 3),
            "tx_mbps": round(tx_mbps, 3),
        }
