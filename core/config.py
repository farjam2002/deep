import copy
import json
import os

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")

DEFAULT_CONFIG = {
    "app_name": "سامانه حضور و غیاب تشخیص چهره",
    "secret_key": "CHANGE-SECRET-KEY",
    "host": "0.0.0.0",
    "port": 5000,
    "debug": False,
    "auto_start": False,
    "websocket_interval_sec": 2,

    "database_uri": "sqlite:///data/app.db",

    "processing": {
        "frame_skip": 5,
        "process_width": 640,

        "motion_enabled": True,
        "motion_threshold": 25,
        "motion_min_area": 700,

        "face_min_size": 60,
        "blur_threshold": 80,
        "min_quality": 25,
        "min_recognition_quality": 40,

        "recognition_backend": "auto",
        "onnx_model_path": "models/mobilefacenet.onnx",
        "onnx_threshold": 0.40,
        "onnx_input_mean": 127.5,
        "onnx_input_std": 128.0,

        "recognition_threshold": 70,
        "review_confidence_threshold": 55,

        "session_timeout_sec": 180,
        "event_interval_sec": 30,

        "snapshot_interval_sec": 5,
        "reid_interval_sec": 15,
        "train_interval_sec": 10,

        "unknown_min_age_sec": 3,
        "unknown_quality_min": 65,

        "max_misses": 12
    },

    "camera": {
        "reconnect_delay_sec": 5
    },

    "storage": {
        "data_dir": "data",
        "employees_dir": "data/employees",
        "unknown_dir": "data/unknown",
        "snapshots_dir": "data/snapshots",
        "backups_dir": "backups",
        "logs_dir": "logs"
    }
}

_config = None


def _deep_update(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def get_config() -> dict:
    global _config

    if _config is None:
        cfg = copy.deepcopy(DEFAULT_CONFIG)

        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                _deep_update(cfg, user_cfg)
            except Exception:
                pass
        else:
            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        _config = cfg

    return _config


def ensure_dirs(cfg: dict) -> None:
    storage = cfg.get("storage", {})

    dirs = [
        storage.get("data_dir", "data"),
        storage.get("employees_dir", "data/employees"),
        storage.get("unknown_dir", "data/unknown"),
        storage.get("snapshots_dir", "data/snapshots"),
        storage.get("backups_dir", "backups"),
        storage.get("logs_dir", "logs"),
        "models"
    ]

    for d in dirs:
        if d:
            os.makedirs(d, exist_ok=True)