import logging
import queue
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from infrastructure.extensions import db
from domain.models import Camera

logger = logging.getLogger(__name__)


class CameraWorker(threading.Thread):
    def __init__(self, manager: "CameraManager", camera_id: int):
        super().__init__(daemon=True)
        self.manager = manager
        self.camera_id = camera_id
        self.stop_event = threading.Event()
        self.tracker = self.manager.tracker_factory()
        self.prev_gray = None
        self.last_snapshot = 0.0
        self.last_close = 0.0
        self.last_process = 0.0
        self.last_error_alert = None
        self.read_count = 0

    def _get_camera_info(self):
        if not self.manager.app:
            return None
        with self.manager.app.app_context():
            cam = db.session.get(Camera, self.camera_id)
            if not cam:
                return None
            return {"id": cam.id, "rtsp_url": cam.rtsp_url, "enabled": cam.enabled}

    def _set_status(self, status: str, alert_message: str = None):
        if not self.manager.app:
            return
        try:
            with self.manager.app.app_context():
                cam = db.session.get(Camera, self.camera_id)
                if cam:
                    cam.status = status
                    if status in ("online", "connecting"):
                        cam.last_seen_at = datetime.utcnow()
                    db.session.commit()
                if status == "error":
                    self.manager.update_stat(self.camera_id, error=alert_message or "خطا")
                    if alert_message:
                        now = datetime.utcnow()
                        should_alert = True
                        if self.last_error_alert:
                            if (now - self.last_error_alert).total_seconds() < 60:
                                should_alert = False
                        if should_alert:
                            self.manager.notifier.alert("error", alert_message)
                            db.session.commit()
                            self.last_error_alert = now
                else:
                    self.manager.update_stat(self.camera_id, error="")
        except Exception:
            logger.exception("خطا در به‌روزرسانی وضعیت دوربین %s", self.camera_id)

    def _build_mask(self, points, h, w):
        try:
            if not points or len(points) < 3:
                return None
            pts = np.array(points, dtype=np.float32)
            if pts.ndim != 2 or pts.shape[1] != 2:
                return None
            if pts.max() <= 1.0:
                pts[:, 0] *= w
                pts[:, 1] *= h
            pts = pts.astype(np.int32)
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(mask, [pts], 255)
            return mask
        except Exception:
            return None

    def _motion(self, gray, mask, proc: dict) -> bool:
        try:
            current = cv2.GaussianBlur(gray, (5, 5), 0)
            if self.prev_gray is None:
                self.prev_gray = current
                return True
            diff = cv2.absdiff(self.prev_gray, current)
            self.prev_gray = current
            threshold_value = int(proc.get("motion_threshold", 25))
            _, thresh = cv2.threshold(diff, threshold_value, 255, cv2.THRESH_BINARY)
            if mask is not None:
                thresh = cv2.bitwise_and(thresh, mask)
            area = cv2.countNonZero(thresh)
            min_area = int(proc.get("motion_min_area", 700))
            return area >= min_area
        except Exception:
            return True

    def _process_loop(self, q: "queue.Queue"):
        while not self.stop_event.is_set():
            try:
                item = q.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                self._on_frame(item)
            except Exception:
                logger.exception("خطا در پردازش فریم دوربین %s", self.camera_id)

    def _on_frame(self, frame):
        cfg = self.manager.cfg
        proc = cfg.get("processing", {})
        if frame is None:
            return

        h, w = frame.shape[:2]
        process_width = int(proc.get("process_width", 640))
        if w > process_width:
            scale = process_width / float(w)
            new_h = int(h * scale)
            frame_small = cv2.resize(frame, (process_width, new_h))
        else:
            frame_small = frame

        now = time.time()
        now_dt = datetime.utcnow()

        snapshot_interval = float(proc.get("snapshot_interval_sec", 0.3))
        if now - self.last_snapshot >= snapshot_interval:
            try:
                ok, buf = cv2.imencode(".jpg", frame_small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    self.manager.set_snapshot(self.camera_id, buf.tobytes())
                    self.last_snapshot = now
            except Exception:
                pass

        process_interval = float(proc.get("process_interval_sec", 2))
        if now - self.last_process < process_interval:
            return
        self.last_process = now

        try:
            if not self.manager.app:
                return
            with self.manager.app.app_context():
                cam = db.session.get(Camera, self.camera_id)
                if not cam or not cam.enabled:
                    self.stop_event.set()
                    return
                zone = cam.zone
                h2, w2 = frame_small.shape[:2]
                gray = cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY)
                mask = self._build_mask(cam.mask_points, h2, w2)
                has_motion = self._motion(gray, mask, proc)
                frame_shape = (h2, w2)

                if proc.get("motion_enabled", True) and not has_motion:
                    if self.tracker.has_active():
                        self.tracker.update([], now_dt, frame_shape)
                    return
                if zone is None:
                    return

                if self.manager.face_engine.should_train():
                    try:
                        self.manager.face_engine.train()
                    except Exception:
                        logger.exception("خطا در آموزش مدل چهره")

                detections = self.manager.face_engine.detect_faces(frame_small, gray, mask)
                self.manager.update_stat(self.camera_id, key="processed_frames", inc=1,
                                         success=now_dt.strftime("%Y-%m-%d %H:%M:%S"))
                self.manager.update_stat(self.camera_id, key="faces", inc=len(detections))

                self.tracker.update(detections, now_dt, frame_shape)
                reid_interval = int(proc.get("reid_interval_sec", 15))
                unknown_quality_min = int(proc.get("unknown_quality_min", 65))
                unknown_min_age_sec = float(proc.get("unknown_min_age_sec", 3))

                for track in self.tracker.tracks:
                    if track.det_idx is None:
                        continue
                    det = detections[track.det_idx]
                    if track.need_recognition(now_dt, reid_interval):
                        result = self.manager.face_engine.recognize_face(det)
                        track.last_recognition_time = now_dt
                        if result and result.get("person_type") in ("employee", "unknown"):
                            track.identity_type = result["person_type"]
                            if result["person_type"] == "employee":
                                track.employee_id = result["person_id"]
                                track.unknown_id = None
                            else:
                                track.unknown_id = result["person_id"]
                                track.employee_id = None
                                track.unknown_created = True
                            track.confidence = result.get("confidence")
                            self.manager.update_stat(self.camera_id, key="recognized", inc=1)
                        else:
                            should_create_unknown = (
                                track.identity_type is None and
                                not track.unknown_created and
                                track.is_stable(now_dt, min_age_sec=unknown_min_age_sec, min_hits=2) and
                                det.quality >= unknown_quality_min
                            )
                            if should_create_unknown:
                                unknown = self.manager.attendance_service.create_unknown_from_detection(
                                    cam, zone, det, track.key)
                                if unknown:
                                    track.identity_type = "unknown"
                                    track.unknown_id = unknown.id
                                    track.employee_id = None
                                    track.unknown_created = True
                                    track.confidence = result.get("confidence") if result else None
                                    self.manager.update_stat(self.camera_id, key="unknown_created", inc=1)

                    if track.identity_type in ("employee", "unknown"):
                        self.manager.attendance_service.record_detection(
                            camera=cam, zone=zone, person_type=track.identity_type,
                            track_key=track.key, confidence=track.confidence,
                            employee_id=track.employee_id, unknown_id=track.unknown_id)
                    else:
                        self.manager.attendance_service.record_detection(
                            camera=cam, zone=zone, person_type="temporary",
                            track_key=track.key, confidence=None)

                if now - self.last_close >= 5:
                    timeout_sec = int(proc.get("session_timeout_sec", 180))
                    self.manager.attendance_service.close_expired_sessions(timeout_sec)
                    self.last_close = now
        except Exception:
            logger.exception("خطا در پردازش دوربین %s", self.camera_id)

    def run(self):
        cfg = self.manager.cfg
        reconnect_delay = int(cfg.get("camera", {}).get("reconnect_delay_sec", 5))
        while not self.stop_event.is_set():
            info = self._get_camera_info()
            if not info or not info["enabled"]:
                break
            self._set_status("connecting")
            source = self.manager.source_factory(info)
            try:
                opened = source.open()
            except Exception:
                logger.exception("خطا در ایجاد اتصال دوربین %s", self.camera_id)
                opened = False
            if not opened:
                source.release()
                self._set_status("error", f"اتصال دوربین {self.camera_id} برقرار نشد.")
                if self.stop_event.wait(reconnect_delay):
                    break
                continue
            self._set_status("online")
            q = queue.Queue(maxsize=2)
            processor = threading.Thread(target=self._process_loop, args=(q,), daemon=True)
            processor.start()
            while not self.stop_event.is_set():
                ret, frame = source.read()
                if not ret or frame is None:
                    break
                self.read_count += 1
                if self.read_count % 20 == 0:
                    self.manager.update_stat(self.camera_id, key="frames_read", inc=20)
                if q.full():
                    try:
                        q.get_nowait()
                    except Exception:
                        pass
                try:
                    q.put_nowait(frame)
                except Exception:
                    pass
            source.release()
            try:
                while True:
                    q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(None)
            except Exception:
                pass
            processor.join(timeout=1)
            if self.stop_event.is_set():
                break
            self._set_status("error", f"اتصال دوربین {self.camera_id} قطع شد.")
            if self.stop_event.wait(reconnect_delay):
                break
        self._set_status("inactive")


