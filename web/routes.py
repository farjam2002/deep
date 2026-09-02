import csv
import io
import json
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    Response,
    send_file,
    abort,
    current_app
)

from sqlalchemy import or_

from infrastructure.extensions import db
from domain.models import (
    Zone,
    Camera,
    Employee,
    EmployeeFace,
    UnknownPerson,
    AttendanceSession,
    PresenceEvent
)

from core.config import get_config
from core.utils import allowed_image, unique_filename, format_datetime
from services import live as live_service
from services.backup import create_backup
from core.logging_setup import read_log_lines

bp = Blueprint("main", __name__)


def get_container():
    return current_app.extensions["container"]


def _pagination_qs(args, exclude=("page",)):
    items = []

    for key in args.keys():
        if key in exclude:
            continue

        for value in args.getlist(key):
            if value != "":
                items.append((key, value))

    return urlencode(items)


@bp.context_processor
def inject_globals():
    try:
        cam_manager = current_app.extensions["container"].camera_manager
    except Exception:
        cam_manager = None

    return {
        "camera_manager": cam_manager,
        "format_datetime": format_datetime
    }


# ----------------------------
# Dashboard
# ----------------------------

@bp.route("/")
def dashboard():
    container = get_container()
    cam_manager = container.camera_manager

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

    cameras = Camera.query.order_by(Camera.id).all()

    has_snapshot = {
        cam.id: bool(cam_manager.get_snapshot(cam.id))
        for cam in cameras
    }

    snapshot_versions = {
        cam.id: cam_manager.get_snapshot_version(cam.id)
        for cam in cameras
    }

    from domain.models import Alert
    alerts = Alert.query.order_by(Alert.created_at.desc()).limit(20).all()

    return render_template(
        "dashboard.html",
        stats=stats,
        cameras=cameras,
        has_snapshot=has_snapshot,
        snapshot_versions=snapshot_versions,
        alerts=alerts,
        running=cam_manager.running
    )


@bp.get("/api/live")
def api_live():
    container = get_container()
    payload = live_service.get_dashboard_payload(
        current_app._get_current_object(),
        container.camera_manager
    )
    return payload


@bp.post("/processing/start")
def processing_start():
    container = get_container()
    container.camera_manager.start_all()
    flash("پردازش دوربین‌ها شروع شد.", "success")
    return redirect(url_for("main.dashboard"))


@bp.post("/processing/stop")
def processing_stop():
    container = get_container()
    container.camera_manager.stop_all()
    flash("پردازش دوربین‌ها متوقف شد.", "warning")
    return redirect(url_for("main.dashboard"))


# ----------------------------
# Cameras
# ----------------------------

@bp.route("/cameras")
def cameras():
    container = get_container()

    items = Camera.query.order_by(Camera.id).all()
    zones = Zone.query.order_by(Zone.name).all()

    stats_dict = {
        cam.id: container.camera_manager.get_stats(cam.id)
        for cam in items
    }

    return render_template("cameras.html", cameras=items, zones=zones, stats_dict=stats_dict)


@bp.route("/cameras/new", methods=["GET", "POST"])
def camera_new():
    container = get_container()
    zones = Zone.query.order_by(Zone.name).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rtsp_url = request.form.get("rtsp_url", "").strip()
        zone_id = request.form.get("zone_id", type=int)
        enabled = bool(request.form.get("enabled"))
        active_mask = request.form.get("active_mask", "[]").strip() or "[]"

        if not name or not rtsp_url:
            flash("نام دوربین و آدرس RTSP الزامی هستند.", "error")
            return render_template("camera_form.html", zones=zones, camera=None)

        try:
            mask_data = json.loads(active_mask)
            if not isinstance(mask_data, list):
                raise ValueError
        except Exception:
            flash("ماسک ناحیه فعال باید یک لیست JSON معتبر باشد.", "error")
            return render_template("camera_form.html", zones=zones, camera=None)

        cam = Camera(
            name=name,
            rtsp_url=rtsp_url,
            zone_id=zone_id,
            enabled=enabled,
            active_mask=active_mask,
            status="inactive"
        )

        db.session.add(cam)
        db.session.commit()

        if enabled and container.camera_manager.running:
            container.camera_manager.start_camera(cam.id)

        flash("دوربین با موفقیت اضافه شد.", "success")
        return redirect(url_for("main.cameras"))

    return render_template("camera_form.html", zones=zones, camera=None)


