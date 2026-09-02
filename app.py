import json
import os
import time

from flask import Flask
from flask_sock import Sock

from core.config import get_config, ensure_dirs
from core.logging_setup import setup_logging
from core.bootstrap import AppContainer

from infrastructure.extensions import db
from infrastructure.db.seed import seed_defaults

from services import live as live_service
from web.routes import bp as main_blueprint


def make_sqlalchemy_uri(cfg: dict) -> str:
    uri = cfg.get("database_uri", "sqlite:///data/app.db")

    if uri.startswith("sqlite:///"):
        path = uri[len("sqlite:///"):]
        if path == ":memory:":
            return uri

        path = os.path.abspath(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        return "sqlite:///" + path.replace(os.sep, "/")

    return uri


def create_app() -> Flask:
    cfg = get_config()
    ensure_dirs(cfg)
    setup_logging(cfg)

    app = Flask(__name__)

    app.config["SECRET_KEY"] = cfg.get("secret_key", "change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = make_sqlalchemy_uri(cfg)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"timeout": 30}
    }

    app.config["APP_NAME"] = cfg.get("app_name", "Face Attendance Extensible")

    db.init_app(app)

    with app.app_context():
        from domain import models
        db.create_all()
        seed_defaults()

    container = AppContainer(cfg)
    container.camera_manager.init_app(app)

    app.extensions["container"] = container

    app.register_blueprint(main_blueprint)

    sock = Sock(app)

    @sock.route("/ws/dashboard")
    def ws_dashboard(ws):
        interval = int(cfg.get("websocket_interval_sec", 2))

        while True:
            try:
                payload = live_service.get_dashboard_payload(
                    app,
                    container.camera_manager
                )
                ws.send(json.dumps(payload, ensure_ascii=False))
                time.sleep(interval)
            except Exception:
                break

    if cfg.get("auto_start", False):
        container.camera_manager.start_all()

    return app


app = create_app()


if __name__ == "__main__":
    cfg = get_config()
    app.run(
        host=cfg.get("host", "0.0.0.0"),
        port=int(cfg.get("port", 5000)),
        debug=cfg.get("debug", False),
        use_reloader=False,
        threaded=True
    )
