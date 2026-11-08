"""Tests for Phase 6 service monitoring."""

import sqlite3
from types import SimpleNamespace

import pytest

from app.database import db
from app.monitoring import services
from app.monitoring.services import ServiceMonitor, check_service, validate_service_name

ACTIVE_OUTPUT = "LoadState=loaded\nActiveState=active\nSubState=running\n"


def fake_systemctl(monkeypatch, stdout="", returncode=0, calls=None):
    monkeypatch.setattr(services, "systemd_available", lambda: True)

    def fake_run(args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(services.subprocess, "run", fake_run)


def test_validate_accepts_normal_names():
    for name in ["nginx", "ssh", "ssh.service", "systemd-resolved",
                 "getty@tty1.service", "docker", "user@1000.service"]:
        assert validate_service_name(name), name


def test_validate_rejects_dangerous_names():
    bad = ["", " ", "nginx\n", "-rf", "--help", "a b", "a;b", "$(reboot)",
           "../etc/passwd", "a|b", "x" * 101, None, 123]
    for name in bad:
        assert not validate_service_name(name), repr(name)


def test_check_service_active(monkeypatch):
    fake_systemctl(monkeypatch, ACTIVE_OUTPUT)
    info = check_service("nginx")
    assert info["status"] == "active"
    assert info["load_state"] == "loaded"
    assert info["sub_state"] == "running"


def test_check_service_not_installed(monkeypatch):
    fake_systemctl(monkeypatch, "LoadState=not-found\nActiveState=inactive\nSubState=dead\n")
    assert check_service("ghost")["status"] == "not-installed"


def test_check_service_failed(monkeypatch):
    fake_systemctl(monkeypatch, "LoadState=loaded\nActiveState=failed\nSubState=failed\n")
    assert check_service("nginx")["status"] == "failed"


def test_check_service_without_systemd(monkeypatch):
    monkeypatch.setattr(services, "systemd_available", lambda: False)

    def must_not_run(*args, **kwargs):
        raise AssertionError("systemctl must not run without systemd")

    monkeypatch.setattr(services.subprocess, "run", must_not_run)
    assert check_service("nginx")["status"] == "unavailable"


def test_check_service_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(services, "systemd_available", lambda: True)

    def slow(*args, **kwargs):
        raise services.subprocess.TimeoutExpired(cmd="systemctl", timeout=5)

    monkeypatch.setattr(services.subprocess, "run", slow)
    assert check_service("nginx")["status"] == "unknown"


def test_check_service_nonzero_exit_is_unknown(monkeypatch):
    fake_systemctl(monkeypatch, "", returncode=1)
    assert check_service("nginx")["status"] == "unknown"


def test_check_service_rejects_invalid_name(monkeypatch):
    fake_systemctl(monkeypatch, ACTIVE_OUTPUT)
    with pytest.raises(ValueError):
        check_service("nginx; reboot")


def test_systemctl_called_with_argument_list_not_shell(monkeypatch):
    calls = []
    fake_systemctl(monkeypatch, ACTIVE_OUTPUT, calls=calls)
    check_service("nginx")
    args, kwargs = calls[0]
    assert isinstance(args, list)
    assert args[:3] == ["systemctl", "show", "nginx"]
    assert not kwargs.get("shell")
    assert kwargs.get("timeout")


def test_failure_counted_only_on_active_to_down(tmp_path):
    path = str(tmp_path / "s.db")
    db.init_db(path)
    sid = db.add_service(path, "nginx")

    def record(status, stamp):
        return db.record_service_status(path, sid, status, stamp)

    def row():
        return db.get_service(path, sid)

    first = record("active", "t1")
    assert first["changed"] and not first["new_failure"]
    assert row()["failure_count"] == 0

    again = record("active", "t2")
    assert not again["changed"]
    assert row()["last_status_change"] == "t1"  # unchanged status keeps old time
    assert row()["last_checked"] == "t2"

    assert record("inactive", "t3")["new_failure"]
    assert row()["failure_count"] == 1
    assert row()["last_status_change"] == "t3"

    record("inactive", "t4")  # still down: not a new failure
    record("failed", "t5")    # down -> failed: not a new failure
    assert row()["failure_count"] == 1

    record("active", "t6")
    record("not-installed", "t7")  # active -> not-installed is not a failure
    assert row()["failure_count"] == 1

    record("active", "t8")
    record("failed", "t9")  # a genuine second failure
    assert row()["failure_count"] == 2

    assert db.record_service_status(path, 9999, "active", "t") is None


def test_migration_upgrades_old_services_table(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE services (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "service_name TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1, "
        "auto_recovery INTEGER NOT NULL DEFAULT 0, last_status TEXT, last_checked TEXT)"
    )
    conn.execute("INSERT INTO services (service_name) VALUES ('nginx')")
    conn.commit()
    conn.close()

    db.init_db(path)

    service = db.list_services(path)[0]
    assert service["service_name"] == "nginx"
    assert service["failure_count"] == 0
    assert service["last_status_change"] is None
    assert service["auto_recovery"] is False


def test_monitor_check_all_records_status_and_skips_disabled(tmp_path, monkeypatch):
    path = str(tmp_path / "m.db")
    db.init_db(path)
    db.add_service(path, "alpha")
    db.add_service(path, "beta")
    with db.connect(path) as conn:
        conn.execute("UPDATE services SET enabled = 0 WHERE service_name = ?", ("beta",))

    checked = []

    def fake_check(name):
        checked.append(name)
        return {"name": name, "status": "active", "load_state": "loaded",
                "sub_state": "running"}

    monkeypatch.setattr(services, "check_service", fake_check)
    ServiceMonitor(path).check_all()

    assert checked == ["alpha"]
    by_name = {s["service_name"]: s for s in db.list_services(path)}
    assert by_name["alpha"]["last_status"] == "active"
    assert by_name["alpha"]["last_checked"] is not None
    assert by_name["beta"]["last_status"] is None


def test_monitor_throttles_checks():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    monitor = ServiceMonitor("unused.db", check_interval=10, clock=clock)
    calls = []
    monitor.check_all = lambda: calls.append(1)

    monitor({})          # first call always checks
    clock.now = 5
    monitor({})          # too soon
    clock.now = 10
    monitor({})          # interval elapsed
    assert len(calls) == 2
