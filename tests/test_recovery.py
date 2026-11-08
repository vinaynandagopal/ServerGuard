"""Tests for the Phase 8 recovery engine, recovery database helpers and settings."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import settings as app_settings
from app.database import db
from app.monitoring import services as svc
from app.recovery import recovery_engine as rec
from app.recovery.recovery_engine import RecoveryEngine, read_allowlist
from app.settings import DEFAULTS, load, validate_update


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class FakeRunner:
    def __init__(self, ok=True, detail=""):
        self.ok, self.detail, self.calls = ok, detail, []

    def __call__(self, name):
        self.calls.append(name)
        return self.ok, self.detail


def fake_checker(status):
    return lambda name: {"name": name, "status": status}


@pytest.fixture(autouse=True)
def _systemd_present(monkeypatch):
    monkeypatch.setattr(svc, "systemd_available", lambda: True)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "r.db")
    db.init_db(path)
    return path


@pytest.fixture
def allowlist(tmp_path):
    path = tmp_path / "allowed-services"
    path.write_text("# comment\nnginx\n\ncron\nbad name\n-rf\n")
    return str(path)


def make_engine(db_path, allowlist, runner=None, status="active", clock=None,
                script="/nonexistent/helper"):
    return RecoveryEngine(
        db_path, allowlist, script,
        runner=runner or FakeRunner(),
        checker=fake_checker(status),
        clock=clock or Clock(),
        sleep=lambda seconds: None,
    )


def add_service(db_path, name, status="inactive", auto=True):
    sid = db.add_service(db_path, name)
    if auto:
        db.set_auto_recovery(db_path, sid, True)
    if status:
        db.record_service_status(db_path, sid, status, "2026-01-01T00:00:00+00:00")
    return sid


def enable_recovery(db_path, **extra):
    values = {"recovery_enabled": "true"}
    values.update({key: str(value) for key, value in extra.items()})
    db.save_settings(db_path, values)


# ----- allowlist ------------------------------------------------------------
def test_read_allowlist_ignores_comments_blanks_and_invalid_names(allowlist):
    assert read_allowlist(allowlist) == {"nginx", "cron"}


def test_read_allowlist_missing_file_is_empty(tmp_path):
    assert read_allowlist(str(tmp_path / "nope")) == set()


# ----- automatic recovery ---------------------------------------------------
def test_auto_recovery_does_nothing_when_globally_disabled(db_path, allowlist):
    add_service(db_path, "nginx")
    runner = FakeRunner()
    assert make_engine(db_path, allowlist, runner).evaluate() == []
    assert runner.calls == []


def test_auto_recovery_restarts_and_verifies(db_path, allowlist):
    sid = add_service(db_path, "nginx")
    enable_recovery(db_path)
    runner = FakeRunner()
    results = make_engine(db_path, allowlist, runner).evaluate()

    assert runner.calls == ["nginx"]
    assert len(results) == 1 and results[0]["result"] == "SUCCESS"
    assert db.get_service(db_path, sid)["last_status"] == "active"
    log = db.list_recovery_logs(db_path)[0]
    assert (log["service_name"], log["action"], log["result"]) == (
        "nginx", "AUTO_RESTART", "SUCCESS")


def test_auto_recovery_skips_ineligible_services(tmp_path, db_path):
    path = tmp_path / "many"
    path.write_text("svc-a\nsvc-b\nsvc-c\nsvc-d\n")
    add_service(db_path, "svc-a", auto=False)               # per-service switch off
    add_service(db_path, "svc-b", status="active")          # not down
    add_service(db_path, "svc-c", status="not-installed")   # not a failure
    add_service(db_path, "svc-d")                           # monitoring disabled below
    with db.connect(db_path) as conn:
        conn.execute("UPDATE services SET enabled = 0 WHERE service_name = 'svc-d'")
    add_service(db_path, "redis")                           # down but not allowlisted

    enable_recovery(db_path)
    runner = FakeRunner()
    assert make_engine(db_path, str(path), runner).evaluate() == []
    assert runner.calls == []


def test_failed_state_is_also_recovered(db_path, allowlist):
    add_service(db_path, "nginx", status="failed")
    enable_recovery(db_path)
    runner = FakeRunner()
    make_engine(db_path, allowlist, runner).evaluate()
    assert runner.calls == ["nginx"]


def test_restart_command_failure_is_recorded(db_path, allowlist):
    sid = add_service(db_path, "nginx")
    enable_recovery(db_path)
    runner = FakeRunner(ok=False, detail="boom")
    results = make_engine(db_path, allowlist, runner).evaluate()
    assert results[0]["result"] == "FAILURE" and results[0]["message"] == "boom"
    assert db.get_service(db_path, sid)["last_status"] == "inactive"


def test_verification_failure_is_a_failure(db_path, allowlist):
    sid = add_service(db_path, "nginx")
    enable_recovery(db_path)
    engine = make_engine(db_path, allowlist, FakeRunner(), status="inactive")
    result = engine.evaluate()[0]
    assert result["result"] == "FAILURE"
    assert "inactive" in result["message"]
    assert db.get_service(db_path, sid)["last_status"] == "inactive"


def test_cooldown_blocks_then_allows(db_path, allowlist):
    add_service(db_path, "nginx")
    enable_recovery(db_path, cooldown_seconds=60, max_restart_attempts=5)
    clock, runner = Clock(), FakeRunner(ok=False, detail="x")
    engine = make_engine(db_path, allowlist, runner, clock=clock)

    engine.evaluate()
    engine.evaluate()
    assert len(runner.calls) == 1        # inside the cooldown
    clock.advance(59)
    engine.evaluate()
    assert len(runner.calls) == 1        # still inside
    clock.advance(2)
    engine.evaluate()
    assert len(runner.calls) == 2        # cooldown over


def test_restart_limit_per_hour_prevents_loops(db_path, allowlist):
    add_service(db_path, "nginx")
    enable_recovery(db_path, cooldown_seconds=10, max_restart_attempts=2)
    clock, runner = Clock(), FakeRunner(ok=False, detail="x")
    engine = make_engine(db_path, allowlist, runner, clock=clock)

    for _ in range(4):
        engine.evaluate()
        clock.advance(20)
    assert len(runner.calls) == 2        # capped at max_restart_attempts

    clock.advance(3600)                  # the window has moved on
    engine.evaluate()
    assert len(runner.calls) == 3


# ----- manual recovery ------------------------------------------------------
def test_manual_restart_not_found_and_not_allowlisted(db_path, allowlist):
    runner = FakeRunner()
    engine = make_engine(db_path, allowlist, runner)
    assert engine.manual_restart(999)["outcome"] == "not_found"
    redis = add_service(db_path, "redis", auto=False)
    assert engine.manual_restart(redis)["outcome"] == "not_allowed"
    assert runner.calls == []


def test_manual_restart_works_without_global_switch_and_logs_manual(db_path, allowlist):
    sid = add_service(db_path, "nginx", auto=False)
    runner = FakeRunner()
    outcome = make_engine(db_path, allowlist, runner).manual_restart(sid)
    assert outcome["outcome"] == "done"
    assert outcome["entry"]["result"] == "SUCCESS"
    assert db.list_recovery_logs(db_path)[0]["action"] == "MANUAL_RESTART"


def test_manual_restart_failure(db_path, allowlist):
    sid = add_service(db_path, "nginx", auto=False)
    engine = make_engine(db_path, allowlist, FakeRunner(ok=False, detail="denied"))
    outcome = engine.manual_restart(sid)
    assert outcome["outcome"] == "done"
    assert outcome["entry"]["result"] == "FAILURE"
    assert outcome["entry"]["message"] == "denied"


def test_manual_restart_requires_systemd(db_path, allowlist, monkeypatch):
    sid = add_service(db_path, "nginx", auto=False)
    monkeypatch.setattr(svc, "systemd_available", lambda: False)
    runner = FakeRunner()
    assert make_engine(db_path, allowlist, runner).manual_restart(sid)["outcome"] == "unavailable"
    assert runner.calls == []


def test_manual_restart_is_busy_while_another_runs(db_path, allowlist):
    sid = add_service(db_path, "nginx", auto=False)
    runner = FakeRunner()
    engine = make_engine(db_path, allowlist, runner)
    engine._lock.acquire()
    try:
        assert engine.manual_restart(sid)["outcome"] == "busy"
    finally:
        engine._lock.release()
    assert runner.calls == []


def test_manual_restarts_do_not_count_toward_the_automatic_limit(db_path, allowlist):
    sid = add_service(db_path, "nginx")
    enable_recovery(db_path, max_restart_attempts=1, cooldown_seconds=10)
    runner = FakeRunner()
    engine = make_engine(db_path, allowlist, runner, status="inactive")  # stays down
    engine.manual_restart(sid)
    engine.evaluate()
    assert runner.calls == ["nginx", "nginx"]


# ----- the real command runner ----------------------------------------------
def real_runner_engine(db_path, allowlist, script):
    return RecoveryEngine(db_path, allowlist, str(script), sudo_bin="sudo",
                          checker=fake_checker("active"), clock=Clock(),
                          sleep=lambda seconds: None)


def test_default_runner_uses_sudo_n_and_an_argument_list(monkeypatch, db_path, allowlist, tmp_path):
    script = tmp_path / "helper"
    script.write_text("#!/bin/true\n")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rec.subprocess, "run", fake_run)
    engine = real_runner_engine(db_path, allowlist, script)
    assert engine._runner("nginx") == (True, "")
    args, kwargs = calls[0]
    assert args == ["sudo", "-n", str(script), "nginx"]
    assert not kwargs.get("shell")
    assert kwargs["timeout"] == rec.RESTART_TIMEOUT


def test_default_runner_reports_a_missing_helper(monkeypatch, db_path, allowlist, tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("must not run without the helper")

    monkeypatch.setattr(rec.subprocess, "run", must_not_run)
    engine = real_runner_engine(db_path, allowlist, tmp_path / "missing")
    ok, message = engine._runner("nginx")
    assert not ok and "not installed" in message


def test_default_runner_explains_a_sudo_password_prompt(monkeypatch, db_path, allowlist, tmp_path):
    script = tmp_path / "helper"
    script.write_text("x")
    monkeypatch.setattr(
        rec.subprocess, "run",
        lambda args, **kw: SimpleNamespace(returncode=1, stdout="",
                                           stderr="sudo: a password is required"),
    )
    ok, message = real_runner_engine(db_path, allowlist, script)._runner("nginx")
    assert not ok and "install_recovery" in message


def test_default_runner_rejects_invalid_names(monkeypatch, db_path, allowlist, tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("must not run for an invalid name")

    monkeypatch.setattr(rec.subprocess, "run", must_not_run)
    engine = real_runner_engine(db_path, allowlist, tmp_path / "helper")
    for bad in ["a;b", "-rf", "$(reboot)", "", "x y"]:
        assert engine._runner(bad)[0] is False


def test_default_runner_handles_a_timeout(monkeypatch, db_path, allowlist, tmp_path):
    script = tmp_path / "helper"
    script.write_text("x")

    def slow(args, **kwargs):
        raise rec.subprocess.TimeoutExpired(cmd="sudo", timeout=40)

    monkeypatch.setattr(rec.subprocess, "run", slow)
    ok, message = real_runner_engine(db_path, allowlist, script)._runner("nginx")
    assert not ok and "timed out" in message


# ----- engine misc ----------------------------------------------------------
def test_engine_call_never_raises(db_path, allowlist):
    engine = make_engine(db_path, allowlist)

    def boom():
        raise RuntimeError("broken")

    engine.evaluate = boom
    engine({})  # a failing evaluation must not crash the collector thread


def test_status_report(db_path, allowlist):
    status = make_engine(db_path, allowlist).status()
    assert status["allowlist"] == ["cron", "nginx"]
    assert status["script_installed"] is False
    assert status["recovery_enabled"] is False
    assert status["max_restart_attempts"] == 2
    assert status["cooldown_seconds"] == 300


# ----- database helpers -----------------------------------------------------
def test_recovery_log_helpers(db_path):
    db.add_recovery_log(db_path, "nginx", "2026-01-01T00:00:01+00:00", "AUTO_RESTART", "FAILURE", "boom")
    db.add_recovery_log(db_path, "nginx", "2026-01-01T00:00:02+00:00", "MANUAL_RESTART", "SUCCESS", "ok")
    db.add_recovery_log(db_path, "cron", "2026-01-01T00:00:03+00:00", "AUTO_RESTART", "SUCCESS", "ok")

    logs = db.list_recovery_logs(db_path)
    assert [entry["service_name"] for entry in logs] == ["cron", "nginx", "nginx"]  # newest first
    assert db.recent_attempts(db_path, "nginx", "AUTO_RESTART", "2026-01-01T00:00:00+00:00") == [
        "2026-01-01T00:00:01+00:00"]
    assert db.recent_attempts(db_path, "nginx", "AUTO_RESTART", "2026-01-01T00:00:02+00:00") == []
    assert db.recovery_counts(db_path) == {"total": 3, "success": 2, "failure": 1}
    with pytest.raises(sqlite3.IntegrityError):
        db.add_recovery_log(db_path, "x", "t", "AUTO_RESTART", "MAYBE", "m")


def test_set_auto_recovery(db_path):
    sid = db.add_service(db_path, "nginx")
    assert db.set_auto_recovery(db_path, sid, True) is True
    assert db.get_service(db_path, sid)["auto_recovery"] is True
    assert db.set_auto_recovery(db_path, sid, False) is True
    assert db.get_service(db_path, sid)["auto_recovery"] is False
    assert db.set_auto_recovery(db_path, 999, True) is False


# ----- recovery settings ----------------------------------------------------
def test_recovery_settings_validation():
    assert DEFAULTS["recovery_enabled"] is False  # off by default
    merged, errors = validate_update(
        dict(DEFAULTS),
        {"recovery_enabled": True, "max_restart_attempts": 3, "cooldown_seconds": 60})
    assert errors == {} and merged["recovery_enabled"] is True
    assert merged["max_restart_attempts"] == 3.0

    bad_updates = [
        {"recovery_enabled": "yes"}, {"recovery_enabled": 1},
        {"max_restart_attempts": 0}, {"max_restart_attempts": 11},
        {"max_restart_attempts": 2.5},
        {"cooldown_seconds": 5}, {"cooldown_seconds": 3601},
    ]
    for bad in bad_updates:
        merged, errors = validate_update(dict(DEFAULTS), bad)
        assert merged is None and errors, repr(bad)


def test_recovery_settings_roundtrip_and_corrupt_values(db_path):
    merged = dict(DEFAULTS, recovery_enabled=True, cooldown_seconds=60.0)
    app_settings.save(db_path, merged, ["recovery_enabled", "cooldown_seconds"])
    loaded = load(db_path)
    assert loaded["recovery_enabled"] is True and loaded["cooldown_seconds"] == 60.0

    db.save_settings(db_path, {"recovery_enabled": "1"})   # a number, not a boolean
    assert load(db_path)["recovery_enabled"] is False
