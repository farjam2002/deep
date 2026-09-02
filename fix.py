import os

dashboard_html = """{% extends "base.html" %}
{% block content %}
<h4 class="mb-3">داشبورد زنده</h4>
<div class="d-flex justify-content-between align-items-center mb-3">
    <div>
        {% if running %}
            <form method="post" action="{{ url_for('main.processing_stop') }}" class="d-inline">
                <button class="btn btn-danger">توقف پردازش</button>
            </form>
        {% else %}
            <form method="post" action="{{ url_for('main.processing_start') }}" class="d-inline">
                <button class="btn btn-success">شروع پردازش</button>
            </form>
        {% endif %}
    </div>
</div>
<div class="row mb-4">
    <div class="col-md-2"><div class="card text-bg-primary"><div class="card-body"><div>دوربین فعال</div><h3 id="stat-active-cameras">{{ stats.active_cameras }}</h3></div></div></div>
    <div class="col-md-2"><div class="card text-bg-success"><div class="card-body"><div>آنلاین</div><h3 id="stat-online-cameras">{{ stats.online_cameras }}</h3></div></div></div>
    <div class="col-md-2"><div class="card text-bg-secondary"><div class="card-body"><div>کارکنان</div><h3 id="stat-employees">{{ stats.employees }}</h3></div></div></div>
    <div class="col-md-2"><div class="card text-bg-warning"><div class="card-body"><div>ناشناس فعال</div><h3 id="stat-unknowns">{{ stats.unknowns }}</h3></div></div></div>
    <div class="col-md-2"><div class="card text-bg-info"><div class="card-body"><div>جلسات فعال</div><h3 id="stat-active-sessions">{{ stats.active_sessions }}</h3></div></div></div>
    <div class="col-md-2"><div class="card text-bg-dark"><div class="card-body"><div>رویدادهای امروز</div><h3 id="stat-today-events">{{ stats.today_events }}</h3></div></div></div>
</div>
<h5>دوربینها (ویدیو زنده)</h5>
<div class="row mb-4">
    {% for cam in cameras %}
        <div class="col-md-3 mb-3">
            <div class="card h-100">
                <img src="{{ url_for('main.live_stream', camera_id=cam.id) }}" class="card-img-top snap" style="background:#000;">
                <div class="card-body p-2">
                    <strong>{{ cam.name }}</strong><br>
                    <span id="cam-status-{{ cam.id }}" class="badge bg-secondary">{{ cam.status_fa }}</span>
                    <span class="badge bg-info">{{ cam.zone.name if cam.zone else "بدون محدوده" }}</span>
                    <div class="mt-2">
                        <form method="post" action="{{ url_for('main.camera_toggle', camera_id=cam.id) }}" class="d-inline">
                            <button class="btn btn-sm btn-outline-warning">{{ "غیرفعال" if cam.enabled else "فعال" }}</button>
                        </form>
                        <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('main.camera_edit', camera_id=cam.id) }}">ویرایش</a>
                    </div>
                </div>
            </div>
        </div>
    {% endfor %}
</div>
{% endblock %}

{% block scripts %}
<script>
    let ws = null;
    function connectWebSocket() {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(proto + "//" + location.host + "/ws/dashboard");
        ws.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                document.getElementById("stat-active-cameras").textContent = data.stats.active_cameras;
                document.getElementById("stat-online-cameras").textContent = data.stats.online_cameras;
                document.getElementById("stat-employees").textContent = data.stats.employees;
                document.getElementById("stat-unknowns").textContent = data.stats.unknowns;
                document.getElementById("stat-active-sessions").textContent = data.stats.active_sessions;
                document.getElementById("stat-today-events").textContent = data.stats.today_events;
            } catch (e) {}
        };
        ws.onclose = function() { setTimeout(connectWebSocket, 3000); };
    }
    connectWebSocket();
</script>
{% endblock %}
"""

