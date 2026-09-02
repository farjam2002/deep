from infrastructure.storage.local_storage import LocalStorage
from infrastructure.vision.face_engine import FaceEngineManager
from infrastructure.vision.tracker import SimpleTracker
from infrastructure.camera.sources import OpenCVVideoSource
from infrastructure.camera.manager import CameraManager
from infrastructure.notifications.logging_notifier import LoggingNotifier

from services.attendance import AttendanceService
from services.reports import ReportService


class AppContainer:
    def __init__(self, cfg: dict):
        self.cfg = cfg

        self.storage = LocalStorage(cfg)
        self.face_engine = FaceEngineManager(cfg)
        self.notifier = LoggingNotifier()

        self.attendance_service = AttendanceService(
            cfg=cfg,
            storage=self.storage,
            notifier=self.notifier,
            gallery_invalidator=self.face_engine.mark_dirty
        )

        self.report_service = ReportService()

        self.tracker_factory = lambda: SimpleTracker(
            max_misses=int(cfg.get("processing", {}).get("max_misses", 12))
        )

        self.source_factory = lambda camera_info: OpenCVVideoSource(
            camera_info["rtsp_url"]
        )

        self.camera_manager = CameraManager(
            cfg=cfg,
            face_engine=self.face_engine,
            tracker_factory=self.tracker_factory,
            attendance_service=self.attendance_service,
            source_factory=self.source_factory,
            notifier=self.notifier
        )