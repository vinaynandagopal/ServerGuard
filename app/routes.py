"""REST API routes. All responses are JSON."""

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from .database import db
from .monitoring.processes import SORT_FIELDS, query_processes
from .monitoring.services import systemd_available, validate_service_name
from . import settings as app_settings
from .alerts.alert_engine import evaluate_level, overall_status

api = Blueprint("api", __name__, url_prefix="/api")


def _collector():
    return current_app.extensions["collector"]


def _get(mapping, *path):
    """Safely walk nested dicts; return None if any step is missing."""
    value = mapping
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


@api.get("/health")
def health():
    """Is the application (and its collector) alive?"""
    collector = _collector()
    running = collector.is_running
    has_data = collector.get_latest() is not None
    return jsonify(
        {
            "status": "ok" if running and has_data else "degraded",
            "collector_running": running,
            "has_data": has_data,
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }
    )


@api.get("/metrics/current")
def metrics_current():
    """Flat summary of the newest measurement, for the dashboard cards."""
    snap = _collector().get_latest()
    if snap is None:
        return (
            jsonify(
                error="warming_up",
                message="No measurement yet. Try again in a few seconds.",
            ),
            503,
        )

    cfg = app_settings.load(_db_path())
    values = {
        "cpu": _get(snap, "cpu", "usage_percent"),
        "memory": _get(snap, "memory", "usage_percent"),
        "disk": _get(snap, "disk", "primary", "usage_percent"),
    }
    levels = {
        k: evaluate_level(v, cfg[f"{k}_warning"], cfg[f"{k}_critical"])
        for k, v in values.items()
    }
    thresholds = {
        k: {"warning": cfg[f"{k}_warning"], "critical": cfg[f"{k}_critical"]}
        for k in values
    }
    open_alerts = db.list_open_alerts(_db_path())
    services = db.list_services(_db_path())

    return jsonify(
        {
            "timestamp": snap["timestamp"],
            "hostname": _get(snap, "system", "hostname"),
            "status": overall_status(levels, open_alerts),
            "levels": levels,
            "thresholds": thresholds,
            "cpu_usage": values["cpu"],
            "memory_usage": values["memory"],
            "disk_usage": values["disk"],
            "network_sent": _get(snap, "network", "bytes_sent"),
            "network_received": _get(snap, "network", "bytes_received"),
            "upload_bytes_per_sec": _get(snap, "network", "upload_bytes_per_sec"),
            "download_bytes_per_sec": _get(snap, "network", "download_bytes_per_sec"),
            "uptime": _get(snap, "system", "uptime_human"),
            "active_alerts": len(open_alerts),
            "services_running": sum(1 for s in services if s["last_status"] == "active"),
            "services_stopped": sum(
                1 for s in services if s["last_status"] in db.DOWN_STATES
            ),
            "errors": snap["errors"],
        }
    )


@api.get("/metrics/detail")
def metrics_detail():
    """The full snapshot: partitions, load average, swap, distro, kernel..."""
    snap = _collector().get_latest()
    if snap is None:
        return (
            jsonify(
                error="warming_up",
                message="No measurement yet. Try again in a few seconds.",
            ),
            503,
        )
    return jsonify(snap)


RANGES = {"5m": 5, "15m": 15, "1h": 60, "24h": 1440}


@api.get("/metrics/history")
def metrics_history():
    """Stored metrics for charts. ?range=5m|15m|1h|24h (default 15m)."""
    key = request.args.get("range", "15m")
    if key not in RANGES:
        return (
            jsonify(
                error="bad_request",
                message="range must be one of: " + ", ".join(RANGES),
            ),
            400,
        )
    points = db.get_metrics_history(current_app.config["DATABASE_PATH"], RANGES[key])
    return jsonify({"range": key, "count": len(points), "points": points})


def _db_path():
    return current_app.config["DATABASE_PATH"]


