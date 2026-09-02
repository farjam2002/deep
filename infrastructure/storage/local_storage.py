import os
import shutil

import cv2

from domain.interfaces import IStorage
from core.utils import ensure_dir


class LocalStorage(IStorage):
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.storage_cfg = cfg.get("storage", {})

        ensure_dir(self.storage_cfg.get("employees_dir"))
        ensure_dir(self.storage_cfg.get("unknown_dir"))
        ensure_dir(self.storage_cfg.get("snapshots_dir"))
        ensure_dir(self.storage_cfg.get("backups_dir"))
        ensure_dir(self.storage_cfg.get("logs_dir"))

    def _get_dir(self, category: str) -> str:
        mapping = {
            "employees": self.storage_cfg.get("employees_dir", "data/employees"),
            "unknown": self.storage_cfg.get("unknown_dir", "data/unknown"),
            "snapshots": self.storage_cfg.get("snapshots_dir", "data/snapshots"),
            "backups": self.storage_cfg.get("backups_dir", "backups"),
            "logs": self.storage_cfg.get("logs_dir", "logs"),
        }

        path = mapping.get(category)
        if not path:
            raise ValueError(f"Unknown storage category: {category}")

        ensure_dir(path)
        return path

    def save_uploaded_file(self, category: str, file, filename: str) -> str:
        directory = self._get_dir(category)
        path = os.path.join(directory, filename)
        file.save(path)
        return path

    def save_cv_image(self, category: str, filename: str, image) -> str:
        directory = self._get_dir(category)
        path = os.path.join(directory, filename)

        try:
            ok = cv2.imwrite(path, image)
            if not ok:
                return None
            return path
        except Exception:
            return None

    def copy_file(self, category: str, src_path: str, filename: str) -> str:
        if not src_path or not os.path.exists(src_path):
            return None

        directory = self._get_dir(category)
        path = os.path.join(directory, filename)

        try:
            shutil.copy2(src_path, path)
            return path
        except Exception:
            return None

    def delete(self, path: str):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def exists(self, path: str) -> bool:
        try:
            return bool(path and os.path.exists(path))
        except Exception:
            return False