"""ServerGuard application package."""

import atexit

from flask import Flask, render_template

from . import settings as app_settings
from .alerts.alert_engine import AlertEngine
from .config import Config
from .database import db
from .database.writer import MetricsWriter
from .monitoring.collector import MetricsCollector
from .monitoring.processes import ProcessSampler
from .monitoring.services import ServiceMonitor, validate_service_name
from .recovery.recovery_engine import RecoveryEngine
from .routes import api, register_error_handlers
from .routes_alerts import alerts_api
from .routes_recovery import recovery_api


def create_app(config=None, start_collector=True):
    """Build the Flask app.

    start_collector=False is used by tests so no background thread is created.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    db_path = app.config["DATABASE_PATH"]
    db.init_db(db_path)

    if db.count_services(db_path) == 0:
        for name in app.config["DEFAULT_SERVICES"]:
            if validate_service_name(name):
                db.add_service(db_path, name)

    # An interval saved on the Settings page overrides the environment default.
    saved = app_settings.load(db_path, app_settings.defaults_for(app.config))

    # ONE collector for the whole process. Routes only read from it.
    collector = MetricsCollector(interval=saved["monitor_interval"])
    service_monitor = ServiceMonitor(
        db_path, check_interval=app.config["SERVICE_CHECK_INTERVAL"]
    )
    process_sampler = ProcessSampler()
    recovery_engine = RecoveryEngine(
        db_path,
        allowlist_file=app.config["RECOVERY_ALLOWLIST"],
        script_path=app.config["RECOVERY_SCRIPT"],
        sudo_bin=app.config["SUDO_BIN"],
    )

    collector.add_listener(
        MetricsWriter(
            db_path,
            save_interval=app.config["DB_SAVE_INTERVAL"],
            retention_days=app.config["RETENTION_DAYS"],
        )
    )
    collector.add_listener(service_monitor)
    collector.add_listener(process_sampler)
    collector.add_listener(AlertEngine(db_path))   # alert first ...
    collector.add_listener(recovery_engine)        # ... then recovery

    app.extensions["collector"] = collector
    app.extensions["service_monitor"] = service_monitor
    app.extensions["process_sampler"] = process_sampler
    app.extensions["recovery_engine"] = recovery_engine

    app.register_blueprint(api)
    app.register_blueprint(alerts_api)
    app.register_blueprint(recovery_api)
    register_error_handlers(app)

    @app.get("/")
    def index():
        return render_template("index.html", active="overview")

    @app.get("/services")
    def services_page():
        return render_template("services.html", active="services")

    @app.get("/processes")
    def processes_page():
        return render_template("processes.html", active="processes")

    @app.get("/alerts")
    def alerts_page():
        return render_template("alerts.html", active="alerts")

    @app.get("/recovery")
    def recovery_page():
        return render_template("recovery.html", active="recovery")

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html", active="settings")

    if start_collector:
        collector.start()
        atexit.register(collector.stop)  # clean shutdown when the process exits

    return app
