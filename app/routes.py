"""REST API routes. All responses are JSON."""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from .database import db

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

    return jsonify(
        {
            "timestamp": snap["timestamp"],
            "hostname": _get(snap, "system", "hostname"),
            "cpu_usage": _get(snap, "cpu", "usage_percent"),
            "memory_usage": _get(snap, "memory", "usage_percent"),
            "disk_usage": _get(snap, "disk", "primary", "usage_percent"),
            "network_sent": _get(snap, "network", "bytes_sent"),
            "network_received": _get(snap, "network", "bytes_received"),
            "upload_bytes_per_sec": _get(snap, "network", "upload_bytes_per_sec"),
            "download_bytes_per_sec": _get(snap, "network", "download_bytes_per_sec"),
            "uptime": _get(snap, "system", "uptime_human"),
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
