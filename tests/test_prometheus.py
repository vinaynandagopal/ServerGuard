import pytest

from app import create_app
from app.database import db
from app.routes_metrics import render_metrics

SNAP = {
    "cpu": {"usage_percent": 12.5}, "memory": {"usage_percent": 40.0},
    "disk": {"primary": {"usage_percent": 7.0}},
    "network": {"bytes_sent": 10, "bytes_received": 20,
                "upload_bytes_per_sec": 1.0, "download_bytes_per_sec": 2.0},
    "system": {"uptime_seconds": 99},
}
SERVICES = [
    {"service_name": "nginx", "enabled": True, "last_status": "active", "failure_count": 1},
    {"service_name": "cron", "enabled": True, "last_status": "inactive", "failure_count": 0},
    {"service_name": "ghost", "enabled": True, "last_status": "not-installed", "failure_count": 0},
]


def test_render_contains_expected_samples():
    text = render_metrics(SNAP, SERVICES, [{"severity": "CRITICAL"}],
                          {"total": 3, "success": 2, "failure": 1})
    assert "serverguard_cpu_usage_percent 12.5" in text
    assert 'serverguard_service_up{service="nginx"} 1' in text
    assert 'serverguard_service_up{service="cron"} 0' in text
    assert 'service="ghost"} ' not in text.split("serverguard_service_up")[1].split("# HELP")[0]
    assert 'serverguard_active_alerts{severity="CRITICAL"} 1' in text
    assert 'serverguard_recovery_attempts_total{result="failure"} 1' in text
    assert "# TYPE serverguard_network_sent_bytes_total counter" in text


def test_render_without_data_reports_collector_down():
    text = render_metrics(None, [], [], {"total": 0, "success": 0, "failure": 0})
    assert "serverguard_collector_up 0" in text
    assert "serverguard_cpu_usage_percent" not in text


def test_label_values_are_escaped():
    odd = [{"service_name": 'a"b\\c', "enabled": True, "last_status": "active", "failure_count": 0}]
    text = render_metrics(SNAP, odd, [], {"total": 0, "success": 0, "failure": 0})
    assert 'service="a\\"b\\\\c"' in text


def test_metrics_endpoint(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "p.db"),
                      "DEFAULT_SERVICES": []}, start_collector=False)
    response = app.test_client().get("/metrics")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert b"serverguard_collector_up" in response.data
