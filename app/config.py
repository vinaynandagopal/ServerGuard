"""Application configuration. Every value can be overridden by an env variable."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    HOST = os.environ.get("SERVERGUARD_HOST", "127.0.0.1")
    PORT = int(os.environ.get("SERVERGUARD_PORT", "5000"))
    MONITOR_INTERVAL = float(os.environ.get("SERVERGUARD_INTERVAL", "5"))
    DATABASE_PATH = os.environ.get(
        "SERVERGUARD_DB", os.path.join(BASE_DIR, "data", "serverguard.db")
    )
    DB_SAVE_INTERVAL = float(os.environ.get("SERVERGUARD_DB_SAVE_INTERVAL", "10"))
    RETENTION_DAYS = int(os.environ.get("SERVERGUARD_RETENTION_DAYS", "7"))
    SERVICE_CHECK_INTERVAL = float(os.environ.get("SERVERGUARD_SERVICE_INTERVAL", "10"))
    # Seeded only when the services table is empty. Recovery is off for all.
    DEFAULT_SERVICES = ["ssh", "cron", "nginx", "docker"]

    # Recovery (installed by scripts/install_recovery.sh, both root-owned)
    RECOVERY_ALLOWLIST = os.environ.get(
        "SERVERGUARD_ALLOWLIST", "/etc/serverguard/allowed-services"
    )
    RECOVERY_SCRIPT = os.environ.get(
        "SERVERGUARD_RECOVERY_SCRIPT", "/usr/local/sbin/serverguard-restart"
    )
    SUDO_BIN = os.environ.get("SERVERGUARD_SUDO", "sudo")
