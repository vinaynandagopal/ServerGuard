"""SQLite access. Every function opens its own short-lived connection."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


@contextmanager
def connect(db_path):
    """Yield a connection; commit on success, roll back on error, always close."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path):
    """Create the database file and tables if they don't exist."""
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    with connect(db_path) as conn:
        conn.executescript(schema)


# ----- metrics --------------------------------------------------------------
def insert_metric(db_path, timestamp, cpu, memory, disk, sent, received):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO metrics "
            "(timestamp, cpu_usage, memory_usage, disk_usage, "
            " network_sent, network_received) VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, cpu, memory, disk, sent, received),
        )


def _downsample(rows, max_points):
    """Average consecutive rows into at most max_points buckets."""
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    size = -(-len(rows) // max_points)  # ceiling division
    fields = ("cpu_usage", "memory_usage", "disk_usage")
    result = []
    for start in range(0, len(rows), size):
        bucket = rows[start : start + size]
        point = {"timestamp": bucket[-1]["timestamp"]}
        for field in fields:
            values = [r[field] for r in bucket if r[field] is not None]
            point[field] = round(sum(values) / len(values), 1) if values else None
        # Counters only grow, so the newest value in the bucket is the right one.
        point["network_sent"] = bucket[-1]["network_sent"]
        point["network_received"] = bucket[-1]["network_received"]
        result.append(point)
    return result


def get_metrics_history(db_path, minutes, max_points=300, now=None):
    """Metrics from the last `minutes`, oldest first, downsampled for charts."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(minutes=minutes)).isoformat()
    with connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT timestamp, cpu_usage, memory_usage, disk_usage, "
            "network_sent, network_received FROM metrics "
            "WHERE timestamp >= ? ORDER BY timestamp ASC",
            (since,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    return _downsample(rows, max_points)


def prune_metrics(db_path, retention_days, now=None):
    """Delete metrics older than retention_days. Returns rows deleted."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    with connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount
