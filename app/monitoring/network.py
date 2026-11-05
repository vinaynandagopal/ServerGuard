"""Network metrics, including upload/download speed."""

import time

import psutil

LOOPBACK = "lo"


def calculate_rate(previous_bytes, current_bytes, elapsed_seconds):
    """Bytes per second between two counter readings.

    Returns 0.0 when elapsed time is invalid or the counter went backwards
    (interface reset or removed), rather than reporting a nonsense negative
    or enormous speed.
    """
    if elapsed_seconds <= 0:
        return 0.0
    if current_bytes < previous_bytes:
        return 0.0
    return (current_bytes - previous_bytes) / elapsed_seconds


class NetworkMonitor:
    """Tracks network counters between samples to compute speeds.

    By default it sums every interface except loopback. Pass interface="eth0"
    to watch a single one. The first sample has nothing to compare against, so
    its speeds are reported as 0.0.
    """

    def __init__(self, interface=None, clock=time.monotonic):
        self._interface = interface
        self._clock = clock  # injectable so tests can control time
        self._previous = None  # (timestamp, bytes_sent, bytes_recv)

    def _select(self, per_nic):
        if self._interface is not None:
            if self._interface in per_nic:
                return {self._interface: per_nic[self._interface]}
            return {}
        return {name: c for name, c in per_nic.items() if name != LOOPBACK}

    def sample(self):
        now = self._clock()
        selected = self._select(psutil.net_io_counters(pernic=True))

        sent = sum(c.bytes_sent for c in selected.values())
        received = sum(c.bytes_recv for c in selected.values())

        upload = download = 0.0
        if self._previous is not None:
            prev_time, prev_sent, prev_recv = self._previous
            elapsed = now - prev_time
            upload = calculate_rate(prev_sent, sent, elapsed)
            download = calculate_rate(prev_recv, received, elapsed)

        self._previous = (now, sent, received)

        return {
            "interfaces": sorted(selected),
            "bytes_sent": sent,
            "bytes_received": received,
            "upload_bytes_per_sec": round(upload, 1),
            "download_bytes_per_sec": round(download, 1),
        }
