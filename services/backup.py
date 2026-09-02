import os
import zipfile
from datetime import datetime

from core.config import get_config
from core.utils import ensure_dir


def _add_dir_to_zip(zf: zipfile.ZipFile, directory: str, arc_base: str):
    if not directory or not os.path.isdir(directory):
        return

    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, directory)
            arcname = os.path.join(arc_base, rel_path)

            try:
                zf.write(file_path, arcname)
            except Exception:
                pass


def create_backup() -> str:
    cfg = get_config()

    backups_dir = cfg.get("storage", {}).get("backups_dir", "backups")
    ensure_dir(backups_dir)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(backups_dir, f"backup_{stamp}.zip")

    db_path = None

    try:
        from flask import current_app
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    except Exception:
        uri = cfg.get("database_uri", "")

    if uri.startswith("sqlite:///"):
        db_path = uri[len("sqlite:///"):]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_path and os.path.exists(db_path):
            zf.write(db_path, os.path.basename(db_path))

        if os.path.exists("config.json"):
            zf.write("config.json", "config.json")

        storage = cfg.get("storage", {})

        _add_dir_to_zip(zf, storage.get("employees_dir"), "employees")
        _add_dir_to_zip(zf, storage.get("unknown_dir"), "unknown")
        _add_dir_to_zip(zf, storage.get("logs_dir"), "logs")

    return zip_path