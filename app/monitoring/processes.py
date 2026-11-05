"""Process monitoring. Read-only: there is deliberately no kill function."""

import logging
import threading
from datetime import datetime, timezone

import psutil

logger = logging.getLogger(__name__)

SORT_FIELDS = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "pid": "pid",
    "name": "name",
}

_ATTRS = ["pid", "name", "username", "cpu_percent", "memory_percent", "status"]


def list_processes():
    """Return every visible process as a list of plain dicts.

    psutil keeps Process objects between calls, so cpu_percent(interval=None)
    measures the time since the previous call. The first call returns zeros.
    Processes we may not inspect come back with None fields, handled below.
    """
    cores = psutil.cpu_count(logical=True) or 1
    result = []
    for proc in psutil.process_iter(_ATTRS):
        info = proc.info
        result.append(
            {
                "pid": info["pid"],
                "name": info["name"] or "?",
                "username": info["username"] or "?",
                # Divide by core count: 100% means the whole machine is busy.
                "cpu_percent": round((info["cpu_percent"] or 0.0) / cores, 1),
                "memory_percent": round(info["memory_percent"] or 0.0, 1),
                "status": info["status"] or "?",
            }
        )
    return result


def query_processes(processes, sort="cpu", order="desc", search=""):
    """Filter by search text (name, user, or exact PID), then sort."""
    if sort not in SORT_FIELDS:
        raise ValueError(f"unknown sort field: {sort}")
    field = SORT_FIELDS[sort]

    term = search.strip().lower()
    if term:
        matched = [
            p for p in processes
            if term in p["name"].lower()
            or term in p["username"].lower()
            or term == str(p["pid"])
        ]
    else:
        matched = list(processes)

    def key(p):
        value = p[field]
        return (value.lower() if isinstance(value, str) else value, p["pid"])

    matched.sort(key=key, reverse=(order == "desc"))
    return matched


class ProcessSampler:
    """Collector listener that refreshes the process list once per cycle."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = []
        self._updated = None
        try:
            list_processes()  # prime psutil's CPU counters
        except Exception:
            logger.exception("Could not prime process sampling")

    def __call__(self, snapshot):
        self.refresh()

    def refresh(self):
        try:
            processes = list_processes()
        except Exception:
            logger.exception("Process sampling failed")
            return
        with self._lock:
            self._latest = processes
            self._updated = datetime.now(timezone.utc).isoformat()

    def get(self):
        """Return (list of processes, UTC timestamp or None before first sample)."""
        with self._lock:
            return list(self._latest), self._updated