def _service_view(service):
    return {
        "id": service["id"],
        "name": service["service_name"],
        "enabled": service["enabled"],
        "auto_recovery": service["auto_recovery"],
        "status": service["last_status"] or "pending",
        "last_checked": service["last_checked"],
        "last_status_change": service["last_status_change"],
        "failure_count": service["failure_count"],
        "allowlisted": current_app.extensions["recovery_engine"].is_allowlisted(
            service["service_name"]
        ),
    }


@api.get("/services")
def services_list():
    """Monitored services with their last saved status (no systemctl call here)."""
    items = [_service_view(s) for s in db.list_services(_db_path())]
    summary = {"running": 0, "stopped": 0, "not_installed": 0, "other": 0}
    for item in items:
        status = item["status"]
        if status == "active":
            summary["running"] += 1
        elif status in ("inactive", "failed"):
            summary["stopped"] += 1
        elif status == "not-installed":
            summary["not_installed"] += 1
        else:
            summary["other"] += 1
    return jsonify(
        systemd_available=systemd_available(), summary=summary, services=items
    )


@api.post("/services")
def services_add():
    """Start monitoring a service (read-only). Does not enable recovery."""
    payload = request.get_json(silent=True)
    name = payload.get("service_name") if isinstance(payload, dict) else None
    if not validate_service_name(name):
        return (
            jsonify(
                error="bad_request",
                message=(
                    "service_name must be 1-100 characters: letters, digits and "
                    "@ _ . : - only, starting with a letter or digit."
                ),
            ),
            400,
        )
    try:
        service_id = db.add_service(_db_path(), name)
    except sqlite3.IntegrityError:
        return jsonify(error="conflict", message=f"'{name}' is already monitored."), 409

    # One immediate check so the new row doesn't sit on "pending".
    current_app.extensions["service_monitor"].check_one(
        db.get_service(_db_path(), service_id)
    )
    return jsonify(_service_view(db.get_service(_db_path(), service_id))), 201


@api.delete("/services/<int:service_id>")
def services_delete(service_id):
    if not db.delete_service(_db_path(), service_id):
        return jsonify(error="not_found", message="No such service."), 404
    return jsonify(deleted=service_id)


@api.get("/processes")
def processes_list():
    """Running processes. ?sort=cpu|memory|pid|name &order=asc|desc &search= &limit="""
    sort = request.args.get("sort", "cpu")
    order = request.args.get("order", "desc")
    search = request.args.get("search", "")

    if sort not in SORT_FIELDS:
        return jsonify(error="bad_request",
                       message="sort must be one of: " + ", ".join(SORT_FIELDS)), 400
    if order not in ("asc", "desc"):
        return jsonify(error="bad_request", message="order must be asc or desc."), 400
    if len(search) > 100:
        return jsonify(error="bad_request",
                       message="search must be at most 100 characters."), 400
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify(error="bad_request", message="limit must be an integer."), 400
    if not 1 <= limit <= 500:
        return jsonify(error="bad_request",
                       message="limit must be between 1 and 500."), 400

    processes, updated = current_app.extensions["process_sampler"].get()
    if updated is None:
        return (
            jsonify(error="warming_up",
                    message="No process data yet. Try again in a few seconds."),
            503,
        )

    matched = query_processes(processes, sort, order, search)
    returned = matched[:limit]
    return jsonify(
        {
            "updated": updated,
            "total_processes": len(processes),
            "matched": len(matched),
            "returned": len(returned),
            "processes": returned,
        }
    )


def register_error_handlers(app):
    """Return JSON (not HTML) for errors on /api/ paths."""

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        if request.path.startswith("/api/"):
            return (
                jsonify(error=exc.name.lower().replace(" ", "_"), message=exc.description),
                exc.code,
            )
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        current_app.logger.exception("Unhandled error")
        if request.path.startswith("/api/"):
            # Don't leak internal details to the client.
            return jsonify(error="internal_server_error", message="Unexpected error."), 500
        return "Internal Server Error", 500
