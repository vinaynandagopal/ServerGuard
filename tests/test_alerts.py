"""Tests for the Phase 7 alert engine and alert database functions."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app.alerts.alert_engine import AlertEngine, evaluate_level, overall_status
from app.database import db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "a.db")
    db.init_db(path)
    return path


@pytest.fixture
def engine(db_path):
    return AlertEngine(db_path)


def snap(cpu=10.0, memory=20.0, disk=30.0):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": None if cpu is None else {"usage_percent": cpu},
        "memory": None if memory is None else {"usage_percent": memory},
        "disk": None if disk is None else {"primary": {"usage_percent": disk}},
        "errors": {},
    }


def all_alerts(path):
    return db.list_alerts(path, "all", "all", limit=None)


def test_evaluate_level_boundaries():
    assert evaluate_level(69.9, 70, 85) == "NORMAL"
    assert evaluate_level(70, 70, 85) == "WARNING"
    assert evaluate_level(84.9, 70, 85) == "WARNING"
    assert evaluate_level(85, 70, 85) == "CRITICAL"
    assert evaluate_level(100, 70, 85) == "CRITICAL"
    assert evaluate_level(None, 70, 85) == "UNKNOWN"


def test_overall_status_is_worst_of_levels_and_open_alerts():
    assert overall_status({"cpu": "NORMAL", "memory": "UNKNOWN"}, []) == "NORMAL"
    assert overall_status({"cpu": "WARNING"}, []) == "WARNING"
    assert overall_status({"cpu": "NORMAL"}, [{"severity": "CRITICAL"}]) == "CRITICAL"
    assert overall_status({"cpu": "WARNING"}, [{"severity": "CRITICAL"}]) == "CRITICAL"


def test_normal_snapshot_creates_no_alerts(engine, db_path):
    result = engine.evaluate(snap())
    assert result == {"opened": [], "updated": [], "resolved": []}
    assert all_alerts(db_path) == []


def test_warning_alert_is_created_with_details(engine, db_path):
    result = engine.evaluate(snap(cpu=75.0))
    alerts = all_alerts(db_path)
    assert result["opened"] == ["cpu"]
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["metric"] == "cpu"
    assert alert["severity"] == "WARNING"
    assert alert["status"] == "ACTIVE"
    assert "75.0%" in alert["message"]
    assert alert["resolved_at"] is None


def test_memory_and_disk_thresholds_are_inclusive(engine, db_path):
    engine.evaluate(snap(memory=75.0, disk=90.0))  # exactly on the default limits
    found = {a["metric"]: a["severity"] for a in db.list_open_alerts(db_path)}
    assert found == {"memory": "WARNING", "disk": "CRITICAL"}


def test_duplicate_alerts_are_suppressed(engine, db_path):
    for _ in range(5):
        result = engine.evaluate(snap(cpu=90.0))
    assert len(all_alerts(db_path)) == 1
    assert result == {"opened": [], "updated": [], "resolved": []}


def test_alert_resolves_and_can_recur(engine, db_path):
    engine.evaluate(snap(cpu=90.0))
    result = engine.evaluate(snap(cpu=10.0))
    assert result["resolved"] == ["cpu"]
    first = all_alerts(db_path)[0]
    assert first["status"] == "RESOLVED"
    assert first["resolved_at"] is not None

    engine.evaluate(snap(cpu=90.0))  # the problem comes back
    assert len(all_alerts(db_path)) == 2
    assert len(db.list_open_alerts(db_path)) == 1


def test_escalation_updates_the_same_incident(engine, db_path):
    engine.evaluate(snap(cpu=75.0))
    result = engine.evaluate(snap(cpu=90.0))
    alerts = all_alerts(db_path)
    assert result["updated"] == ["cpu"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "CRITICAL"


def test_escalation_reactivates_an_acknowledged_incident(engine, db_path):
    engine.evaluate(snap(cpu=75.0))
    alert_id = db.list_open_alerts(db_path)[0]["id"]
    db.acknowledge_alert(db_path, alert_id)
    engine.evaluate(snap(cpu=90.0))
    assert db.get_alert(db_path, alert_id)["status"] == "ACTIVE"


def test_acknowledged_stays_acknowledged_until_resolved(engine, db_path):
    engine.evaluate(snap(cpu=90.0))
    alert_id = db.list_open_alerts(db_path)[0]["id"]
    db.acknowledge_alert(db_path, alert_id)

    result = engine.evaluate(snap(cpu=90.0))
    assert result["updated"] == []
    assert db.get_alert(db_path, alert_id)["status"] == "ACKNOWLEDGED"

    engine.evaluate(snap(cpu=10.0))
    assert db.get_alert(db_path, alert_id)["status"] == "RESOLVED"


def test_failed_component_does_not_resolve_an_incident(engine, db_path):
    engine.evaluate(snap(cpu=90.0))
    engine.evaluate(snap(cpu=None))  # CPU collection failed: unknown, not normal
    assert len(db.list_open_alerts(db_path)) == 1


def test_custom_thresholds_apply_immediately(engine, db_path):
    db.save_settings(db_path, {"cpu_warning": "20", "cpu_critical": "30"})
    engine.evaluate(snap(cpu=25.0))
    assert db.list_open_alerts(db_path)[0]["severity"] == "WARNING"
    engine.evaluate(snap(cpu=30.0))
    assert db.list_open_alerts(db_path)[0]["severity"] == "CRITICAL"


def test_service_down_opens_critical_and_resolves_on_recovery(engine, db_path):
    sid = db.add_service(db_path, "nginx")
    db.record_service_status(db_path, sid, "active", "t1")
    engine.evaluate(snap())
    assert db.list_open_alerts(db_path) == []

    db.record_service_status(db_path, sid, "inactive", "t2")
    engine.evaluate(snap())
    alert = db.list_open_alerts(db_path)[0]
    assert alert["metric"] == "service:nginx"
    assert alert["severity"] == "CRITICAL"
    assert "nginx" in alert["message"]

    engine.evaluate(snap())  # still down: no duplicate
    assert len(all_alerts(db_path)) == 1

    db.record_service_status(db_path, sid, "active", "t3")
    engine.evaluate(snap())
    assert db.list_open_alerts(db_path) == []
    assert all_alerts(db_path)[0]["status"] == "RESOLVED"


def test_not_installed_or_unknown_service_is_not_an_incident(engine, db_path):
    sid = db.add_service(db_path, "ghost")
    for status in ["not-installed", "unknown", "unavailable", "activating"]:
        db.record_service_status(db_path, sid, status, "t")
        engine.evaluate(snap())
    assert all_alerts(db_path) == []


def test_indeterminate_status_keeps_an_existing_incident(engine, db_path):
    sid = db.add_service(db_path, "nginx")
    db.record_service_status(db_path, sid, "inactive", "t1")
    engine.evaluate(snap())
    db.record_service_status(db_path, sid, "unknown", "t2")
    engine.evaluate(snap())
    assert len(db.list_open_alerts(db_path)) == 1


def test_removed_or_disabled_service_resolves_its_incident(engine, db_path):
    gone = db.add_service(db_path, "nginx")
    quiet = db.add_service(db_path, "cron")
    db.record_service_status(db_path, gone, "inactive", "t")
    db.record_service_status(db_path, quiet, "inactive", "t")
    engine.evaluate(snap())
    assert len(db.list_open_alerts(db_path)) == 2

    db.delete_service(db_path, gone)
    with db.connect(db_path) as conn:
        conn.execute("UPDATE services SET enabled = 0 WHERE service_name = ?", ("cron",))
    engine.evaluate(snap())
    assert db.list_open_alerts(db_path) == []


def test_engine_call_never_raises(engine):
    def boom(snapshot):
        raise RuntimeError("broken")

    engine.evaluate = boom
    engine({})  # a failing evaluation must not crash the collector thread


def test_database_allows_one_open_incident_per_metric(db_path):
    first = db.create_alert(db_path, "t1", "cpu", "WARNING", "m")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_alert(db_path, "t2", "cpu", "CRITICAL", "m")
    db.resolve_alert(db_path, first, "t3")
    db.create_alert(db_path, "t4", "cpu", "WARNING", "m")  # allowed after resolution


def test_list_alerts_filters_and_counts(db_path):
    a = db.create_alert(db_path, "2026-01-01T00:00:01+00:00", "cpu", "WARNING", "m")
    db.create_alert(db_path, "2026-01-01T00:00:02+00:00", "memory", "CRITICAL", "m")
    c = db.create_alert(db_path, "2026-01-01T00:00:03+00:00", "disk", "WARNING", "m")
    db.resolve_alert(db_path, a, "2026-01-01T00:05:00+00:00")
    db.acknowledge_alert(db_path, c)

    def metrics(status, severity="all"):
        return {x["metric"] for x in db.list_alerts(db_path, status, severity)}

    assert metrics("active") == {"memory", "disk"}
    assert metrics("resolved") == {"cpu"}
    assert metrics("all") == {"cpu", "memory", "disk"}
    assert metrics("all", "critical") == {"memory"}
    assert db.alert_counts(db_path) == {
        "total": 3, "critical": 1, "active": 2, "acknowledged": 1, "resolved": 1,
    }
    with pytest.raises(ValueError):
        db.list_alerts(db_path, "bogus")
    with pytest.raises(ValueError):
        db.list_alerts(db_path, "all", "bogus")


def test_acknowledge_results(db_path):
    assert db.acknowledge_alert(db_path, 999) == "not_found"
    alert_id = db.create_alert(db_path, "t", "cpu", "WARNING", "m")
    assert db.acknowledge_alert(db_path, alert_id) == "ok"
    assert db.acknowledge_alert(db_path, alert_id) == "ok"  # idempotent
    db.resolve_alert(db_path, alert_id, "t2")
    assert db.acknowledge_alert(db_path, alert_id) == "resolved"
