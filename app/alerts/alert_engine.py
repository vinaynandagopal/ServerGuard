"""Threshold evaluation and incident management.

Rules for one condition (cpu, memory, disk, service:<name>):
  abnormal and no open incident  -> open one
  abnormal and one is open       -> leave it (update only if severity changed)
  normal and one is open         -> resolve it
  cannot be determined           -> leave everything as it is
"""

import logging
import sqlite3
from datetime import datetime, timezone

from .. import settings as app_settings
from ..database import db

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}
SERVICE_DOWN = ("inactive", "failed")
SERVICE_FINE = ("active", "not-installed")  # not installed is not a failure


def evaluate_level(value, warning, critical):
    """NORMAL / WARNING / CRITICAL, or UNKNOWN when there is no value."""
    if value is None:
        return "UNKNOWN"
    if value >= critical:
        return "CRITICAL"
    if value >= warning:
        return "WARNING"
    return "NORMAL"


def overall_status(levels, open_alerts):
    """Worst of the current resource levels and all open incidents."""
    found = [lvl for lvl in levels.values() if lvl in ("WARNING", "CRITICAL")]
    found += [a["severity"] for a in open_alerts]
    if "CRITICAL" in found:
        return "CRITICAL"
    if "WARNING" in found:
        return "WARNING"
    return "NORMAL"


def _dig(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _service_message(name, status):
    if status == "failed":
        return f"Service {name} is in a failed state"
    return f"Service {name} is stopped"


class AlertEngine:
    """Collector listener. Runs after the service monitor each cycle."""

    def __init__(self, db_path):
        self._db_path = db_path

    def __call__(self, snapshot):
        try:
            self.evaluate(snapshot)
        except Exception:
            logger.exception("Alert evaluation failed")

    def evaluate(self, snapshot, now=None):
        """Evaluate one snapshot. Returns {'opened': [], 'updated': [], 'resolved': []}."""
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        cfg = app_settings.load(self._db_path)
        desired = {}      # key -> (severity, message) for abnormal conditions
        evaluable = set() # keys whose state we could determine this cycle

        readings = (
            ("cpu", "CPU usage", _dig(snapshot, "cpu", "usage_percent")),
            ("memory", "Memory usage", _dig(snapshot, "memory", "usage_percent")),
            ("disk", "Disk usage on /", _dig(snapshot, "disk", "primary", "usage_percent")),
        )
        for key, phrase, value in readings:
            if value is None:
                continue  # collection failed: unknown is not "normal"
            evaluable.add(key)
            warning, critical = cfg[f"{key}_warning"], cfg[f"{key}_critical"]
            level = evaluate_level(value, warning, critical)
            if level != "NORMAL":
                limit = critical if level == "CRITICAL" else warning
                desired[key] = (
                    level,
                    f"{phrase} is {value:.1f}% ({level.lower()} threshold {limit:g}%)",
                )

        enabled_keys = set()
        for service in db.list_services(self._db_path):
            if not service["enabled"]:
                continue
            key = f"service:{service['service_name']}"
            enabled_keys.add(key)
            status = service["last_status"]
            if status in SERVICE_DOWN:
                evaluable.add(key)
                desired[key] = ("CRITICAL", _service_message(service["service_name"], status))
            elif status in SERVICE_FINE:
                evaluable.add(key)
            # Any other status (unknown, unavailable, activating, pending) is
            # indeterminate: an existing incident is left untouched.

        summary = {"opened": [], "updated": [], "resolved": []}
        open_alerts = {a["metric"]: a for a in db.list_open_alerts(self._db_path)}

        for key in sorted(evaluable):
            want, current = desired.get(key), open_alerts.get(key)
            if want and current is None:
                try:
                    db.create_alert(self._db_path, stamp, key, want[0], want[1])
                except sqlite3.IntegrityError:
                    logger.info("Incident for %s already open", key)
                    continue
                summary["opened"].append(key)
                logger.warning("ALERT %s: %s", want[0], want[1])
            elif want and current["severity"] != want[0]:
                escalated = SEVERITY_ORDER[want[0]] > SEVERITY_ORDER[current["severity"]]
                db.update_alert(
                    self._db_path, current["id"], want[0], want[1], reactivate=escalated
                )
                summary["updated"].append(key)
                logger.warning("ALERT %s (changed): %s", want[0], want[1])
            elif want is None and current is not None:
                db.resolve_alert(self._db_path, current["id"], stamp)
                summary["resolved"].append(key)
                logger.info("Resolved: %s", current["message"])

        # Incidents for services that are no longer monitored cannot clear
        # themselves, so resolve them.
        for key, current in open_alerts.items():
            if key.startswith("service:") and key not in enabled_keys:
                if db.resolve_alert(self._db_path, current["id"], stamp):
                    summary["resolved"].append(key)
                    logger.info("Resolved (service no longer monitored): %s", key)

        return summary
