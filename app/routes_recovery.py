"""REST API: recovery history, manual restart, per-service auto-recovery."""

from flask import Blueprint, current_app, jsonify, request

from .database import db

recovery_api = Blueprint("recovery_api", __name__, url_prefix="/api")


def _db_path():
    return current_app.config["DATABASE_PATH"]


def _engine():
    return current_app.extensions["recovery_engine"]


@recovery_api.get("/recovery/history")
def recovery_history():
    """?limit=1-500 (default 100). Includes summary counts and engine status."""
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return jsonify(error="bad_request", message="limit must be an integer."), 400
    if not 1 <= limit <= 500:
        return jsonify(error="bad_request", message="limit must be between 1 and 500."), 400

    logs = db.list_recovery_logs(_db_path(), limit)
    return jsonify(
        summary=db.recovery_counts(_db_path()),
        status=_engine().status(),
        count=len(logs),
        logs=logs,
    )


@recovery_api.post("/services/<int:service_id>/restart")
def service_restart(service_id):
    """Manual restart of an allowlisted service, identified by ID only."""
    # A custom header cannot be sent cross-origin without a CORS preflight,
    # which blocks other web pages from triggering restarts.
    if request.headers.get("X-ServerGuard") != "1":
        return jsonify(error="forbidden", message="Missing X-ServerGuard header."), 403

    outcome = _engine().manual_restart(service_id)
    kind = outcome["outcome"]
    if kind == "not_found":
        return jsonify(error="not_found", message="No such service."), 404
    if kind == "not_allowed":
        return (
            jsonify(error="forbidden", message="This service is not on the recovery allowlist."),
            403,
        )
    if kind == "unavailable":
        return (
            jsonify(error="unavailable",
                    message="systemd is not available, so services cannot be restarted."),
            503,
        )
    if kind == "busy":
        return jsonify(error="conflict", message="Another restart is already running."), 409

    entry = outcome["entry"]
    if entry["result"] == "SUCCESS":
        return jsonify(entry)
    return jsonify(error="restart_failed", message=entry["message"], entry=entry), 502


@recovery_api.put("/services/<int:service_id>/recovery")
def service_recovery_toggle(service_id):
    """Turn automatic recovery on or off for one service."""
    payload = request.get_json(silent=True)
    enabled = payload.get("enabled") if isinstance(payload, dict) else None
    if not isinstance(enabled, bool):
        return jsonify(error="bad_request", message='Send {"enabled": true} or {"enabled": false}.'), 400

    service = db.get_service(_db_path(), service_id)
    if service is None:
        return jsonify(error="not_found", message="No such service."), 404
    if enabled and not _engine().is_allowlisted(service["service_name"]):
        return (
            jsonify(error="forbidden",
                    message="Only services on the recovery allowlist can use automatic recovery."),
            403,
        )
    db.set_auto_recovery(_db_path(), service_id, enabled)
    return jsonify(id=service_id, auto_recovery=enabled)
