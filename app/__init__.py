"""ServerGuard application package."""

import atexit

from flask import Flask, jsonify

from .config import Config
from .database import db
from .database.writer import MetricsWriter
from .monitoring.collector import MetricsCollector
from .routes import api, register_error_handlers


def create_app(config=None, start_collector=True):
    """Build the Flask app.

    start_collector=False is used by tests so no background thread is created.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    db.init_db(app.config["DATABASE_PATH"])

    # ONE collector for the whole process. Routes only read from it.
    collector = MetricsCollector(interval=app.config["MONITOR_INTERVAL"])
    collector.add_listener(
        MetricsWriter(
            app.config["DATABASE_PATH"],
            save_interval=app.config["DB_SAVE_INTERVAL"],
            retention_days=app.config["RETENTION_DAYS"],
        )
    )
    app.extensions["collector"] = collector

    app.register_blueprint(api)
    register_error_handlers(app)

    @app.get("/")
    def index():
        # Temporary landing response; Phase 5 replaces this with the dashboard.
        return jsonify(
            name="ServerGuard",
            message="API is running. Dashboard arrives in Phase 5.",
            endpoints=[
                "/api/health",
                "/api/metrics/current",
                "/api/metrics/detail",
                "/api/metrics/history?range=15m",
            ],
        )

    if start_collector:
        collector.start()
        atexit.register(collector.stop)  # clean shutdown when the process exits

    return app
