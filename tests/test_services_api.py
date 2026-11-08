"""Tests for the Phase 6 services API and pages."""

import pytest

from app import create_app
from app.database import db
from app.monitoring import services


@pytest.fixture
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "t.db"), "DEFAULT_SERVICES": []},
        start_collector=False,
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def fake_check(monkeypatch):
    monkeypatch.setattr(
        services,
        "check_service",
        lambda name: {"name": name, "status": "active",
                      "load_state": "loaded", "sub_state": "running"},
    )


def test_list_services_with_summary(app, client):
    path = app.config["DATABASE_PATH"]
    for name, status in [("a", "active"), ("b", "inactive"),
                         ("c", "failed"), ("d", "not-installed")]:
        sid = db.add_service(path, name)
        db.record_service_status(path, sid, status, "2026-01-01T00:00:00+00:00")
    db.add_service(path, "e")  # never checked -> "pending"

    body = client.get("/api/services").get_json()
    assert body["summary"] == {"running": 1, "stopped": 2, "not_installed": 1, "other": 1}
    assert "systemd_available" in body
    by_name = {s["name"]: s for s in body["services"]}
    assert by_name["a"]["status"] == "active"
    assert by_name["e"]["status"] == "pending"
    assert by_name["a"]["auto_recovery"] is False  # recovery is off by default


def test_add_service_valid(client, fake_check):
    response = client.post("/api/services", json={"service_name": "nginx"})
    assert response.status_code == 201
    body = response.get_json()
    assert body["name"] == "nginx"
    assert body["status"] == "active"  # checked immediately
    assert body["auto_recovery"] is False
    assert body["failure_count"] == 0


def test_add_service_rejects_invalid_names(app, client, fake_check):
    for bad in ["", "bad name", "a;b", "-rf", "$(reboot)", "x" * 101, 5, None]:
        response = client.post("/api/services", json={"service_name": bad})
        assert response.status_code == 400, repr(bad)
        assert response.get_json()["error"] == "bad_request"
    assert db.count_services(app.config["DATABASE_PATH"]) == 0


def test_add_duplicate_returns_409(client, fake_check):
    assert client.post("/api/services", json={"service_name": "nginx"}).status_code == 201
    duplicate = client.post("/api/services", json={"service_name": "nginx"})
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "conflict"


def test_add_service_requires_json_object(client):
    not_json = client.post("/api/services", data="nginx", content_type="text/plain")
    assert not_json.status_code == 400
    wrong_shape = client.post("/api/services", json=["nginx"])
    assert wrong_shape.status_code == 400


def test_delete_service(app, client):
    sid = db.add_service(app.config["DATABASE_PATH"], "nginx")
    assert client.delete(f"/api/services/{sid}").status_code == 200
    assert db.get_service(app.config["DATABASE_PATH"], sid) is None
    assert client.delete(f"/api/services/{sid}").status_code == 404
    assert client.delete("/api/services/abc").status_code == 404


def test_default_services_seeded_only_once(tmp_path):
    config = {"TESTING": True, "DATABASE_PATH": str(tmp_path / "seed.db"),
              "DEFAULT_SERVICES": ["cron", "nginx", "bad name;"]}
    create_app(config, start_collector=False)
    create_app(config, start_collector=False)  # second start must not duplicate
    names = [s["service_name"] for s in db.list_services(config["DATABASE_PATH"])]
    assert names == ["cron", "nginx"]  # invalid default skipped, no duplicates


def test_pages_are_served(client):
    for path in ["/", "/services", "/processes"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"SERVERGUARD" in response.data
