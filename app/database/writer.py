"""Collector listener that saves snapshots to SQLite, throttled."""

import logging
import time

from . import db

logger = logging.getLogger(__name__)

PRUNE_EVERY_SECONDS = 3600


class MetricsWriter:
    def __init__(self, db_path, save_interval=10.0, retention_days=7,
                 clock=time.monotonic):
        self._db_path = db_path
        self._save_interval = save_interval
        self._retention_days = retention_days
        self._clock = clock
        self._last_save = None
        self._last_prune = None

    def __call__(self, snapshot):
        """Called by the collector after every measurement."""
        now = self._clock()
        if self._last_save is not None and now - self._last_save < self._save_interval:
            return  # too soon, skip this one

        def pick(section, *path):
            value = snapshot.get(section)
            for key in path:
                if not isinstance(value, dict):
                    return None
                value = value.get(key)
            return value

        try:
            db.insert_metric(
                self._db_path,
                snapshot["timestamp"],
                pick("cpu", "usage_percent"),
                pick("memory", "usage_percent"),
                pick("disk", "primary", "usage_percent"),
                pick("network", "bytes_sent"),
                pick("network", "bytes_received"),
            )
            self._last_save = now
        except Exception:
            logger.exception("Could not save metrics")
            return

        if self._last_prune is None or now - self._last_prune >= PRUNE_EVERY_SECONDS:
            try:
                deleted = db.prune_metrics(self._db_path, self._retention_days)
                if deleted:
                    logger.info("Pruned %d old metric rows", deleted)
                self._last_prune = now
            except Exception:
                logger.exception("Could not prune old metrics")
