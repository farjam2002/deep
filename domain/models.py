import json
from datetime import datetime

from infrastructure.extensions import db


class Zone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    cameras = db.relationship("Camera", backref="zone", lazy="dynamic")

    def __repr__(self):
        return self.name


class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    rtsp_url = db.Column(db.String(500), nullable=False)

    zone_id = db.Column(db.Integer, db.ForeignKey("zone.id"), nullable=True)

    enabled = db.Column(db.Boolean, default=True, index=True)
    status = db.Column(db.String(20), default="inactive", index=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    active_mask = db.Column(db.Text, default="[]")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def mask_points(self):
        try:
            data = json.loads(self.active_mask or "[]")
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    @property
    def status_fa(self):
        mapping = {
            "online": "آنلاین",
            "error": "خطا",
            "connecting": "در حال اتصال",
            "inactive": "غیرفعال",
            "disabled": "غیرفعال",
        }
        return mapping.get(self.status, self.status)

    def __repr__(self):
        return self.name


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    personnel_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    faces = db.relationship(
        "EmployeeFace",
        backref="employee",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    @property
    def face_count(self):
        return self.faces.count()

    def __repr__(self):
        return f"{self.personnel_code} - {self.full_name}"


class EmployeeFace(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    image_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UnknownPerson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)

    active = db.Column(db.Boolean, default=True, index=True)

    assigned_employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employee.id"),
        nullable=True,
        index=True
    )

    merged_into_id = db.Column(
        db.Integer,
        db.ForeignKey("unknown_person.id"),
        nullable=True,
        index=True
    )

    sample_image_path = db.Column(db.String(500), nullable=True)

    first_seen_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    notes = db.Column(db.Text, nullable=True)

    assigned_employee = db.relationship(
        "Employee",
        foreign_keys=[assigned_employee_id]
    )

    merged_into = db.relationship(
        "UnknownPerson",
        remote_side=[id],
        foreign_keys=[merged_into_id]
    )

    def __repr__(self):
        return self.code


class AttendanceSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    person_type = db.Column(db.String(20), default="temporary", index=True)

    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True, index=True)
    unknown_id = db.Column(db.Integer, db.ForeignKey("unknown_person.id"), nullable=True, index=True)

    zone_id = db.Column(db.Integer, db.ForeignKey("zone.id"), nullable=True, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=True, index=True)

    track_key = db.Column(db.String(50), nullable=True, index=True)

    start_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    last_seen_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    end_time = db.Column(db.DateTime, nullable=True)

    duration_seconds = db.Column(db.Integer, default=0)

    status = db.Column(db.String(20), default="active", index=True)

    avg_confidence = db.Column(db.Float, nullable=True)
    confidence_samples = db.Column(db.Integer, default=0)

    review_status = db.Column(db.String(20), default="auto", index=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    last_event_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", foreign_keys=[employee_id])
    unknown = db.relationship("UnknownPerson", foreign_keys=[unknown_id])
    zone = db.relationship("Zone", foreign_keys=[zone_id])
    camera = db.relationship("Camera", foreign_keys=[camera_id])

    __table_args__ = (
        db.Index("ix_session_status_last_seen", "status", "last_seen_time"),
        db.Index("ix_session_person_employee", "person_type", "employee_id"),
        db.Index("ix_session_person_unknown", "person_type", "unknown_id"),
        db.Index("ix_session_zone_start", "zone_id", "start_time"),
    )

    @property
    def identity_code(self):
        if self.person_type == "employee" and self.employee:
            return self.employee.personnel_code
        if self.person_type == "unknown" and self.unknown:
            return self.unknown.code
        if self.track_key:
            return self.track_key
        return ""

    @property
    def identity_name(self):
        if self.person_type == "employee" and self.employee:
            return self.employee.full_name
        if self.person_type == "unknown":
            return "ناشناس"
        return "موقت"

    def __repr__(self):
        return f"Session {self.id} - {self.person_type}"


class PresenceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    session_id = db.Column(db.Integer, db.ForeignKey("attendance_session.id"), nullable=True, index=True)

    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True, index=True)
    unknown_id = db.Column(db.Integer, db.ForeignKey("unknown_person.id"), nullable=True, index=True)

    zone_id = db.Column(db.Integer, db.ForeignKey("zone.id"), nullable=True, index=True)
    camera_id = db.Column(db.Integer, db.ForeignKey("camera.id"), nullable=True, index=True)

    person_type = db.Column(db.String(20), default="temporary")
    track_key = db.Column(db.String(50), nullable=True)

    seen_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    confidence = db.Column(db.Float, nullable=True)


class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(20), default="info")
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    acknowledged = db.Column(db.Boolean, default=False)