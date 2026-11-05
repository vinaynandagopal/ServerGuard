"""REST API: alerts, CSV export and settings."""

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from . import settings as app_settings
from .database import db

alerts_api = Blueprint("alerts_api", __name__, url_prefix="/api")

CSV_COLUMNS = ("id", "timestamp", "metric", "severity", "message", "status", "resolved_at")


def _db_path():
    return current_app.config["DATABASE_PATH"]


def _bad_request(message, **extra):
    return jsonify(error="bad_request", message=message, **extra), 400


# ----- alerts ---------------------------------------------------------------
@alerts_api.get("/alerts")
def alerts_list():
    """?status=active|resolved|all  &severity=all|warning|critical  &limit=1-500"""
    status = request.args.get("status", "active")
    severity = request.args.get("severity", "all")
    if status not in ("active", "resolved", "all"):
        return _bad_request("status must be one of: active, resolved, all.")
    if severity not in ("all", "warning", "critical"):
        return _bad_request("severity must be one of: all, warning, critical.")
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return _bad_request("limit must be an integer.")
    if not 1 <= limit <= 500:
        return _bad_request("limit must be between 1 and 500.")

    alerts = db.list_alerts(_db_path(), status, severity, limit)
    return jsonify(summary=db.alert_counts(_db_path()), count=len(alerts), alerts=alerts)


@alerts_api.post("/alerts/<int:alert_id>/acknowledge")
def alerts_acknowledge(alert_id):
    result = db.acknowledge_alert(_db_path(), alert_id)
    if result == "not_found":
        return jsonify(error="not_found", message="No such alert."), 404
    if result == "resolved":
        return (
            jsonify(error="conflict", message="Resolved alerts cannot be acknowledged."),
            409,
        )
    return jsonify(db.get_alert(_db_path(), alert_id))


def _csv_safe(value):
    """Stop spreadsheets from treating a cell as a formula."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@alerts_api.get("/reports/export")
def reports_export():
    """Complete alert history as a CSV download."""
    rows = db.list_alerts(_db_path(), "all", "all", limit=None)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_csv_safe(row[column]) for column in CSV_COLUMNS])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="serverguard-alerts-{stamp}.csv"'
        },
    )


# ----- settings -------------------------------------------------------------
def _settings_view(values):
    return {
        "settings": values,
        "defaults": app_settings.defaults_for(current_app.config),
        "limits": {
            "threshold_max": app_settings.THRESHOLD_MAX,
            "interval_min": app_settings.INTERVAL_MIN,
            "interval_max": app_settings.INTERVAL_MAX,
        },
    }


@alerts_api.get("/settings")
def settings_get():
    defaults = app_settings.defaults_for(current_app.config)
    return jsonify(_settings_view(app_settings.load(_db_path(), defaults)))


@alerts_api.put("/settings")
def settings_put():
    """Partial update. Everything is validated before anything is saved."""
    payload = request.get_json(silent=True)
    defaults = app_settings.defaults_for(current_app.config)
    current = app_settings.load(_db_path(), defaults)

    merged, errors = app_settings.validate_update(current, payload)
    if errors:
        message = "; ".join(
            text if key == "_" else f"{key}: {text}" for key, text in errors.items()
        )
        return _bad_request(message, fields=errors)

    app_settings.save(_db_path(), merged, payload.keys())
    if "monitor_interval" in payload:
        # Takes effect after the collector's current wait finishes.
        current_app.extensions["collector"].set_interval(merged["monitor_interval"])
    return jsonify(_settings_view(merged))