@bp.route("/cameras/<int:camera_id>/edit", methods=["GET", "POST"])
def camera_edit(camera_id):
    container = get_container()

    cam = Camera.query.get_or_404(camera_id)
    zones = Zone.query.order_by(Zone.name).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rtsp_url = request.form.get("rtsp_url", "").strip()
        zone_id = request.form.get("zone_id", type=int)
        enabled = bool(request.form.get("enabled"))
        active_mask = request.form.get("active_mask", "[]").strip() or "[]"

        if not name or not rtsp_url:
            flash("نام دوربین و آدرس RTSP الزامی هستند.", "error")
            return render_template("camera_form.html", zones=zones, camera=cam)

        try:
            mask_data = json.loads(active_mask)
            if not isinstance(mask_data, list):
                raise ValueError
        except Exception:
            flash("ماسک ناحیه فعال باید یک لیست JSON معتبر باشد.", "error")
            return render_template("camera_form.html", zones=zones, camera=cam)

        cam.name = name
        cam.rtsp_url = rtsp_url
        cam.zone_id = zone_id
        cam.enabled = enabled
        cam.active_mask = active_mask

        db.session.commit()

        container.camera_manager.stop_camera(cam.id, set_inactive=False)

        if cam.enabled and container.camera_manager.running:
            container.camera_manager.start_camera(cam.id)

        flash("تغییرات ذخیره شد.", "success")
        return redirect(url_for("main.cameras"))

    return render_template("camera_form.html", zones=zones, camera=cam)


@bp.post("/cameras/<int:camera_id>/toggle")
def camera_toggle(camera_id):
    container = get_container()
    cam = Camera.query.get_or_404(camera_id)

    cam.enabled = not cam.enabled

    if cam.enabled:
        cam.status = "inactive"
        db.session.commit()

        if container.camera_manager.running:
            container.camera_manager.start_camera(cam.id)
    else:
        container.camera_manager.stop_camera(cam.id, set_inactive=False)
        cam.status = "disabled"
        db.session.commit()

    flash("وضعیت دوربین به‌روزرسانی شد.", "success")
    return redirect(url_for("main.cameras"))


@bp.post("/cameras/<int:camera_id>/restart")
def camera_restart(camera_id):
    container = get_container()
    cam = Camera.query.get_or_404(camera_id)

    container.camera_manager.stop_camera(cam.id, set_inactive=False)

    if cam.enabled:
        container.camera_manager.start_camera(cam.id)
        flash("اتصال مجدد دوربین شروع شد.", "success")
    else:
        flash("دوربین غیرفعال است.", "warning")

    return redirect(url_for("main.dashboard"))


@bp.post("/cameras/<int:camera_id>/delete")
def camera_delete(camera_id):
    container = get_container()
    cam = Camera.query.get_or_404(camera_id)

    sessions = AttendanceSession.query.filter_by(camera_id=cam.id).count()
    events = PresenceEvent.query.filter_by(camera_id=cam.id).count()

    if sessions > 0 or events > 0:
        flash("به دلیل وجود سوابق حضور، امکان حذف این دوربین وجود ندارد.", "error")
        return redirect(url_for("main.cameras"))

    container.camera_manager.stop_camera(cam.id, set_inactive=False)

    db.session.delete(cam)
    db.session.commit()

    flash("دوربین حذف شد.", "success")
    return redirect(url_for("main.cameras"))


@bp.get("/cameras/<int:camera_id>/snapshot")
def camera_snapshot(camera_id):
    container = get_container()
    snap = container.camera_manager.get_snapshot(camera_id)

    if snap:
        return Response(snap, mimetype="image/jpeg")

    return Response(status=204)


# ----------------------------
# Employees
# ----------------------------

@bp.route("/employees")
def employees():
    items = Employee.query.order_by(Employee.personnel_code).all()
    return render_template("employees.html", employees=items)


