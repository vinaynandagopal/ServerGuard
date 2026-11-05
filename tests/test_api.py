"""Tests for the Phase 3 REST API."""

import pytest

from app import create_app
from app.monitoring.collector import MetricsCollector


class FakeCollector:
    """Stands in for MetricsCollector so tests are fast and deterministic."""

    def __init__(self, snapshot=None, running=True):
        self._snapshot = snapshot
        self._running = running

    @property
    def is_running(self):
        return self._running

    def get_latest(self):
        return self._snapshot


SAMPLE = {
    "timestamp": "2026-01-01T00:00:00+00:00",
    "cpu": {"usage_percent": 35.4},
    "memory": {"usage_percent": 62.8},
    "disk": {"primary": {"usage_percent": 48.2}},
    "network": {
        "bytes_sent": 1000,
        "bytes_received": 2000,
        "upload_bytes_per_sec": 10.0,
        "download_bytes_per_sec": 20.0,
    },
    "system": {"hostname": "testhost", "uptime_human": "2 days, 4 hours"},
    "errors": {},
}


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    """Every API test gets its own throwaway database file."""
    monkeypatch.setattr("tests.test_api.DB_FILE", str(tmp_path / "test.db"), raising=False)


DB_FILE = None


def make_client(collector):
    app = create_app(
        {"TESTING": True, "DATABASE_PATH": DB_FILE}, start_collector=False
    )
    app.extensions["collector"] = collector
    return app.test_client()


def test_health_ok():
    response = make_client(FakeCollector(SAMPLE)).get("/api/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["collector_running"] is True
    assert body["has_data"] is True


def test_health_degraded_without_data():
    response = make_client(FakeCollector(None)).get("/api/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["has_data"] is False


def test_current_returns_503_while_warming_up():
    response = make_client(FakeCollector(None)).get("/api/metrics/current")
    assert response.status_code == 503
    assert response.get_json()["error"] == "warming_up"


def test_current_returns_flat_metrics():
    response = make_client(FakeCollector(SAMPLE)).get("/api/metrics/current")
    body = response.get_json()
    assert response.status_code == 200
    assert body["cpu_usage"] == 35.4
    assert body["memory_usage"] == 62.8
    assert body["disk_usage"] == 48.2
    assert body["network_sent"] == 1000
    assert body["network_received"] == 2000
    assert body["uptime"] == "2 days, 4 hours"
    assert body["hostname"] == "testhost"


def test_current_tolerates_failed_components():
    broken = dict(SAMPLE, cpu=None, errors={"cpu": "sensor offline"})
    body = make_client(FakeCollector(broken)).get("/api/metrics/current").get_json()
    assert body["cpu_usage"] is None
    assert body["memory_usage"] == 62.8
    assert body["errors"] == {"cpu": "sensor offline"}


def test_current_with_real_measurement():
    snapshot = MetricsCollector(interval=1).collect_once()
    response = make_client(FakeCollector(snapshot)).get("/api/metrics/current")
    body = response.get_json()
    assert response.status_code == 200
    assert 0 <= body["memory_usage"] <= 100
    assert 0 <= body["disk_usage"] <= 100


def test_detail_returns_full_snapshot():
    body = make_client(FakeCollector(SAMPLE)).get("/api/metrics/detail").get_json()
    assert body["cpu"]["usage_percent"] == 35.4


def test_unknown_api_path_returns_json_404():
    response = make_client(FakeCollector(SAMPLE)).get("/api/nope")
    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"] == "not_found"


def test_wrong_method_returns_json_405():
    response = make_client(FakeCollector(SAMPLE)).post("/api/health")
    assert response.status_code == 405
    assert response.get_json()["error"] == "method_not_allowed"
