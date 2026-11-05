"""Tests for the Phase 7 alerts API, CSV export and the extended current-metrics."""

import csv
import io

import pytest

from app import create_app
from app.database import db


class FakeCollector:
    is_running = True

    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_latest(self):
        return self._snapshot


def sample(cpu=10.0, memory=20.0, disk=30.0):
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cpu": None if cpu is None else {"usage_percent": cpu},
        "memory": {"usage_percent": memory},
        "disk": {"primary": {"usage_percent": disk}},
        "network": {"bytes_sent": 1, "bytes_received": 2,
                    "upload_bytes_per_sec": 0.0, "download_bytes_per_sec": 0.0},
        "system": {"hostname": "h", "uptime_human": "1 hour"},
        "errors": {},
    }


@pytest.fixture
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "x.db"), "DEFAULT_SERVICES": []},
        start_collector=False,
    )


@pytest.fixture
def client(app):
    return app.test_client()


def seed(app):
    """cpu = resolved, memory = active (critical), disk = acknowledged."""
    path = app.config["DATABASE_PATH"]
    cpu = db.create_alert(path, "2026-01-01T00:00:01+00:00", "cpu", "WARNING", "CPU high")
    mem = db.create_alert(path, "2026-01-01T00:00:02+00:00", "memory", "CRITICAL", "Memory high")
    disk = db.create_alert(path, "2026-01-01T00:00:03+00:00", "disk", "WARNING", "Disk high")
    db.resolve_alert(path, cpu, "2026-01-01T00:05:00+00:00")
    db.acknowledge_alert(path, disk)
    return {"cpu": cpu, "memory": mem, "disk": disk}


def test_list_alerts_defaults_to_active_with_summary(app, client):
    seed(app)
    body = client.get("/api/alerts").get_json()
    assert {a["metric"] for a in body["alerts"]} == {"memory", "disk"}
    assert body["count"] == 2
    assert body["summary"] == {
        "total": 3, "critical": 1, "active": 2, "acknowledged": 1, "resolved": 1,
    }


def test_list_alerts_filters(app, client):
    seed(app)

    def metrics(query):
        return {a["metric"] for a in client.get("/api/alerts?" + query).get_json()["alerts"]}

    assert metrics("status=resolved") == {"cpu"}
    assert metrics("status=all") == {"cpu", "memory", "disk"}
    assert metrics("status=all&severity=critical") == {"memory"}
    assert metrics("status=all&severity=warning") == {"cpu", "disk"}


def test_list_alerts_validates_input(client):
    for query in ["status=bogus", "severity=bogus", "limit=0", "limit=abc", "limit=501"]:
        response = client.get("/api/alerts?" + query)
        assert response.status_code == 400, query
        assert response.get_json()["error"] == "bad_request"


def test_acknowledge_alert(app, client):
    ids = seed(app)
    response = client.post(f"/api/alerts/{ids['memory']}/acknowledge")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ACKNOWLEDGED"
    assert db.get_alert(app.config["DATABASE_PATH"], ids["memory"])["status"] == "ACKNOWLEDGED"


def test_acknowledge_errors(app, client):
    ids = seed(app)
    assert client.post(f"/api/alerts/{ids['cpu']}/acknowledge").status_code == 409  # resolved
    assert client.post("/api/alerts/999/acknowledge").status_code == 404
    assert client.post("/api/alerts/abc/acknowledge").status_code == 404
    assert client.get(f"/api/alerts/{ids['memory']}/acknowledge").status_code == 405


def test_export_csv(app, client):
    seed(app)
    response = client.get("/api/reports/export")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition and ".csv" in disposition

    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows[0] == ["id", "timestamp", "metric", "severity", "message", "status", "resolved_at"]
    assert len(rows) == 4  # header + all 3 alerts, resolved ones included


def test_export_csv_neutralizes_spreadsheet_formulas(app, client):
    path = app.config["DATABASE_PATH"]
    db.create_alert(path, "2026-01-01T00:00:00+00:00", "cpu", "WARNING", "=1+1")
    rows = list(csv.reader(io.StringIO(client.get("/api/reports/export").get_data(as_text=True))))
    assert rows[1][4] == "'=1+1"


def test_current_metrics_status_levels_and_counts(app):
    path = app.config["DATABASE_PATH"]
    up = db.add_service(path, "nginx")
    down = db.add_service(path, "cron")
    db.record_service_status(path, up, "active", "t")
    db.record_service_status(path, down, "inactive", "t")
    app.extensions["collector"] = FakeCollector(sample(cpu=90.0))

    body = app.test_client().get("/api/metrics/current").get_json()
    assert body["levels"] == {"cpu": "CRITICAL", "memory": "NORMAL", "disk": "NORMAL"}
    assert body["status"] == "CRITICAL"
    assert body["thresholds"]["cpu"] == {"warning": 70.0, "critical": 85.0}
    assert body["services_running"] == 1
    assert body["services_stopped"] == 1
    assert body["active_alerts"] == 0


def test_current_metrics_status_reflects_open_incidents(app):
    path = app.config["DATABASE_PATH"]
    app.extensions["collector"] = FakeCollector(sample())
    client = app.test_client()
    assert client.get("/api/metrics/current").get_json()["status"] == "NORMAL"

    alert_id = db.create_alert(path, "t", "service:nginx", "CRITICAL", "Service nginx is stopped")
    body = client.get("/api/metrics/current").get_json()
    assert body["status"] == "CRITICAL"
    assert body["active_alerts"] == 1

    db.resolve_alert(path, alert_id, "t2")
    assert client.get("/api/metrics/current").get_json()["status"] == "NORMAL"


def test_current_metrics_tolerates_missing_values(app):
    app.extensions["collector"] = FakeCollector(sample(cpu=None))
    body = app.test_client().get("/api/metrics/current").get_json()
    assert body["levels"]["cpu"] == "UNKNOWN"
    assert body["cpu_usage"] is None
    assert body["status"] == "NORMAL"


def test_alerts_and_settings_pages_are_served(client):
    for path in ["/alerts", "/settings"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"SERVERGUARD" in response.data
