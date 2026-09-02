import time
from datetime import datetime

from infrastructure.extensions import db
from domain.models import (
    Camera,
    Employee,
    UnknownPerson,
    AttendanceSession,
    PresenceEvent,
    Alert
)
from core.utils import format_datetime

_cache = {
    "ts": 0,
    "data": None
}


def get_dashboard_payload(app, camera_manager):
    now = time.time()

    if _cache["data"] is not None and now - _cache["ts"] < 1:
        return _cache["data"]

    with app.app_context():
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        stats = {
            "active_cameras": Camera.query.filter_by(enabled=True).count(),
            "online_cameras": Camera.query.filter_by(enabled=True, status="online").count(),
            "employees": Employee.query.filter_by(active=True).count(),
            "unknowns": UnknownPerson.query.filter(
                UnknownPerson.active == True,  # noqa: E712
                UnknownPerson.assigned_employee_id.is_(None),
                UnknownPerson.merged_into_id.is_(None)
            ).count(),
            "active_sessions": AttendanceSession.query.filter_by(status="active").count(),
            "today_events": PresenceEvent.query.filter(PresenceEvent.seen_at >= today_start).count(),
            "pending_review": AttendanceSession.query.filter_by(review_status="pending").count(),
        }

        cameras = []

        for cam in Camera.query.order_by(Camera.id).all():
            st = camera_manager.get_stats(cam.id)

            cameras.append({
                "id": cam.id,
                "name": cam.name,
                "status": cam.status,
                "status_fa": cam.status_fa,
                "enabled": cam.enabled,
                "zone": cam.zone.name if cam.zone else None,
                "snapshot_version": camera_manager.get_snapshot_version(cam.id),
                "stats": st
            })

        alerts = []

        for alert in Alert.query.order_by(Alert.created_at.desc()).limit(10).all():
            alerts.append({
                "created_at": format_datetime(alert.created_at),
                "level": alert.level,
                "message": alert.message
            })

        payload = {
            "ts": now,
            "running": camera_manager.running,
            "stats": stats,
            "cameras": cameras,
            "alerts": alerts
        }

    _cache["ts"] = now
    _cache["data"] = payload

    return payload