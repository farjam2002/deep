import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(cfg: dict) -> None:
    logs_dir = cfg.get("storage", {}).get("logs_dir", "logs")
    os.makedirs(logs_dir, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        file_handler = RotatingFileHandler(
            os.path.join(logs_dir, "app.log"),
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


def read_log_lines(cfg: dict, lines: int = 300) -> list[str]:
    logs_dir = cfg.get("storage", {}).get("logs_dir", "logs")
    path = os.path.join(logs_dir, "app.log")

    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-lines:]
    except Exception:
        return []