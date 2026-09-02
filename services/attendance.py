import uuid
from datetime import datetime, timedelta

from infrastructure.extensions import db
from domain.models import (
    AttendanceSession,
    PresenceEvent,
    UnknownPerson
)
from core.utils import unique_filename


class AttendanceService:
    def __init__(self, cfg: dict, storage, notifier, gallery_invalidator=None):
        self.cfg = cfg
        self.storage = storage
        self.notifier = notifier
        self.gallery_invalidator = gallery_invalidator

    def _update_confidence(self, session: AttendanceSession, confidence):
        if confidence is None:
            return

        try:
            confidence = float(confidence)
        except Exception:
            return

        if session.avg_confidence is None or session.confidence_samples == 0:
            session.avg_confidence = confidence
            session.confidence_samples = 1
        else:
            session.avg_confidence = (
                (session.avg_confidence * session.confidence_samples) + confidence
            ) / (session.confidence_samples + 1)
            session.confidence_samples += 1

    def _maybe_mark_review(self, session: AttendanceSession):
        proc = self.cfg.get("processing", {})
        review_threshold = float(proc.get("review_confidence_threshold", 55))

        if session.person_type == "temporary":
            return

        if session.review_status not in ("auto", "pending"):
            return

        if session.avg_confidence is not None and session.avg_confidence > review_threshold:
            session.review_status = "pending"

    def _active_temp_session(self, track_key: str, zone_id: int):
        if not track_key:
            return None

        return AttendanceSession.query.filter_by(
            status="active",
            person_type="temporary",
            track_key=track_key,
            zone_id=zone_id
        ).order_by(AttendanceSession.last_seen_time.desc()).first()

    def _active_identity_session(self, person_type: str, employee_id, unknown_id, zone_id: int):
        query = AttendanceSession.query.filter_by(
            status="active",
            person_type=person_type,
            zone_id=zone_id
        )

        if person_type == "employee":
            query = query.filter_by(employee_id=employee_id)
        elif person_type == "unknown":
            query = query.filter_by(unknown_id=unknown_id)
        else:
            return None

        return query.order_by(AttendanceSession.last_seen_time.desc()).first()

    def _add_event_if_needed(self, session: AttendanceSession, camera, zone, track_key, confidence, seen_at):
        proc = self.cfg.get("processing", {})
        event_interval = int(proc.get("event_interval_sec", 30))

        should_add = False

        if session.last_event_at is None:
            should_add = True
        else:
            elapsed = (seen_at - session.last_event_at).total_seconds()
            if elapsed >= event_interval:
                should_add = True

        if not should_add:
            return

        event = PresenceEvent(
            session_id=session.id,
            employee_id=session.employee_id,
            unknown_id=session.unknown_id,
            zone_id=zone.id if zone else None,
            camera_id=camera.id if camera else None,
            person_type=session.person_type,
            track_key=track_key,
            seen_at=seen_at,
            confidence=confidence
        )

        db.session.add(event)
        session.last_event_at = seen_at

    def record_detection(
        self,
        camera,
        zone,
        person_type: str,
        track_key: str,
        confidence=None,
        employee_id=None,
        unknown_id=None
    ):
        if zone is None:
            return None

        seen_at = datetime.utcnow()

        if person_type == "temporary":
            session = self._active_temp_session(track_key, zone.id)

            if not session:
                session = AttendanceSession(
                    person_type="temporary",
                    employee_id=None,
                    unknown_id=None,
                    zone_id=zone.id,
                    camera_id=camera.id if camera else None,
                    track_key=track_key,
                    start_time=seen_at,
                    last_seen_time=seen_at,
                    status="active",
                    review_status="auto"
                )
                db.session.add(session)

            session.last_seen_time = seen_at
            session.camera_id = camera.id if camera else None
            session.duration_seconds = int((seen_at - session.start_time).total_seconds())

            self._add_event_if_needed(session, camera, zone, track_key, confidence, seen_at)
            db.session.commit()

            return session

        temp_session = self._active_temp_session(track_key, zone.id)
        identity_session = self._active_identity_session(person_type, employee_id, unknown_id, zone.id)

        if identity_session:
            session = identity_session

            if temp_session:
                if temp_session.start_time < session.start_time:
                    session.start_time = temp_session.start_time

                temp_session.status = "closed"
                temp_session.end_time = seen_at
                temp_session.person_type = person_type
                temp_session.employee_id = employee_id
                temp_session.unknown_id = unknown_id
                temp_session.duration_seconds = int(
                    (temp_session.end_time - temp_session.start_time).total_seconds()
                )
                temp_session.review_status = "merged"

        else:
            if temp_session:
                session = temp_session
                session.person_type = person_type
                session.employee_id = employee_id
                session.unknown_id = unknown_id
            else:
                session = AttendanceSession(
                    person_type=person_type,
                    employee_id=employee_id,
                    unknown_id=unknown_id,
                    zone_id=zone.id,
                    camera_id=camera.id if camera else None,
                    track_key=track_key,
                    start_time=seen_at,
                    last_seen_time=seen_at,
                    status="active",
                    review_status="auto"
                )
                db.session.add(session)

        session.last_seen_time = seen_at
        session.camera_id = camera.id if camera else None
        session.duration_seconds = int((seen_at - session.start_time).total_seconds())

        self._update_confidence(session, confidence)
        self._maybe_mark_review(session)

        self._add_event_if_needed(session, camera, zone, track_key, confidence, seen_at)

        db.session.commit()
        return session

    def close_expired_sessions(self, timeout_sec: int):
        cutoff = datetime.utcnow() - timedelta(seconds=int(timeout_sec))

        sessions = AttendanceSession.query.filter_by(status="active").filter(
            AttendanceSession.last_seen_time < cutoff
        ).all()

        if not sessions:
            return

        for session in sessions:
            session.status = "closed"
            session.end_time = session.last_seen_time
            session.duration_seconds = int(
                (session.end_time - session.start_time).total_seconds()
            )

            if session.person_type == "temporary" and not session.employee_id and not session.unknown_id:
                if session.review_status == "auto":
                    session.review_status = "pending"

        db.session.commit()

    def create_unknown_from_detection(self, camera, zone, detection, track_key: str):
        if detection is None or detection.face_bgr is None:
            return None

        filename = unique_filename("unknown.jpg")
        path = self.storage.save_cv_image("unknown", filename, detection.face_bgr)

        if not path:
            return None

        now = datetime.utcnow()

        unknown = UnknownPerson(
            code=f"TEMP-{uuid.uuid4().hex}",
            active=True,
            sample_image_path=path,
            first_seen_at=now,
            last_seen_at=now
        )

        db.session.add(unknown)
        db.session.flush()

        unknown.code = f"UNKNOWN-{unknown.id:06d}"

        self.notifier.unknown_created(unknown.code)

        db.session.commit()

        if self.gallery_invalidator:
            try:
                self.gallery_invalidator()
            except Exception:
                pass

        return unknown