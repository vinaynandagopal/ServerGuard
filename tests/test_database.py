"""Tests for the Phase 4 database layer and history endpoint."""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.database import db
from app.database.writer import MetricsWriter


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_db(path)
    return path


def iso(dt):
    return dt.isoformat()


def test_init_creates_all_tables(db_path):
    with db.connect(db_path) as conn:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"metrics", "alerts", "services", "recovery_logs"} <= names


def test_init_is_idempotent(db_path):
    db.insert_metric(db_path, iso(datetime.now(timezone.utc)), 1, 2, 3, 4, 5)
    db.init_db(db_path)  # must not wipe data
    assert len(db.get_metrics_history(db_path, 5)) == 1


def test_insert_and_history_window(db_path):
    now = datetime.now(timezone.utc)
    db.insert_metric(db_path, iso(now - timedelta(minutes=30)), 10, 20, 30, 1, 1)
    db.insert_metric(db_path, iso(now - timedelta(minutes=2)), 40, 50, 60, 2, 2)

    last_5 = db.get_metrics_history(db_path, 5)
    assert len(last_5) == 1
    assert last_5[0]["cpu_usage"] == 40

    last_hour = db.get_metrics_history(db_path, 60)
    assert [p["cpu_usage"] for p in last_hour] == [10, 40]  # oldest first


def test_history_downsamples_to_max_points(db_path):
    now = datetime.now(timezone.utc)
    for i in range(100):
        db.insert_metric(
            db_path, iso(now - timedelta(seconds=100 - i)), float(i), 50, 50, i, i
        )
    points = db.get_metrics_history(db_path, 60, max_points=10)
    assert len(points) <= 10
    assert points[0]["cpu_usage"] < points[-1]["cpu_usage"]


def test_prune_removes_only_old_rows(db_path):
    now = datetime.now(timezone.utc)
    db.insert_metric(db_path, iso(now - timedelta(days=10)), 1, 1, 1, 1, 1)
    db.insert_metric(db_path, iso(now), 2, 2, 2, 2, 2)
    assert db.prune_metrics(db_path, retention_days=7) == 1
    remaining = db.get_metrics_history(db_path, 60)
    assert len(remaining) == 1 and remaining[0]["cpu_usage"] == 2


def test_sql_injection_text_is_stored_harmlessly(db_path):
    nasty = "x'); DROP TABLE metrics;--"
    db.insert_metric(db_path, nasty, 1, 1, 1, 1, 1)
    with db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1


def test_writer_throttles_saves(db_path):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    writer = MetricsWriter(db_path, save_interval=10, clock=clock)
    ts = iso(datetime.now(timezone.utc))
    snap = {
        "timestamp": ts,
        "cpu": {"usage_percent": 5.0},
        "memory": {"usage_percent": 6.0},
        "disk": {"primary": {"usage_percent": 7.0}},
        "network": {"bytes_sent": 1, "bytes_received": 2},
    }
    writer(snap)  # saved
    clock.now = 3
    writer(snap)  # skipped (too soon)
    clock.now = 11
    writer(snap)  # saved
    assert len(db.get_metrics_history(db_path, 5)) == 2


def test_writer_handles_missing_components(db_path):
    writer = MetricsWriter(db_path, save_interval=0)
    ts = iso(datetime.now(timezone.utc))
    writer({"timestamp": ts, "cpu": None, "memory": None, "disk": None,
            "network": None, "errors": {}})
    rows = db.get_metrics_history(db_path, 5)
    assert len(rows) == 1 and rows[0]["cpu_usage"] is None


def test_history_endpoint_and_validation(db_path):
    db.insert_metric(db_path, iso(datetime.now(timezone.utc)), 12.5, 30, 40, 1, 2)
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path}, start_collector=False)
    client = app.test_client()

    ok = client.get("/api/metrics/history?range=5m")
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["range"] == "5m" and body["count"] == 1
    assert body["points"][0]["cpu_usage"] == 12.5

    bad = client.get("/api/metrics/history?range=999d")
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "bad_request"

    assert client.get("/api/metrics/history").status_code == 200  # default range
