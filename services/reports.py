from datetime import datetime, timedelta

from sqlalchemy import and_, or_, func

from infrastructure.extensions import db
from domain.models import (
    Zone,
    Employee,
    UnknownPerson,
    AttendanceSession
)


class ReportService:
    def _filter_sessions(self, args, include_date=True):
        query = AttendanceSession.query

        if include_date:
            date_str = args.get("date", "").strip()
            if date_str:
                try:
                    day = datetime.strptime(date_str, "%Y-%m-%d")
                    query = query.filter(
                        AttendanceSession.start_time >= day,
                        AttendanceSession.start_time < day + timedelta(days=1)
                    )
                except Exception:
                    pass

        zone_id = args.get("zone_id", type=int)
        if zone_id:
            query = query.filter(AttendanceSession.zone_id == zone_id)

        person_type = args.get("person_type", "").strip()
        if person_type and person_type != "all":
            query = query.filter(AttendanceSession.person_type == person_type)

        status = args.get("status", "").strip()
        if status and status != "all":
            query = query.filter(AttendanceSession.status == status)

        review_status = args.get("review_status", "").strip()
        if review_status and review_status != "all":
            query = query.filter(AttendanceSession.review_status == review_status)

        shift = args.get("shift", "").strip()
        if shift and shift != "all":
            hour = db.func.cast(db.func.strftime("%H", AttendanceSession.start_time), db.Integer)

            if shift == "morning":
                query = query.filter(hour >= 6, hour < 14)
            elif shift == "afternoon":
                query = query.filter(hour >= 14, hour < 22)
            elif shift == "night":
                query = query.filter(or_(hour >= 22, hour < 6))

        code = args.get("code", "").strip()
        if code:
            like = f"%{code}%"

            query = query.outerjoin(
                Employee,
                and_(
                    AttendanceSession.person_type == "employee",
                    AttendanceSession.employee_id == Employee.id
                )
            ).outerjoin(
                UnknownPerson,
                and_(
                    AttendanceSession.person_type == "unknown",
                    AttendanceSession.unknown_id == UnknownPerson.id
                )
            ).filter(
                or_(
                    Employee.personnel_code.like(like),
                    UnknownPerson.code.like(like)
                )
            )

        return query.order_by(AttendanceSession.start_time.desc())

    def query_sessions(self, args):
        return self._filter_sessions(args)

    def paginate_sessions(self, args, page: int, per_page: int):
        return self._filter_sessions(args).paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )

    def get_summary(self, args):
        query = self._filter_sessions(args)

        summary_row = query.with_entities(
            func.count(AttendanceSession.id),
            func.coalesce(func.sum(AttendanceSession.duration_seconds), 0)
        ).one()

        total_sessions = int(summary_row[0] or 0)
        total_duration = int(summary_row[1] or 0)

        employee_present = query.filter(
            AttendanceSession.person_type == "employee"
        ).with_entities(
            func.count(func.distinct(AttendanceSession.employee_id))
        ).scalar() or 0

        unknown_present = query.filter(
            AttendanceSession.person_type == "unknown"
        ).with_entities(
            func.count(func.distinct(AttendanceSession.unknown_id))
        ).scalar() or 0

        return {
            "total_sessions": total_sessions,
            "total_duration": total_duration,
            "total_duration_hours": round(total_duration / 3600.0, 1),
            "employee_present": employee_present,
            "unknown_present": unknown_present
        }

    def get_daily_chart(self, args):
        chart_args = args.copy()
        chart_args.pop("date", None)
        chart_args.pop("page", None)
        chart_args.pop("per_page", None)

        start_14 = datetime.utcnow() - timedelta(days=14)

        daily_query = self._filter_sessions(chart_args, include_date=False).filter(
            AttendanceSession.start_time >= start_14
        )

        daily_rows = daily_query.order_by(None).with_entities(
            func.date(AttendanceSession.start_time).label("day"),
            func.count(AttendanceSession.id)
        ).group_by("day").all()

        daily_map = {str(day): int(count) for day, count in daily_rows}

        labels = []
        values = []

        for i in range(13, -1, -1):
            d = (datetime.utcnow() - timedelta(days=i)).date()
            key = d.strftime("%Y-%m-%d")
            labels.append(key)
            values.append(daily_map.get(key, 0))

        return {
            "labels": labels,
            "values": values
        }

    def get_zone_chart(self, args):
        chart_args = args.copy()
        chart_args.pop("date", None)
        chart_args.pop("page", None)
        chart_args.pop("per_page", None)

        query = self._filter_sessions(chart_args, include_date=False)

        zone_rows = query.join(
            Zone,
            AttendanceSession.zone_id == Zone.id
        ).with_entities(
            Zone.name,
            func.count(AttendanceSession.id)
        ).group_by(Zone.name).limit(10).all()

        return {
            "labels": [row[0] for row in zone_rows],
            "values": [int(row[1]) for row in zone_rows]
        }

    def get_absence_day(self, args):
        date_str = args.get("date", "").strip()

        try:
            if date_str:
                return datetime.strptime(date_str, "%Y-%m-%d")
            return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    def get_absentees(self, args):
        absence_day = self.get_absence_day(args)

        present_ids = db.session.query(AttendanceSession.employee_id).filter(
            AttendanceSession.person_type == "employee",
            AttendanceSession.start_time >= absence_day,
            AttendanceSession.start_time < absence_day + timedelta(days=1),
            AttendanceSession.employee_id.isnot(None)
        ).distinct().subquery()

        return Employee.query.filter(
            Employee.active == True,  # noqa: E712
            Employee.id.notin_(present_ids)
        ).order_by(Employee.personnel_code).limit(50).all()