@bp.route("/employees/new", methods=["POST"])
def employee_new():
    container = get_container()

    personnel_code = request.form.get("personnel_code", "").strip()
    full_name = request.form.get("full_name", "").strip()

    if not personnel_code or not full_name:
        flash("کد پرسنلی و نام کارمند الزامی هستند.", "error")
        return redirect(url_for("main.employees"))

    exists = Employee.query.filter_by(personnel_code=personnel_code).first()
    if exists:
        flash("این کد پرسنلی قبلاً ثبت شده است.", "error")
        return redirect(url_for("main.employees"))

    employee = Employee(
        personnel_code=personnel_code,
        full_name=full_name,
        active=True
    )

    db.session.add(employee)
    db.session.commit()

    container.face_engine.mark_dirty()

    flash("کارمند با موفقیت اضافه شد.", "success")
    return redirect(url_for("main.employees"))


@bp.route("/employees/<int:employee_id>")
def employee_detail(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    faces = employee.faces.order_by(EmployeeFace.created_at.desc()).all()
    return render_template("employee_detail.html", employee=employee, faces=faces)


@bp.post("/employees/<int:employee_id>/toggle")
def employee_toggle(employee_id):
    container = get_container()
    employee = Employee.query.get_or_404(employee_id)

    employee.active = not employee.active
    db.session.commit()

    container.face_engine.mark_dirty()

    flash("وضعیت کارمند به‌روزرسانی شد.", "success")
    return redirect(request.referrer or url_for("main.employees"))


@bp.post("/employees/<int:employee_id>/delete")
def employee_delete(employee_id):
    container = get_container()
    employee = Employee.query.get_or_404(employee_id)

    sessions = AttendanceSession.query.filter_by(employee_id=employee.id).count()
    assigned_unknowns = UnknownPerson.query.filter_by(assigned_employee_id=employee.id).count()

    if sessions > 0 or assigned_unknowns > 0:
        flash("به دلیل وجود سوابق، امکان حذف این کارمند وجود ندارد.", "error")
        return redirect(url_for("main.employees"))

    for face in employee.faces:
        container.storage.delete(face.image_path)

    db.session.delete(employee)
    db.session.commit()

    container.face_engine.mark_dirty()

    flash("کارمند حذف شد.", "success")
    return redirect(url_for("main.employees"))


@bp.post("/employees/<int:employee_id>/faces/upload")
def employee_upload_faces(employee_id):
    container = get_container()
    employee = Employee.query.get_or_404(employee_id)

    files = request.files.getlist("files")

    if not files:
        flash("هیچ فایلی انتخاب نشد.", "error")
        return redirect(url_for("main.employee_detail", employee_id=employee.id))

    saved = 0

    for file in files:
        if not file or not file.filename:
            continue

        if not allowed_image(file.filename):
            continue

        filename = unique_filename(file.filename)

        try:
            path = container.storage.save_uploaded_file("employees", file, filename)

            face = EmployeeFace(
                employee_id=employee.id,
                image_path=path
            )

            db.session.add(face)
            saved += 1
        except Exception:
            continue

    db.session.commit()

    if saved > 0:
        container.face_engine.mark_dirty()
        flash(f"{saved} تصویر چهره ذخیره شد.", "success")
    else:
        flash("هیچ تصویر معتبری ذخیره نشد.", "error")

    return redirect(url_for("main.employee_detail", employee_id=employee.id))


@bp.post("/faces/<int:face_id>/delete")
def face_delete(face_id):
    container = get_container()
    face = EmployeeFace.query.get_or_404(face_id)
    employee_id = face.employee_id

    container.storage.delete(face.image_path)

    db.session.delete(face)
    db.session.commit()

    container.face_engine.mark_dirty()

    flash("تصویر چهره حذف شد.", "success")
    return redirect(url_for("main.employee_detail", employee_id=employee_id))


@bp.get("/faces/<int:face_id>/image")
def face_image(face_id):
    container = get_container()
    face = EmployeeFace.query.get_or_404(face_id)

    if not container.storage.exists(face.image_path):
        abort(404)

    return send_file(face.image_path, mimetype="image/jpeg")


# ----------------------------
# Unknowns
# ----------------------------

@bp.route("/unknowns")
def unknowns():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "all").strip()

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(max(per_page, 10), 100)

    query = UnknownPerson.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                UnknownPerson.code.like(like),
                UnknownPerson.notes.like(like)
            )
        )

    if status == "active":
        query = query.filter(
            UnknownPerson.active == True,  # noqa: E712
            UnknownPerson.merged_into_id.is_(None)
        )
    elif status == "assigned":
        query = query.filter(UnknownPerson.assigned_employee_id.isnot(None))
    elif status == "merged":
        query = query.filter(UnknownPerson.merged_into_id.isnot(None))

    pagination = query.order_by(
        UnknownPerson.first_seen_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    items = pagination.items

    employees = Employee.query.filter_by(active=True).order_by(Employee.personnel_code).all()

    all_unknowns = UnknownPerson.query.filter(
        UnknownPerson.active == True,  # noqa: E712
        UnknownPerson.merged_into_id.is_(None)
    ).order_by(UnknownPerson.code).limit(500).all()

    pagination_qs = _pagination_qs(request.args)

    return render_template(
        "unknowns.html",
        unknowns=items,
        pagination=pagination,
        pagination_qs=pagination_qs,
        endpoint="main.unknowns",
        employees=employees,
        all_unknowns=all_unknowns,
        q=q,
        status=status
    )


@bp.get("/unknowns/<int:unknown_id>/image")
def unknown_image(unknown_id):
    container = get_container()
    unknown = UnknownPerson.query.get_or_404(unknown_id)

    if not container.storage.exists(unknown.sample_image_path):
        abort(404)

    return send_file(unknown.sample_image_path, mimetype="image/jpeg")


@bp.post("/unknowns/<int:unknown_id>/assign")
def unknown_assign(unknown_id):
    container = get_container()
    unknown = UnknownPerson.query.get_or_404(unknown_id)

    employee_id = request.form.get("employee_id", type=int)
    if not employee_id:
        flash("کارمند انتخاب نشده است.", "error")
        return redirect(url_for("main.unknowns"))

    employee = Employee.query.get_or_404(employee_id)

    if unknown.sample_image_path:
        new_filename = unique_filename(os.path.basename(unknown.sample_image_path))
        new_path = container.storage.copy_file("employees", unknown.sample_image_path, new_filename)

        if new_path:
            face = EmployeeFace(
                employee_id=employee.id,
                image_path=new_path
            )
            db.session.add(face)

    unknown.assigned_employee_id = employee.id
    unknown.active = False

    db.session.commit()

    container.face_engine.mark_dirty()

    flash(f"ناشناس {unknown.code} به کارمند {employee.full_name} اختصاص یافت.", "success")
    return redirect(url_for("main.unknowns"))


@bp.post("/unknowns/merge")
def unknown_merge():
    container = get_container()

    target_id = request.form.get("target_id", type=int)
    selected_ids = request.form.getlist("selected_ids", type=int)

    if not target_id or len(selected_ids) == 0:
        flash("برای ادغام، باید هدف و حداقل یک ناشناس انتخاب شود.", "error")
        return redirect(url_for("main.unknowns"))

    target = UnknownPerson.query.get_or_404(target_id)

    merged_count = 0

    for source_id in selected_ids:
        if source_id == target_id:
            continue

        source = UnknownPerson.query.get(source_id)
        if not source:
            continue

        AttendanceSession.query.filter_by(unknown_id=source.id).update(
            {"unknown_id": target.id},
            synchronize_session=False
        )

        PresenceEvent.query.filter_by(unknown_id=source.id).update(
            {"unknown_id": target.id},
            synchronize_session=False
        )

        source.active = False
        source.merged_into_id = target.id

        note = f"[merged into {target.code}] "
        source.notes = (source.notes or "") + " " + note

        if source.last_seen_at and (not target.last_seen_at or source.last_seen_at > target.last_seen_at):
            target.last_seen_at = source.last_seen_at

        merged_count += 1

    db.session.commit()

    container.face_engine.mark_dirty()

    flash(f"{merged_count} ناشناس در {target.code} ادغام شدند.", "success")
    return redirect(url_for("main.unknowns"))


# ----------------------------
# Reports
# ----------------------------

@bp.route("/reports")
def reports():
    container = get_container()
    report_service = container.report_service

    args = request.args

    page = args.get("page", 1, type=int)
    per_page = args.get("per_page", 20, type=int)
    per_page = min(max(per_page, 10), 100)

    zones = Zone.query.order_by(Zone.name).all()

    summary = report_service.get_summary(args)
    daily_chart = report_service.get_daily_chart(args)
    zone_chart = report_service.get_zone_chart(args)

    absence_day = report_service.get_absence_day(args)
    absentees = report_service.get_absentees(args)

    sessions_pagination = report_service.paginate_sessions(args, page, per_page)
    sessions = sessions_pagination.items

    pagination_qs = _pagination_qs(args)

    return render_template(
        "reports.html",
        sessions=sessions,
        sessions_pagination=sessions_pagination,
        pagination_qs=pagination_qs,
        endpoint="main.reports",
        zones=zones,
        args=args,
        summary=summary,
        daily_chart=daily_chart,
        zone_chart=zone_chart,
        absentees=absentees,
        selected_date=absence_day.strftime("%Y-%m-%d")
    )


@bp.route("/reports/sessions.csv")
def sessions_csv():
    container = get_container()
    report_service = container.report_service

    sessions = report_service.query_sessions(request.args).limit(20000).all()

    headers = [
        "ID",
        "PersonType",
        "Code",
        "Name",
        "Zone",
        "Camera",
        "Start",
        "LastSeen",
        "End",
        "DurationSeconds",
        "Status",
        "Review",
        "AvgConfidence",
        "TrackKey"
    ]

    output = io.StringIO()
    output.write("\ufeff")

    writer = csv.writer(output)
    writer.writerow(headers)

    for s in sessions:
        writer.writerow([
            s.id,
            s.person_type,
            s.identity_code,
            s.identity_name,
            s.zone.name if s.zone else "",
            s.camera.name if s.camera else "",
            format_datetime(s.start_time),
            format_datetime(s.last_seen_time),
            format_datetime(s.end_time),
            s.duration_seconds or 0,
            s.status,
            s.review_status,
            s.avg_confidence if s.avg_confidence is not None else "",
            s.track_key or ""
        ])

    filename = f"sessions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"

    return response


# ----------------------------
# Review
# ----------------------------

@bp.route("/review")
def review():
    sessions = AttendanceSession.query.filter_by(
        review_status="pending"
    ).order_by(AttendanceSession.last_seen_time.desc()).limit(300).all()

    employees = Employee.query.filter_by(active=True).order_by(Employee.personnel_code).all()

    return render_template("review.html", sessions=sessions, employees=employees)


@bp.post("/review/<int:session_id>/approve")
def review_approve(session_id):
    session = AttendanceSession.query.get_or_404(session_id)

    session.review_status = "approved"
    session.reviewed_at = datetime.utcnow()

    db.session.commit()

    flash("جلسه تأیید شد.", "success")
    return redirect(url_for("main.review"))


@bp.post("/review/<int:session_id>/reject")
def review_reject(session_id):
    session = AttendanceSession.query.get_or_404(session_id)

    session.review_status = "rejected"
    session.reviewed_at = datetime.utcnow()

    db.session.commit()

    flash("جلسه رد شد.", "warning")
    return redirect(url_for("main.review"))


@bp.post("/review/<int:session_id>/correct")
def review_correct(session_id):
    session = AttendanceSession.query.get_or_404(session_id)

    employee_id = request.form.get("employee_id", type=int)
    if not employee_id:
        flash("کارمند انتخاب نشده است.", "error")
        return redirect(url_for("main.review"))

    employee = Employee.query.get_or_404(employee_id)

    session.person_type = "employee"
    session.employee_id = employee.id
    session.unknown_id = None

    session.review_status = "corrected"
    session.reviewed_at = datetime.utcnow()

    db.session.commit()

    flash("جلسه به کارمند انتخابی اصلاح شد.", "success")
    return redirect(url_for("main.review"))


# ----------------------------
# Logs
# ----------------------------

@bp.route("/logs")
def logs():
    cfg = get_config()
    lines = read_log_lines(cfg, 300)
    lines.reverse()
    log_text = "".join(lines)
    return render_template("logs.html", log_text=log_text)


# ----------------------------
# Backup
# ----------------------------

@bp.post("/backup")
def create_backup_route():
    try:
        path = create_backup()
        return send_file(
            path,
            as_attachment=True,
            download_name=os.path.basename(path)
        )
    except Exception:
        flash("خطا در تهیه نسخه پشتیبان.", "error")
        return redirect(url_for("main.dashboard"))




# ----------------------------
# Live Stream
# ----------------------------

import time

def generate_frames(camera_manager, camera_id):
    while True:
        frame = camera_manager.get_snapshot(camera_id)
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.1)


@bp.route('/live/<int:camera_id>')
def live_stream(camera_id):
    container = get_container()
    return Response(generate_frames(container.camera_manager, camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')