class CameraManager:
    def __init__(self, cfg: dict, face_engine, tracker_factory, attendance_service, source_factory, notifier):
        self.cfg = cfg
        self.app = None
        self.face_engine = face_engine
        self.tracker_factory = tracker_factory
        self.attendance_service = attendance_service
        self.source_factory = source_factory
        self.notifier = notifier
        self.workers = {}
        self.snapshots = {}
        self.snapshot_versions = {}
        self.stats = {}
        self.lock = threading.Lock()
        self.media_lock = threading.Lock()
        self.stats_lock = threading.Lock()
        self.running = False

    def init_app(self, app):
        self.app = app

    def _default_stats(self):
        return {"frames_read": 0, "processed_frames": 0, "faces": 0,
                "recognized": 0, "unknown_created": 0, "last_success": None, "last_error": ""}

    def update_stat(self, camera_id, key=None, inc=0, value=None, error=None, success=None):
        with self.stats_lock:
            st = self.stats.setdefault(camera_id, self._default_stats())
            if key and inc:
                st[key] = st.get(key, 0) + inc
            if key and value is not None:
                st[key] = value
            if error is not None:
                st["last_error"] = error
            if success is not None:
                st["last_success"] = success

    def get_stats(self, camera_id):
        with self.stats_lock:
            return dict(self.stats.get(camera_id, self._default_stats()))

    def set_snapshot(self, camera_id, data: bytes):
        with self.media_lock:
            self.snapshots[camera_id] = data
            self.snapshot_versions[camera_id] = self.snapshot_versions.get(camera_id, 0) + 1

    def get_snapshot(self, camera_id):
        with self.media_lock:
            return self.snapshots.get(camera_id)

    def get_snapshot_version(self, camera_id):
        with self.media_lock:
            return self.snapshot_versions.get(camera_id, 0)

    def start_camera(self, camera_id: int) -> bool:
        if not self.app:
            return False
        with self.lock:
            existing = self.workers.get(camera_id)
            if existing and existing.is_alive():
                return True
            with self.app.app_context():
                cam = db.session.get(Camera, camera_id)
                if not cam or not cam.enabled:
                    return False
            worker = CameraWorker(self, camera_id)
            self.workers[camera_id] = worker
            worker.start()
            self.running = True
            return True

    def stop_camera(self, camera_id: int, set_inactive: bool = True):
        with self.lock:
            worker = self.workers.pop(camera_id, None)
        if worker:
            worker.stop_event.set()
            worker.join(timeout=2)
        if self.app and set_inactive:
            try:
                with self.app.app_context():
                    cam = db.session.get(Camera, camera_id)
                    if cam:
                        cam.status = "disabled" if not cam.enabled else "inactive"
                        db.session.commit()
            except Exception:
                logger.exception("خطا در تنظیم وضعیت دوربین %s", camera_id)
        with self.lock:
            if len(self.workers) == 0:
                self.running = False

    def restart_camera(self, camera_id: int) -> bool:
        self.stop_camera(camera_id, set_inactive=False)
        return self.start_camera(camera_id)

    def start_all(self):
        if not self.app:
            return
        with self.app.app_context():
            cameras = Camera.query.filter_by(enabled=True).all()
            camera_ids = [cam.id for cam in cameras]
        self.running = True
        for camera_id in camera_ids:
            self.start_camera(camera_id)

    def stop_all(self):
        self.running = False
        with self.lock:
            camera_ids = list(self.workers.keys())
        for camera_id in camera_ids:
            self.stop_camera(camera_id, set_inactive=True)