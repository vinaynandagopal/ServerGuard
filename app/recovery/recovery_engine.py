"""Automated and manual service recovery.

Safety model (each layer is enforced independently):
  1. The administrator's allowlist file is root-owned. A service that is not
     listed there can never be restarted, from the browser or automatically.
  2. This app never runs `systemctl restart` itself. It calls ONE root-owned
     helper through `sudo -n`, and sudoers permits only that helper.
  3. The helper re-checks the name and the allowlist as root.
Automatic restarts need a global switch AND a per-service switch (both off by
default) and are limited by a cooldown and a per-hour cap.
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import settings as app_settings
from ..database import db
from ..monitoring import services as svc

logger = logging.getLogger(__name__)

AUTO_ACTION = "AUTO_RESTART"
MANUAL_ACTION = "MANUAL_RESTART"
RESTART_TIMEOUT = 40      # seconds for the restart command
VERIFY_ATTEMPTS = 5       # status checks after a restart, 1 second apart
VERIFY_DELAY = 1.0
WINDOW_SECONDS = 3600     # rolling window for max_restart_attempts
MESSAGE_LIMIT = 300


def read_allowlist(path):
    """Valid service names from the allowlist file; empty set if unreadable.

    Matching is exact, line for line, the same as the root helper's grep -x.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    return {
        line for line in lines
        if line and not line.startswith("#") and svc.validate_service_name(line)
    }


class RecoveryEngine:
    """Collector listener for automatic recovery; also serves manual restarts."""

    def __init__(self, db_path, allowlist_file, script_path, sudo_bin="sudo",
                 runner=None, checker=None, clock=None, sleep=None):
        self._db_path = db_path
        self._allowlist_file = allowlist_file
        self._script = script_path
        self._sudo = sudo_bin
        self._runner = runner or self._sudo_restart
        self._check = checker or svc.check_service
        self._now = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()       # one restart at a time
        self._limit_warned = set()

    # ----- allowlist and status -----------------------------------------
    def allowlist(self):
        return read_allowlist(self._allowlist_file)

    def is_allowlisted(self, name):
        return name in self.allowlist()

    def status(self):
        cfg = app_settings.load(self._db_path)
        return {
            "recovery_enabled": cfg["recovery_enabled"],
            "script_installed": os.path.exists(self._script),
            "allowlist": sorted(self.allowlist()),
            "max_restart_attempts": int(cfg["max_restart_attempts"]),
            "cooldown_seconds": int(cfg["cooldown_seconds"]),
            "window_seconds": WINDOW_SECONDS,
        }

    # ----- the only place that touches the system ------------------------
    def _sudo_restart(self, name):
        """Run the root helper. Returns (ok, detail). Never uses a shell."""
        if not svc.validate_service_name(name):
            return False, "Invalid service name."
        if not os.path.exists(self._script):
            return False, "The recovery helper is not installed. Run scripts/install_recovery.sh."
        try:
            done = subprocess.run(
                [self._sudo, "-n", self._script, name],   # -n: never prompt
                capture_output=True,
                text=True,
                timeout=RESTART_TIMEOUT,
                check=False,
            )
        except FileNotFoundError:
            return False, "sudo is not available."
        except subprocess.TimeoutExpired:
            return False, f"Restart timed out after {RESTART_TIMEOUT} seconds."

        if done.returncode == 0:
            return True, ""
        text = (done.stderr or done.stdout or "").strip()
        lowered = text.lower()
        if "password" in lowered or "terminal is required" in lowered:
            return False, "sudo is not set up for passwordless recovery. Run scripts/install_recovery.sh."
        return False, f"Restart helper failed (exit {done.returncode}): {text}"[:MESSAGE_LIMIT]

    # ----- one restart attempt -------------------------------------------
    def _verify(self, name):
        status = "unknown"
        for attempt in range(VERIFY_ATTEMPTS):
            status = self._check(name)["status"]
            if status == "active":
                return status
            if attempt < VERIFY_ATTEMPTS - 1:
                self._sleep(VERIFY_DELAY)
        return status

    def _attempt(self, service, action):
        name = service["service_name"]
        ok, detail = self._runner(name)
        status_after = None
        if ok:
            status_after = self._verify(name)
            if status_after == "active":
                result, message = "SUCCESS", "Service restarted and verified active."
            else:
                result = "FAILURE"
                message = f"Restart command succeeded but the service is '{status_after}'."
        else:
            result, message = "FAILURE", detail or "Restart failed."
        message = message[:MESSAGE_LIMIT]

        stamp = self._now().isoformat()
        db.add_recovery_log(self._db_path, name, stamp, action, result, message)
        if status_after is not None:
            # Update the dashboard immediately instead of waiting for the next check.
            db.record_service_status(self._db_path, service["id"], status_after, stamp)

        level = logging.INFO if result == "SUCCESS" else logging.WARNING
        logger.log(level, "%s %s %s: %s", action, name, result, message)
        return {
            "service": name,
            "action": action,
            "result": result,
            "message": message,
            "status_after": status_after,
            "timestamp": stamp,
        }

    # ----- manual ----------------------------------------------------------
    def manual_restart(self, service_id):
        """Outcome is one of: not_found, not_allowed, unavailable, busy, done."""
        service = db.get_service(self._db_path, service_id)
        if service is None:
            return {"outcome": "not_found"}
        if not self.is_allowlisted(service["service_name"]):
            return {"outcome": "not_allowed"}
        if not svc.systemd_available():
            return {"outcome": "unavailable"}
        if not self._lock.acquire(blocking=False):
            return {"outcome": "busy"}
        try:
            entry = self._attempt(service, MANUAL_ACTION)
        finally:
            self._lock.release()
        return {"outcome": "done", "entry": entry}

    # ----- automatic -------------------------------------------------------
    def _blocked(self, name, cfg, now):
        """'limit', 'cooldown' or None. Counted from the recovery log."""
        since = (now - timedelta(seconds=WINDOW_SECONDS)).isoformat()
        times = db.recent_attempts(self._db_path, name, AUTO_ACTION, since)
        if len(times) >= int(cfg["max_restart_attempts"]):
            return "limit"
        if times:
            last = datetime.fromisoformat(times[0])
            if (now - last).total_seconds() < cfg["cooldown_seconds"]:
                return "cooldown"
        return None

    def evaluate(self):
        """Restart eligible down services. Returns the list of attempt results."""
        cfg = app_settings.load(self._db_path)
        if not cfg["recovery_enabled"]:
            return []

        allowed = self.allowlist()
        now = self._now()
        results = []
        for service in db.list_services(self._db_path):
            name = service["service_name"]
            if not (service["enabled"] and service["auto_recovery"]):
                continue
            if service["last_status"] not in db.DOWN_STATES:
                continue
            if name not in allowed:
                continue

            reason = self._blocked(name, cfg, now)
            if reason:
                if reason == "limit" and name not in self._limit_warned:
                    self._limit_warned.add(name)
                    logger.warning(
                        "Automatic recovery for %s paused: restart limit reached", name
                    )
                continue
            self._limit_warned.discard(name)

            if not self._lock.acquire(blocking=False):
                break
            try:
                results.append(self._attempt(service, AUTO_ACTION))
            finally:
                self._lock.release()
        return results

    def __call__(self, snapshot):
        """Collector listener. A failure here must never stop monitoring."""
        try:
            self.evaluate()
        except Exception:
            logger.exception("Automatic recovery failed")
