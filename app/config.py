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
