"""Tests for the Phase 8 recovery API and pages."""

import pytest

from app import create_app
from app.database import db
from app.monitoring import services as svc
from app.recovery.recovery_engine import RecoveryEngine

HEADER = {"X-ServerGuard": "1"}


class FakeRunner:
    def __init__(self):
        self.ok, self.detail, self.calls = True, "", []

    def __call__(self, name):
        self.calls.append(name)
        return self.ok, self.detail


@pytest.fixture(autouse=True)
def _systemd_present(monkeypatch):
    monkeypatch.setattr(svc, "systemd_available", lambda: True)


@pytest.fixture
def setup(tmp_path):
    allow = tmp_path / "allowed"
    allow.write_text("nginx\n")
    script = str(tmp_path / "helper")
    path = str(tmp_path / "api.db")
    app = create_app(
        {"TESTING": True, "DATABASE_PATH": path, "DEFAULT_SERVICES": [],
         "RECOVERY_ALLOWLIST": str(allow), "RECOVERY_SCRIPT": script},
        start_collector=False,
    )
    runner = FakeRunner()
    app.extensions["recovery_engine"] = RecoveryEngine(
        path, str(allow), script, runner=runner,
        checker=lambda name: {"name": name, "status": "active"},
        sleep=lambda seconds: None,
    )
    return app, runner


def add(app, name):
    return db.add_service(app.config["DATABASE_PATH"], name)


def test_services_list_reports_allowlisted(setup):
    app, _ = setup
    add(app, "nginx")
    add(app, "redis")
    body = app.test_client().get("/api/services").get_json()
    by_name = {s["name"]: s for s in body["services"]}
    assert by_name["nginx"]["allowlisted"] is True
    assert by_name["redis"]["allowlisted"] is False


def test_restart_requires_custom_header(setup):
    app, runner = setup
    sid = add(app, "nginx")
    response = app.test_client().post(f"/api/services/{sid}/restart")
    assert response.status_code == 403
    assert runner.calls == []


def test_restart_success(setup):
    app, runner = setup
    sid = add(app, "nginx")
    response = app.test_client().post(f"/api/services/{sid}/restart", headers=HEADER)
    assert response.status_code == 200
    assert response.get_json()["result"] == "SUCCESS"
    assert runner.calls == ["nginx"]


def test_restart_error_responses(setup, monkeypatch):
    app, runner = setup
    client = app.test_client()
    nginx, redis = add(app, "nginx"), add(app, "redis")

    assert client.post("/api/services/999/restart", headers=HEADER).status_code == 404

    refused = client.post(f"/api/services/{redis}/restart", headers=HEADER)
    assert refused.status_code == 403 and refused.get_json()["error"] == "forbidden"

    runner.ok, runner.detail = False, "denied"
    failed = client.post(f"/api/services/{nginx}/restart", headers=HEADER)
    assert failed.status_code == 502
    assert failed.get_json()["error"] == "restart_failed"

    monkeypatch.setattr(svc, "systemd_available", lambda: False)
    unavailable = client.post(f"/api/services/{nginx}/restart", headers=HEADER)
    assert unavailable.status_code == 503


def test_history_endpoint(setup):
    app, _ = setup
    client = app.test_client()
    empty = client.get("/api/recovery/history").get_json()
    assert empty["summary"] == {"total": 0, "success": 0, "failure": 0}
    assert empty["status"]["recovery_enabled"] is False

    sid = add(app, "nginx")
    client.post(f"/api/services/{sid}/restart", headers=HEADER)
    body = client.get("/api/recovery/history").get_json()
    assert body["summary"] == {"total": 1, "success": 1, "failure": 0}
    assert body["status"]["allowlist"] == ["nginx"]
    assert body["logs"][0]["action"] == "MANUAL_RESTART"


def test_history_validates_limit(setup):
    client = setup[0].test_client()
    for query in ["limit=0", "limit=abc", "limit=501"]:
        response = client.get("/api/recovery/history?" + query)
        assert response.status_code == 400, query
        assert response.get_json()["error"] == "bad_request"


def test_toggle_auto_recovery(setup):
    app, _ = setup
    client = app.test_client()
    nginx, redis = add(app, "nginx"), add(app, "redis")

    assert client.put(f"/api/services/{nginx}/recovery", json={"enabled": True}).status_code == 200
    services = client.get("/api/services").get_json()["services"]
    assert {s["name"]: s["auto_recovery"] for s in services}["nginx"] is True

    assert client.put(f"/api/services/{nginx}/recovery", json={"enabled": False}).status_code == 200
    assert client.put(f"/api/services/{redis}/recovery", json={"enabled": True}).status_code == 403
    assert client.put(f"/api/services/{redis}/recovery", json={"enabled": False}).status_code == 200
    assert client.put(f"/api/services/{nginx}/recovery", json={"enabled": "yes"}).status_code == 400
    assert client.put(f"/api/services/{nginx}/recovery", data="x",
                      content_type="text/plain").status_code == 400
    assert client.put("/api/services/999/recovery", json={"enabled": False}).status_code == 404


def test_recovery_page_is_served(setup):
    response = setup[0].test_client().get("/recovery")
    assert response.status_code == 200
    assert b"SERVERGUARD" in response.data


def test_settings_api_accepts_recovery_settings(setup):
    client = setup[0].test_client()
    ok = client.put("/api/settings", json={
        "recovery_enabled": True, "max_restart_attempts": 3, "cooldown_seconds": 60})
    assert ok.status_code == 200
    saved = client.get("/api/settings").get_json()["settings"]
    assert saved["recovery_enabled"] is True and saved["cooldown_seconds"] == 60.0
    assert client.put("/api/settings", json={"recovery_enabled": "yes"}).status_code == 400
