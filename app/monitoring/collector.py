"""Background metrics collector.

One MetricsCollector runs one background thread. Everything else (API routes,
alert engine, database writer) reads the latest snapshot or subscribes as a
listener, so requests never trigger their own measurements.
"""

import copy
import logging
import threading
import time
from datetime import datetime, timezone

import psutil

from .cpu import get_cpu_metrics
from .disk import get_disk_metrics
from .memory import get_memory_metrics
from .network import NetworkMonitor
from .system_info import get_system_info

logger = logging.getLogger(__name__)


class MetricsCollector:
    def __init__(self, interval=5.0, network_monitor=None):
        if interval <= 0:
            raise ValueError("interval must be greater than 0")
        self._interval = float(interval)
        self._network = network_monitor or NetworkMonitor()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._latest = None
        self._listeners = []

    # ----- configuration -------------------------------------------------
    @property
    def interval(self):
        return self._interval

    def set_interval(self, seconds):
        """Change the interval; takes effect on the next cycle."""
        if seconds <= 0:
            raise ValueError("interval must be greater than 0")
        self._interval = float(seconds)

    def add_listener(self, callback):
        """Register callback(snapshot), called after every collection.

        Listener errors are logged and never stop the collector. Treat the
        snapshot as read-only.
        """
        self._listeners.append(callback)

    # ----- collection ----------------------------------------------------
    def collect_once(self):
        """Take one measurement of everything and return it as a dict.

        Each component is isolated: if one fails, its value is None and the
        reason is stored in snapshot["errors"]. The others still report.
        """
        errors = {}

        def safe(name, func):
            try:
                return func()
            except Exception as exc:  # deliberately broad: isolate failures
                logger.warning("Metric '%s' failed: %s", name, exc)
                errors[name] = str(exc)
                return None

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu": safe("cpu", get_cpu_metrics),
            "memory": safe("memory", get_memory_metrics),
            "disk": safe("disk", get_disk_metrics),
            "network": safe("network", self._network.sample),
            "system": safe("system", get_system_info),
            "errors": errors,
        }

    def get_latest(self):
        """Return a copy of the newest snapshot, or None before the first one."""
        with self._lock:
            return copy.deepcopy(self._latest)

    # ----- lifecycle -----------------------------------------------------
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start the background thread. Safe to call twice."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="serverguard-collector", daemon=True
        )
        self._thread.start()

    def stop(self, timeout=5.0):
        """Signal the thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if not self._thread.is_alive():
                self._thread = None

    def _notify(self, snapshot):
        for callback in list(self._listeners):
            try:
                callback(snapshot)
            except Exception:
                logger.exception("Collector listener failed")

    def _run(self):
        logger.info("Collector started (interval %.1fs)", self._interval)

        # Prime the counters so the first real reading covers a time window.
        psutil.cpu_percent(interval=None)
        try:
            self._network.sample()
        except Exception:
            logger.exception("Could not prime network counters")
        if self._stop_event.wait(min(1.0, self._interval)):
            return

        while True:
            started = time.monotonic()
            try:
                snapshot = self.collect_once()
                with self._lock:
                    self._latest = snapshot
                self._notify(snapshot)
            except Exception:
                logger.exception("Collection cycle failed")

            remaining = self._interval - (time.monotonic() - started)
            if self._stop_event.wait(max(0.0, remaining)):
                break

        logger.info("Collector stopped")


# ---------------------------------------------------------------------------
# Manual demo:  python -m app.monitoring.collector
# ---------------------------------------------------------------------------
def _fmt_bytes(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024


def _demo_line(snap):
    parts = [snap["timestamp"][11:19] + "Z"]
    if snap["cpu"]:
        parts.append(f"CPU {snap['cpu']['usage_percent']:5.1f}%")
    if snap["memory"]:
        parts.append(f"RAM {snap['memory']['usage_percent']:5.1f}%")
    if snap["disk"] and snap["disk"]["primary"]:
        parts.append(f"Disk {snap['disk']['primary']['usage_percent']:5.1f}%")
    if snap["network"]:
        net = snap["network"]
        parts.append(
            f"Up {_fmt_bytes(net['upload_bytes_per_sec'])}/s "
            f"Down {_fmt_bytes(net['download_bytes_per_sec'])}/s"
        )
    if snap["system"] and snap["system"]["uptime_human"]:
        parts.append(f"Uptime {snap['system']['uptime_human']}")
    if snap["errors"]:
        parts.append(f"ERRORS: {snap['errors']}")
    return " | ".join(parts)


def _demo():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    collector = MetricsCollector(interval=2)
    collector.start()
    print("Collecting every 2 seconds. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(2)
            snapshot = collector.get_latest()
            if snapshot is not None:
                print(_demo_line(snapshot))
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        collector.stop()


if __name__ == "__main__":
    _demo()
