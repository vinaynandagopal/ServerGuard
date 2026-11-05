"""Tests for the Phase 2 monitoring engine."""

import time
from types import SimpleNamespace

import pytest

from app.monitoring import collector as collector_module
from app.monitoring import disk, network
from app.monitoring.collector import MetricsCollector
from app.monitoring.cpu import get_cpu_metrics
from app.monitoring.disk import get_disk_metrics
from app.monitoring.memory import get_memory_metrics
from app.monitoring.network import NetworkMonitor, calculate_rate
from app.monitoring.system_info import format_uptime, get_system_info


# ----- helpers --------------------------------------------------------------
class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def counters(sent, recv):
    return SimpleNamespace(bytes_sent=sent, bytes_recv=recv)


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


# ----- CPU / memory / disk --------------------------------------------------
def test_cpu_metrics_shape():
    cpu = get_cpu_metrics()
    assert 0.0 <= cpu["usage_percent"] <= 100.0
    assert cpu["cores_logical"] >= 1
    assert set(cpu["load_avg"]) == {"1m", "5m", "15m"}


def test_memory_metrics_are_consistent():
    mem = get_memory_metrics()
    assert mem["total_bytes"] > 0
    assert mem["used_bytes"] + mem["available_bytes"] == mem["total_bytes"]
    assert 0.0 <= mem["usage_percent"] <= 100.0


def test_disk_primary_metrics():
    result = get_disk_metrics("/")
    primary = result["primary"]
    assert primary is not None
    assert primary["total_bytes"] > 0
    assert 0.0 <= primary["usage_percent"] <= 100.0
    assert isinstance(result["partitions"], list)


def test_disk_survives_usage_errors(monkeypatch):
    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr(disk.psutil, "disk_usage", denied)
    result = get_disk_metrics("/")
    assert result["primary"] is None
    assert result["partitions"] == []


# ----- network --------------------------------------------------------------
def test_calculate_rate():
    assert calculate_rate(1000, 3000, 2.0) == 1000.0
    assert calculate_rate(3000, 1000, 2.0) == 0.0  # counter reset
    assert calculate_rate(1000, 3000, 0) == 0.0  # no elapsed time


def test_network_monitor_speeds_and_loopback_exclusion(monkeypatch):
    nics = {"lo": counters(1000, 1000), "eth0": counters(0, 0)}
    monkeypatch.setattr(network.psutil, "net_io_counters", lambda pernic=True: nics)
    clock = FakeClock()
    monitor = NetworkMonitor(clock=clock)

    first = monitor.sample()
    assert first["upload_bytes_per_sec"] == 0.0  # nothing to compare yet

    clock.now += 2.0
    nics["eth0"] = counters(2000, 6000)
    nics["lo"] = counters(9_000_000, 9_000_000)  # must be ignored
    second = monitor.sample()

    assert second["interfaces"] == ["eth0"]
    assert second["bytes_sent"] == 2000
    assert second["upload_bytes_per_sec"] == 1000.0
    assert second["download_bytes_per_sec"] == 3000.0


def test_network_monitor_handles_counter_reset(monkeypatch):
    nics = {"eth0": counters(5000, 5000)}
    monkeypatch.setattr(network.psutil, "net_io_counters", lambda pernic=True: nics)
    clock = FakeClock()
    monitor = NetworkMonitor(clock=clock)
    monitor.sample()

    clock.now += 1.0
    nics["eth0"] = counters(100, 100)
    result = monitor.sample()
    assert result["upload_bytes_per_sec"] == 0.0
    assert result["download_bytes_per_sec"] == 0.0


# ----- system info ----------------------------------------------------------
def test_format_uptime():
    assert format_uptime(0) == "0 minutes"
    assert format_uptime(60) == "1 minute"
    assert format_uptime(3600) == "1 hour"
    assert format_uptime(2 * 86400 + 4 * 3600) == "2 days, 4 hours"
    assert format_uptime(86400 + 60) == "1 day, 1 minute"


def test_system_info_has_expected_fields():
    info = get_system_info()
    for key in (
        "hostname",
        "distribution",
        "kernel",
        "boot_time_utc",
        "uptime_seconds",
        "uptime_human",
    ):
        assert key in info
    assert info["hostname"]


# ----- collector ------------------------------------------------------------
def test_collector_snapshot_structure():
    snapshot = MetricsCollector(interval=1).collect_once()
    for key in ("timestamp", "cpu", "memory", "disk", "network", "system", "errors"):
        assert key in snapshot
    assert snapshot["errors"] == {}
    assert snapshot["timestamp"].endswith("+00:00")  # UTC


def test_collector_isolates_component_failure(monkeypatch):
    def broken():
        raise RuntimeError("sensor offline")

    monkeypatch.setattr(collector_module, "get_cpu_metrics", broken)
    snapshot = MetricsCollector(interval=1).collect_once()
    assert snapshot["cpu"] is None
    assert "cpu" in snapshot["errors"]
    assert snapshot["memory"] is not None  # other metrics still work


def test_collector_thread_lifecycle_and_listeners():
    collector = MetricsCollector(interval=0.2)
    seen = []
    collector.add_listener(lambda snap: 1 / 0)  # a broken listener...
    collector.add_listener(seen.append)  # ...must not stop the others

    assert collector.get_latest() is None
    collector.start()
    collector.start()  # second start is a no-op
    try:
        assert wait_until(lambda: collector.get_latest() is not None)
        assert wait_until(lambda: len(seen) >= 2)
        assert collector.is_running
    finally:
        collector.stop()
    assert not collector.is_running


def test_collector_rejects_bad_interval():
    with pytest.raises(ValueError):
        MetricsCollector(interval=0)
