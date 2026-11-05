"""Tests for Phase 7 settings (module and API)."""

import pytest

from app import create_app
from app import settings as app_settings
from app.database import db
from app.settings import DEFAULTS, load, validate_update


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "s.db")
    db.init_db(path)
    return path


@pytest.fixture
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "api.db"), "DEFAULT_SERVICES": []},
        start_collector=False,
    )


def test_defaults_when_nothing_saved(db_path):
    assert load(db_path) == DEFAULTS


def test_save_and_load_roundtrip(db_path):
    merged = dict(DEFAULTS, cpu_warning=60.0, cpu_critical=80.0)
    app_settings.save(db_path, merged, ["cpu_warning", "cpu_critical"])
    loaded = load(db_path)
    assert loaded["cpu_warning"] == 60.0
    assert loaded["cpu_critical"] == 80.0
    assert loaded["memory_warning"] == DEFAULTS["memory_warning"]


def test_load_ignores_corrupt_and_out_of_range_values(db_path):
    db.save_settings(
        db_path,
        {"cpu_warning": "oops", "cpu_critical": "999", "bogus": "1", "disk_warning": "60"},
    )
    loaded = load(db_path)
    assert loaded["cpu_warning"] == DEFAULTS["cpu_warning"]
    assert loaded["cpu_critical"] == DEFAULTS["cpu_critical"]
    assert loaded["disk_warning"] == 60.0
    assert "bogus" not in loaded


def test_validate_accepts_partial_and_pair_updates():
    merged, errors = validate_update(dict(DEFAULTS), {"memory_critical": 95})
    assert errors == {} and merged["memory_critical"] == 95.0
    assert merged["cpu_warning"] == DEFAULTS["cpu_warning"]  # untouched

    # Moving both limits above the old critical value is fine together.
    merged, errors = validate_update(dict(DEFAULTS), {"cpu_warning": 90, "cpu_critical": 95})
    assert errors == {} and merged["cpu_warning"] == 90.0


def test_validate_rejects_bad_input():
    bad_updates = [
        {"nope": 1},
        {"cpu_warning": "70"},
        {"cpu_warning": True},
        {"cpu_warning": float("nan")},
        {"cpu_warning": float("inf")},
        {"cpu_warning": 0},
        {"cpu_critical": 101},
        {"monitor_interval": 1},
        {"monitor_interval": 61},
        {"cpu_warning": 90},   # not below the existing critical 85
        {"cpu_critical": 60},  # not above the existing warning 70
        [],
        {},
        None,
    ]
    for bad in bad_updates:
        merged, errors = validate_update(dict(DEFAULTS), bad)
        assert merged is None and errors, repr(bad)


def test_api_get_settings_shows_defaults_and_env_interval(tmp_path):
    app = create_app(
        {"TESTING": True, "DATABASE_PATH": str(tmp_path / "e.db"),
         "DEFAULT_SERVICES": [], "MONITOR_INTERVAL": 7},
        start_collector=False,
    )
    body = app.test_client().get("/api/settings").get_json()
    assert body["settings"]["monitor_interval"] == 7.0
    assert body["settings"]["cpu_warning"] == 70.0
    assert body["defaults"]["cpu_critical"] == 85.0
    assert body["limits"]["interval_max"] == 60


def test_api_put_saves_applies_and_persists(app):
    client = app.test_client()
    response = client.put(
        "/api/settings", json={"cpu_warning": 60, "cpu_critical": 80, "monitor_interval": 10}
    )
    assert response.status_code == 200
    assert client.get("/api/settings").get_json()["settings"]["cpu_warning"] == 60.0
    assert app.extensions["collector"].interval == 10.0

    # A new app on the same database loads the saved values.
    again = create_app(
        {"TESTING": True, "DATABASE_PATH": app.config["DATABASE_PATH"], "DEFAULT_SERVICES": []},
        start_collector=False,
    )
    assert again.extensions["collector"].interval == 10.0
    assert again.test_client().get("/api/settings").get_json()["settings"]["cpu_critical"] == 80.0


def test_api_put_invalid_returns_400_and_saves_nothing(app):
    client = app.test_client()
    response = client.put("/api/settings", json={"cpu_warning": 95})
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "bad_request"
    assert "cpu_warning" in body["fields"]
    assert client.get("/api/settings").get_json()["settings"]["cpu_warning"] == 70.0


def test_api_put_requires_json_object(app):
    client = app.test_client()
    assert client.put("/api/settings", data="x", content_type="text/plain").status_code == 400
    assert client.put("/api/settings", json=[1, 2]).status_code == 400
