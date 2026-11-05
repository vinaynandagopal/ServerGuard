"""Application configuration. Every value can be overridden by an env variable."""

import os


class Config:
    HOST = os.environ.get("SERVERGUARD_HOST", "127.0.0.1")
    PORT = int(os.environ.get("SERVERGUARD_PORT", "5000"))
    MONITOR_INTERVAL = float(os.environ.get("SERVERGUARD_INTERVAL", "5"))
