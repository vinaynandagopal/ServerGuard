"""ServerGuard application package."""

import atexit

from flask import Flask, render_template

from .config import Config
from .database import db
from .database.writer import MetricsWriter
from .monitoring.collector import MetricsCollector
from .monitoring.processes import ProcessSampler
from .monitoring.services import ServiceMonitor, validate_service_name
from .routes import api, register_error_handlers


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

    # ONE collector for the whole process. Routes only read from it.
    collector = MetricsCollector(interval=app.config["MONITOR_INTERVAL"])
    service_monitor = ServiceMonitor(
        db_path, check_interval=app.config["SERVICE_CHECK_INTERVAL"]
    )
    process_sampler = ProcessSampler()

    collector.add_listener(
        MetricsWriter(
            db_path,
            save_interval=app.config["DB_SAVE_INTERVAL"],
            retention_days=app.config["RETENTION_DAYS"],
        )
    )
    collector.add_listener(service_monitor)
    collector.add_listener(process_sampler)

    app.extensions["collector"] = collector
    app.extensions["service_monitor"] = service_monitor
    app.extensions["process_sampler"] = process_sampler

    app.register_blueprint(api)
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

    if start_collector:
        collector.start()
        atexit.register(collector.stop)  # clean shutdown when the process exits

    return app
