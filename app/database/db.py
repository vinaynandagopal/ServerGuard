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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS never
# alters an existing table, so older databases are upgraded here.
SERVICE_COLUMNS = {
    "last_status_change": "TEXT",
    "failure_count": "INTEGER NOT NULL DEFAULT 0",
}


def _migrate(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(services)")}
    for column, ddl in SERVICE_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE services ADD COLUMN {column} {ddl}")


def init_db(db_path):
    """Create the database file and tables if they don't exist."""
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    with connect(db_path) as conn:
        conn.executescript(schema)
        _migrate(conn)


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


# ----- services -------------------------------------------------------------
SERVICE_FIELDS = (
    "id, service_name, enabled, auto_recovery, last_status, "
    "last_checked, last_status_change, failure_count"
)
DOWN_STATES = ("inactive", "failed")


def _service_dict(row):
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["auto_recovery"] = bool(data["auto_recovery"])
    return data


def add_service(db_path, name):
    """Add a monitored service. Raises sqlite3.IntegrityError if it exists."""
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO services (service_name) VALUES (?)", (name,)
        )
        return cursor.lastrowid


def count_services(db_path):
    with connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]


def list_services(db_path):
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {SERVICE_FIELDS} FROM services ORDER BY service_name"
        ).fetchall()
    return [_service_dict(r) for r in rows]


def get_service(db_path, service_id):
    with connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {SERVICE_FIELDS} FROM services WHERE id = ?", (service_id,)
        ).fetchone()
    return _service_dict(row) if row else None


def delete_service(db_path, service_id):
    """Returns True if a row was deleted."""
    with connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        return cursor.rowcount > 0


def record_service_status(db_path, service_id, status, checked_at):
    """Store a check result and update change/failure tracking.

    A *failure* is only counted on a transition from 'active' to a down state,
    so a service that stays down is not counted again on every check.
    Returns None if the service no longer exists.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_status FROM services WHERE id = ?", (service_id,)
        ).fetchone()
        if row is None:
            return None

        previous = row["last_status"]
        changed = previous != status
        new_failure = previous == "active" and status in DOWN_STATES

        if changed:
            conn.execute(
                "UPDATE services SET last_status = ?, last_checked = ?, "
                "last_status_change = ?, failure_count = failure_count + ? "
                "WHERE id = ?",
                (status, checked_at, checked_at, 1 if new_failure else 0, service_id),
            )
        else:
            conn.execute(
                "UPDATE services SET last_checked = ? WHERE id = ?",
                (checked_at, service_id),
            )
        return {"previous": previous, "changed": changed, "new_failure": new_failure}


# ----- alerts ---------------------------------------------------------------
ALERT_FIELDS = "id, timestamp, metric, severity, message, status, resolved_at"
# Constant SQL fragment (never built from user input).
_OPEN_SQL = "status IN ('ACTIVE', 'ACKNOWLEDGED')"


def create_alert(db_path, timestamp, metric, severity, message):
    """Open a new incident.

    The partial unique index allows one open incident per metric, so a second
    one raises sqlite3.IntegrityError.
    """
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO alerts (timestamp, metric, severity, message) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, metric, severity, message),
        )
        return cursor.lastrowid


def get_alert(db_path, alert_id):
    with connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {ALERT_FIELDS} FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()
    return dict(row) if row else None


def list_open_alerts(db_path):
    """Incidents that are not resolved (active or acknowledged), newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {ALERT_FIELDS} FROM alerts WHERE {_OPEN_SQL} "
            "ORDER BY timestamp DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_alert(db_path, alert_id, severity, message, reactivate=False):
    """Change severity/message of an open incident. Returns True if updated."""
    status_sql = ", status = 'ACTIVE'" if reactivate else ""
    with connect(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE alerts SET severity = ?, message = ?{status_sql} "
            f"WHERE id = ? AND {_OPEN_SQL}",
            (severity, message, alert_id),
        )
        return cursor.rowcount > 0


def resolve_alert(db_path, alert_id, resolved_at):
    """Mark an open incident resolved. Returns True if a row changed."""
    with connect(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE alerts SET status = 'RESOLVED', resolved_at = ? "
            f"WHERE id = ? AND {_OPEN_SQL}",
            (resolved_at, alert_id),
        )
        return cursor.rowcount > 0


def acknowledge_alert(db_path, alert_id):
    """Returns 'ok', 'not_found' or 'resolved'. Acknowledging twice is fine."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()
        if row is None:
            return "not_found"
        if row["status"] == "RESOLVED":
            return "resolved"
        conn.execute(
            "UPDATE alerts SET status = 'ACKNOWLEDGED' WHERE id = ?", (alert_id,)
        )
        return "ok"


def list_alerts(db_path, status="active", severity="all", limit=100):
    """status: active (open) | resolved | all.  severity: all | warning | critical.

    limit=None returns everything (used by the CSV export).
    """
    clauses, params = [], []
    if status == "active":
        clauses.append(_OPEN_SQL)
    elif status == "resolved":
        clauses.append("status = 'RESOLVED'")
    elif status != "all":
        raise ValueError(f"unknown status filter: {status}")

    if severity in ("warning", "critical"):
        clauses.append("severity = ?")
        params.append(severity.upper())
    elif severity != "all":
        raise ValueError(f"unknown severity filter: {severity}")

    sql = f"SELECT {ALERT_FIELDS} FROM alerts"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY timestamp DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def alert_counts(db_path):
    """'active' means open incidents, which includes acknowledged ones."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(severity = 'CRITICAL'), 0) AS critical, "
            "COALESCE(SUM(status IN ('ACTIVE', 'ACKNOWLEDGED')), 0) AS active, "
            "COALESCE(SUM(status = 'ACKNOWLEDGED'), 0) AS acknowledged, "
            "COALESCE(SUM(status = 'RESOLVED'), 0) AS resolved "
            "FROM alerts"
        ).fetchone()
    return dict(row)


# ----- settings -------------------------------------------------------------
def get_all_settings(db_path):
    with connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_settings(db_path, mapping):
    """Upsert key -> JSON text pairs in one transaction."""
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(mapping.items()),
        )
