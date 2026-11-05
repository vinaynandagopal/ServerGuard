"""Linux service monitoring through systemd. Strictly read-only."""

import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone

from ..database import db

logger = logging.getLogger(__name__)

# Must start with a letter or digit, so a name can never look like a
# command-line option such as "--help". Matched with fullmatch(), because "$"
# would also accept a trailing newline.
SERVICE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:\-]{0,99}")
SYSTEMCTL_TIMEOUT = 5  # seconds
KNOWN_ACTIVE_STATES = {
    "active", "inactive", "failed", "activating", "deactivating", "reloading",
}


def validate_service_name(name):
    return isinstance(name, str) and SERVICE_NAME_RE.fullmatch(name) is not None


def systemd_available():
    """True when systemd is the running init system (the standard check)."""
    return os.path.isdir("/run/systemd/system")


def _result(name, status, load_state=None, sub_state=None):
    return {
        "name": name,
        "status": status,
        "load_state": load_state,
        "sub_state": sub_state,
    }


def check_service(name):
    """Query one service. Returns a dict whose 'status' is one of:

    active, inactive, failed, activating, deactivating, reloading,
    not-installed, unavailable (no systemd), unknown (query failed).
    """
    if not validate_service_name(name):
        raise ValueError(f"invalid service name: {name!r}")
    if not systemd_available():
        return _result(name, "unavailable")

    try:
        completed = subprocess.run(
            ["systemctl", "show", name,
             "--property=LoadState,ActiveState,SubState", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return _result(name, "unavailable")
    except subprocess.TimeoutExpired:
        logger.warning("systemctl timed out for %s", name)
        return _result(name, "unknown")

    if completed.returncode != 0:
        return _result(name, "unknown")

    props = {}
    for line in completed.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value

    load_state = props.get("LoadState")
    active_state = props.get("ActiveState")
    sub_state = props.get("SubState")

    if load_state == "not-found":
        status = "not-installed"
    elif active_state in KNOWN_ACTIVE_STATES:
        status = active_state
    else:
        status = "unknown"
    return _result(name, status, load_state, sub_state)


class ServiceMonitor:
    """Collector listener: checks every enabled service, throttled.

    API requests only read the saved results from SQLite.
    """

    def __init__(self, db_path, check_interval=10.0, clock=time.monotonic):
        self._db_path = db_path
        self._interval = check_interval
        self._clock = clock
        self._last_check = None

    def __call__(self, snapshot):
        now = self._clock()
        if self._last_check is not None and now - self._last_check < self._interval:
            return
        self._last_check = now
        self.check_all()

    def check_all(self):
        for service in db.list_services(self._db_path):
            if service["enabled"]:
                self.check_one(service)

    def check_one(self, service):
        """Check one service row and store the result. Never raises."""
        try:
            info = check_service(service["service_name"])
            db.record_service_status(
                self._db_path,
                service["id"],
                info["status"],
                datetime.now(timezone.utc).isoformat(),
            )
            return info
        except Exception:
            logger.exception("Check failed for %s", service.get("service_name"))
            return None
