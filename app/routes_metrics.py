"""Prometheus text exposition at /metrics."""

from flask import Blueprint, Response, current_app

from .database import db

prom = Blueprint("prom", __name__)


def _esc(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _dig(data, *path):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def render_metrics(snapshot, services, open_alerts, recovery):
    lines = []

    def family(name, kind, help_text, samples):
        """samples: list of (labels dict, value). None values are skipped."""
        samples = [(lab, val) for lab, val in samples if val is not None]
        if not samples:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            if labels:
                text = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
                lines.append(f"{name}{{{text}}} {value}")
            else:
                lines.append(f"{name} {value}")

    family("serverguard_collector_up", "gauge",
           "1 if the collector has produced a measurement.",
           [({}, 1 if snapshot else 0)])

    if snapshot:
        family("serverguard_cpu_usage_percent", "gauge", "CPU usage.",
               [({}, _dig(snapshot, "cpu", "usage_percent"))])
        family("serverguard_memory_usage_percent", "gauge", "Memory usage (total - available).",
               [({}, _dig(snapshot, "memory", "usage_percent"))])
        family("serverguard_disk_usage_percent", "gauge", "Disk usage of /.",
               [({}, _dig(snapshot, "disk", "primary", "usage_percent"))])
        family("serverguard_network_sent_bytes_total", "counter",
               "Bytes sent (all interfaces except loopback).",
               [({}, _dig(snapshot, "network", "bytes_sent"))])
        family("serverguard_network_received_bytes_total", "counter",
               "Bytes received (all interfaces except loopback).",
               [({}, _dig(snapshot, "network", "bytes_received"))])
        family("serverguard_network_upload_bytes_per_second", "gauge", "Upload speed.",
               [({}, _dig(snapshot, "network", "upload_bytes_per_sec"))])
        family("serverguard_network_download_bytes_per_second", "gauge", "Download speed.",
               [({}, _dig(snapshot, "network", "download_bytes_per_sec"))])
        family("serverguard_uptime_seconds", "gauge", "System uptime.",
               [({}, _dig(snapshot, "system", "uptime_seconds"))])

    # Only report services whose state is known: 1 = running, 0 = stopped/failed.
    up, failures = [], []
    for service in services:
        if not service["enabled"]:
            continue
        if service["last_status"] == "active":
            up.append(({"service": service["service_name"]}, 1))
        elif service["last_status"] in db.DOWN_STATES:
            up.append(({"service": service["service_name"]}, 0))
        failures.append(({"service": service["service_name"]}, service["failure_count"]))
    family("serverguard_service_up", "gauge", "1 if the service is active, 0 if down.", up)
    family("serverguard_service_failures_total", "counter",
           "Detected active-to-down transitions.", failures)

    family("serverguard_active_alerts", "gauge", "Open incidents by severity.",
           [({"severity": sev}, sum(1 for a in open_alerts if a["severity"] == sev))
            for sev in ("WARNING", "CRITICAL")])
    family("serverguard_recovery_attempts_total", "counter",
           "Recovery attempts by result.",
           [({"result": "success"}, recovery["success"]),
            ({"result": "failure"}, recovery["failure"])])

    return "\n".join(lines) + "\n"


@prom.get("/metrics")
def metrics():
    path = current_app.config["DATABASE_PATH"]
    body = render_metrics(
        current_app.extensions["collector"].get_latest(),
        db.list_services(path),
        db.list_open_alerts(path),
        db.recovery_counts(path),
    )
    return Response(body, mimetype="text/plain; version=0.0.4; charset=utf-8")