reports_html = """{% extends "base.html" %}
{% block head %}<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>{% endblock %}
{% block content %}
<h4 class="mb-3">گزارش حضور و جلسات</h4>
<div class="row mb-3 g-2">
    <div class="col-md-3"><div class="card text-bg-primary"><div class="card-body"><div>تعداد جلسات</div><h3>{{ summary.total_sessions }}</h3></div></div></div>
    <div class="col-md-3"><div class="card text-bg-success"><div class="card-body"><div>مجموع مدت (ساعت)</div><h3>{{ summary.total_duration_hours }}</h3></div></div></div>
    <div class="col-md-3"><div class="card text-bg-info"><div class="card-body"><div>کارکنان حاضر</div><h3>{{ summary.employee_present }}</h3></div></div></div>
    <div class="col-md-3"><div class="card text-bg-warning"><div class="card-body"><div>ناشناس مشاهدهشده</div><h3>{{ summary.unknown_present }}</h3></div></div></div>
</div>
<div class="card mb-3">
    <div class="card-body">
        <form method="get" action="{{ url_for('main.reports') }}" class="row g-2">
            <div class="col-md-2"><label class="form-label small">تاریخ</label><input type="date" name="date" class="form-control form-control-sm" value="{{ args.get('date', '') }}"></div>
            <div class="col-md-2"><label class="form-label small">کد فرد</label><input type="text" name="code" class="form-control form-control-sm" value="{{ args.get('code', '') }}"></div>
            <div class="col-md-2"><label class="form-label small">محدوده</label><select name="zone_id" class="form-select form-select-sm"><option value="">همه</option>{% for z in zones %}<option value="{{ z.id }}" {% if args.get('zone_id', '')|int == z.id %}selected{% endif %}>{{ z.name }}</option>{% endfor %}</select></div>
            <div class="col-md-2"><label class="form-label small">نوع فرد</label><select name="person_type" class="form-select form-select-sm"><option value="all">همه</option><option value="employee" {% if args.get('person_type') == 'employee' %}selected{% endif %}>کارمند</option><option value="unknown" {% if args.get('person_type') == 'unknown' %}selected{% endif %}>ناشناس</option><option value="temporary" {% if args.get('person_type') == 'temporary' %}selected{% endif %}>موقت</option></select></div>
            <div class="col-md-2"><label class="form-label small">شیفت</label><select name="shift" class="form-select form-select-sm"><option value="all">همه</option><option value="morning" {% if args.get('shift') == 'morning' %}selected{% endif %}>صبح</option><option value="afternoon" {% if args.get('shift') == 'afternoon' %}selected{% endif %}>عصر</option><option value="night" {% if args.get('shift') == 'night' %}selected{% endif %}>شب</option></select></div>
            <div class="col-md-2"><label class="form-label small">وضعیت</label><select name="status" class="form-select form-select-sm"><option value="all">همه</option><option value="active" {% if args.get('status') == 'active' %}selected{% endif %}>فعال</option><option value="closed" {% if args.get('status') == 'closed' %}selected{% endif %}>بسته</option></select></div>
            <div class="col-md-12">
                <button class="btn btn-sm btn-primary">اعمال</button>
                <a class="btn btn-sm btn-success" href="{{ url_for('main.sessions_csv') }}{% if request.query_string %}?{{ request.query_string.decode() }}{% endif %}">خروجی CSV</a>
            </div>
        </form>
    </div>
</div>
<div class="row mb-3 g-2">
    <div class="col-md-7"><div class="card h-100"><div class="card-header">نمودار ۱۴ روز اخیر</div><div class="card-body"><canvas id="dailyChart" height="120"></canvas></div></div></div>
    <div class="col-md-5"><div class="card h-100"><div class="card-header">توزیع محدودهها</div><div class="card-body"><canvas id="zoneChart" height="120"></canvas></div></div></div>
</div>
<div class="card mb-3">
    <div class="card-header">غیبت احتمالی کارکنان در {{ selected_date }}</div>
    <div class="card-body p-0">
        <table class="table table-sm mb-0">
            <thead><tr><th>کد پرسنلی</th><th>نام</th></tr></thead>
            <tbody>
                {% for e in absentees %}<tr><td>{{ e.personnel_code }}</td><td>{{ e.full_name }}</td></tr>{% else %}<tr><td colspan="2" class="text-center text-muted">غیبتی ثبت نشد.</td></tr>{% endfor %}
            </tbody>
        </table>
    </div>
</div>
<div class="card">
    <div class="card-header">نتایج گزارش</div>
    <div class="card-body p-0">
        <div class="table-responsive">
            <table class="table table-sm table-hover mb-0">
                <thead><tr><th>#</th><th>نوع</th><th>کد</th><th>نام</th><th>محدوده</th><th>شروع</th><th>آخرین مشاهده</th><th>پایان</th><th>مدت</th><th>وضعیت</th><th>بررسی</th></tr></thead>
                <tbody>
                    {% for s in sessions %}
                        <tr>
                            <td>{{ loop.index }}</td>
                            <td>{{ s.person_type }}</td>
                            <td>{{ s.identity_code }}</td>
                            <td>{{ s.identity_name }}</td>
                            <td>{{ s.zone.name if s.zone else "-" }}</td>
                            <td>{{ format_datetime(s.start_time) }}</td>
                            <td>{{ format_datetime(s.last_seen_time) }}</td>
                            <td>{{ format_datetime(s.end_time) }}</td>
                            <td>{{ s.duration_seconds }}</td>
                            <td>{{ s.status }}</td>
                            <td>{{ s.review_status }}</td>
                        </tr>
                    {% else %}
                        <tr><td colspan="11" class="text-center text-muted">رکوردی یافت نشد.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
{% set pagination = sessions_pagination %}{% include "_pagination.html" %}
{% endblock %}

{% block scripts %}
<script>
    const dailyLabels = {{ daily_chart['labels']|tojson }};
    const dailyValues = {{ daily_chart['values']|tojson }};
    const zoneLabels = {{ zone_chart['labels']|tojson }};
    const zoneValues = {{ zone_chart['values']|tojson }};

    new Chart(document.getElementById("dailyChart"), {
        type: "bar",
        data: {
            labels: dailyLabels,
            datasets: [{
                label: "تعداد جلسات",
                data: dailyValues,
                backgroundColor: "#0d6efd"
            }]
        },
        options: { responsive: true, plugins: { legend: { display: false } } }
    });

    new Chart(document.getElementById("zoneChart"), {
        type: "pie",
        data: {
            labels: zoneLabels,
            datasets: [{
                data: zoneValues
            }]
        },
        options: { responsive: true }
    });
</script>
{% endblock %}
"""

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(dashboard_html)
    
with open('templates/reports.html', 'w', encoding='utf-8') as f:
    f.write(reports_html)

print("Templates updated successfully!")
