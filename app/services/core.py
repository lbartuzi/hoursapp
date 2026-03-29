
import csv
import hashlib
import io
import json
import re
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import current_app, flash, get_flashed_messages, redirect, request, send_file, session, url_for
from openpyxl import Workbook

from app.db import get_db, get_setting, parse_iso, set_setting, utcnow, utcnow_iso


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def normalize_client(value):
    return (value or "").strip()


def normalize_email(value):
    return re.sub(r"\s+", "", (value or "").strip().lower())


def render_text_template(template_text, context):
    result = template_text
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def app_name():
    return get_setting("app_name") or "Hours Admin"


def send_email(to, subject, body):
    host = current_app.config["SMTP_HOST"]
    sender = current_app.config["SMTP_FROM"]
    if not host or not sender:
        return False, "SMTP is not configured."
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=20) as server:
        if current_app.config["SMTP_USE_TLS"]:
            server.starttls()
        if current_app.config["SMTP_USER"]:
            server.login(current_app.config["SMTP_USER"], current_app.config["SMTP_PASS"])
        server.send_message(msg)
    return True, None


def send_template_email(template_subject_key, template_body_key, to, context):
    context = {**context, "app_name": app_name(), "public_base_url": current_app.config["PUBLIC_BASE_URL"]}
    subject = render_text_template(get_setting(template_subject_key), context)
    body = render_text_template(get_setting(template_body_key), context)
    return send_email(to, subject, body)


def make_token_hash(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_token(token_type, *, user_id=None, email=None, payload=None, expires_hours=48):
    raw_token = secrets.token_urlsafe(32)
    token_hash = make_token_hash(raw_token)
    payload_json = json.dumps(payload or {})
    expires_at = (utcnow() + timedelta(hours=expires_hours)).replace(microsecond=0).isoformat()
    get_db().execute(
        """
        INSERT INTO tokens (token_hash, token_type, user_id, email, payload_json, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (token_hash, token_type, user_id, email, payload_json, expires_at, utcnow_iso()),
    )
    get_db().commit()
    return raw_token, expires_at


def fetch_token(raw_token, expected_type):
    row = get_db().execute(
        """
        SELECT * FROM tokens
        WHERE token_hash = ? AND token_type = ? AND used_at IS NULL
        """,
        (make_token_hash(raw_token), expected_type),
    ).fetchone()
    if not row:
        return None
    if parse_iso(row["expires_at"]) < utcnow():
        return None
    return row


def mark_token_used(token_id):
    get_db().execute("UPDATE tokens SET used_at = ? WHERE id = ?", (utcnow_iso(), token_id))
    get_db().commit()


def username_exists(username, exclude_user_id=None):
    username_norm = (username or "").strip().lower()
    if not username_norm:
        return False
    query = "SELECT id FROM users WHERE lower(username) = ?"
    params = [username_norm]
    if exclude_user_id is not None:
        query += " AND id != ?"
        params.append(int(exclude_user_id))
    return get_db().execute(query, params).fetchone() is not None


def suggest_username(base_username):
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "", (base_username or "").strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        cleaned = "user"
    candidate = cleaned
    counter = 2
    while username_exists(candidate):
        candidate = f"{cleaned}{counter}"
        counter += 1
    return candidate


def hours_from_minutes_input(value):
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        raise ValueError("Minutes is required.")
    minutes = float(raw)
    if minutes < 0:
        raise ValueError("Minutes cannot be negative.")
    return minutes / 60.0


def minutes_from_hours(hours_value):
    return int(round(float(hours_value) * 60))


def format_hours_hm(hours_value):
    total_minutes = minutes_from_hours(hours_value)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes:02d}m"


def entry_exists(user_id, work_date, hours, client, exclude_id=None):
    client = normalize_client(client)
    query = """
        SELECT id FROM entries
        WHERE user_id = ?
          AND work_date = ?
          AND hours = ?
          AND COALESCE(client, '') = ?
    """
    params = [int(user_id), work_date, float(hours), client]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(int(exclude_id))
    return get_db().execute(query, params).fetchone() is not None


def parse_import_file(file_storage):
    raw = file_storage.read()
    if not raw:
        raise ValueError("The uploaded file is empty.")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        raise ValueError("Could not detect CSV headers.")

    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "date" not in headers:
        raise ValueError("Missing required column: date")
    if "hours" not in headers and "minutes" not in headers:
        raise ValueError("Missing required column: add either hours or minutes.")

    rows = []
    for idx, row in enumerate(reader, start=2):
        work_date = (row.get(headers["date"]) or "").strip()
        hours_raw = (row.get(headers["hours"]) or "").strip().replace(",", ".") if "hours" in headers else ""
        minutes_raw = (row.get(headers["minutes"]) or "").strip().replace(",", ".") if "minutes" in headers else ""
        client = normalize_client(row.get(headers["client"]) if "client" in headers else "")
        project = (row.get(headers["project"]) or "").strip() if "project" in headers else ""
        activity = (row.get(headers["activity"]) or "").strip() if "activity" in headers else ""
        hour_type = (row.get(headers["type"]) or "direct").strip().lower() if "type" in headers else "direct"
        notes = (row.get(headers["notes"]) or "").strip() if "notes" in headers else ""
        username = (row.get(headers["user"]) or "").strip() if "user" in headers else ""
        if not work_date:
            raise ValueError(f"Row {idx}: date is required.")
        try:
            datetime.strptime(work_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f'Row {idx}: invalid date "{work_date}". Expected YYYY-MM-DD.') from exc
        if not hours_raw and not minutes_raw:
            raise ValueError(f"Row {idx}: add hours or minutes.")
        try:
            if minutes_raw:
                minutes = float(minutes_raw)
                if minutes < 0:
                    raise ValueError
                hours = minutes / 60.0
            else:
                hours = float(hours_raw)
                if hours < 0:
                    raise ValueError
        except ValueError as exc:
            raise ValueError(f"Row {idx}: invalid duration value.") from exc
        if hour_type not in ("direct", "indirect"):
            raise ValueError(f"Row {idx}: type must be direct or indirect.")
        rows.append({"line": idx, "work_date": work_date, "hours": hours, "client": client, "project": project, "activity": activity, "hour_type": hour_type, "notes": notes, "username": username})
    return rows


def parse_filters():
    return (
        request.args.get("date_from", ""),
        request.args.get("date_to", ""),
        request.args.get("client", "").strip(),
        request.args.get("project", "").strip(),
        request.args.get("user_id", ""),
    )


def build_entry_query(for_export=False):
    db = get_db()
    date_from, date_to, client, project, selected_user = parse_filters()
    query = """
        SELECT entries.*, users.username
        FROM entries
        JOIN users ON users.id = entries.user_id
        WHERE 1=1
    """
    params = []
    if date_from:
        query += " AND work_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND work_date <= ?"
        params.append(date_to)
    if client:
        query += " AND client LIKE ?"
        params.append(f"%{client}%")
    if project:
        query += " AND project LIKE ?"
        params.append(f"%{project}%")
    me = current_user()
    if me and me["role"] != "admin":
        query += " AND entries.user_id = ?"
        params.append(me["id"])
        selected_user = str(me["id"])
    elif me and selected_user:
        query += " AND entries.user_id = ?"
        params.append(selected_user)
    query += " ORDER BY work_date DESC, entries.id DESC"
    rows = db.execute(query, params).fetchall()
    if for_export:
        return rows
    users = db.execute("SELECT id, username FROM users ORDER BY username").fetchall()
    return rows, users, date_from, date_to, client, project, selected_user


def get_filter_context():
    rows = build_entry_query(for_export=True)
    db = get_db()
    me = current_user()
    users = db.execute("SELECT id, username FROM users ORDER BY username").fetchall()
    date_from, date_to, client, project, selected_user = parse_filters()
    if me and me["role"] != "admin":
        selected_user = str(me["id"])
    return {"rows": rows, "users": users, "me": me, "date_from": date_from, "date_to": date_to, "client": client, "project": project, "selected_user": selected_user}


def compute_dashboard_metrics(rows):
    monthly = {}
    direct_hours = 0.0
    indirect_hours = 0.0
    by_client = {}
    by_project = {}
    by_weekday = {idx: 0.0 for idx in range(7)}
    active_dates = set()
    for row in rows:
        work_date = datetime.strptime(row["work_date"], "%Y-%m-%d")
        month_key = work_date.strftime("%Y-%m")
        monthly[month_key] = monthly.get(month_key, 0.0) + float(row["hours"])
        hours = float(row["hours"])
        if row["hour_type"] == "direct": direct_hours += hours
        else: indirect_hours += hours
        client_key = (row["client"] or "").strip() or "No client"
        project_key = (row["project"] or "").strip() or "No project"
        by_client[client_key] = by_client.get(client_key, 0.0) + hours
        by_project[project_key] = by_project.get(project_key, 0.0) + hours
        by_weekday[work_date.weekday()] += hours
        active_dates.add(row["work_date"])
    monthly_items = sorted(monthly.items())
    monthly_labels = monthly_items[-12:]
    top_clients = sorted(by_client.items(), key=lambda item: item[1], reverse=True)[:8]
    top_projects = sorted(by_project.items(), key=lambda item: item[1], reverse=True)[:8]
    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_values = [round(by_weekday[idx], 2) for idx in range(7)]
    total_hours = direct_hours + indirect_hours
    active_days = len(active_dates)
    avg_hours_per_active_day = round(total_hours / active_days, 2) if active_days else 0.0
    active_clients = sum(1 for name, value in by_client.items() if value > 0 and name != "No client")
    active_projects = sum(1 for name, value in by_project.items() if value > 0 and name != "No project")
    busiest_month = max(monthly_items, key=lambda item: item[1]) if monthly_items else None
    busiest_weekday_idx = max(range(7), key=lambda idx: weekday_values[idx]) if any(weekday_values) else None
    recent_month_total = monthly_items[-1][1] if monthly_items else 0.0
    return {
        "total_hours": total_hours, "direct_hours": direct_hours, "indirect_hours": indirect_hours, "active_days": active_days,
        "avg_hours_per_active_day": avg_hours_per_active_day, "active_clients": active_clients, "active_projects": active_projects,
        "recent_month_total": recent_month_total, "busiest_month_label": busiest_month[0] if busiest_month else "n/a",
        "busiest_month_hours": busiest_month[1] if busiest_month else 0.0,
        "busiest_weekday_label": weekday_labels[busiest_weekday_idx] if busiest_weekday_idx is not None else "n/a",
        "monthly_chart_labels": [label for label, _ in monthly_labels],
        "monthly_chart_values": [round(value, 2) for _, value in monthly_labels],
        "top_clients_labels": [name for name, _ in top_clients], "top_clients_values": [round(value, 2) for _, value in top_clients],
        "top_projects_labels": [name for name, _ in top_projects], "top_projects_values": [round(value, 2) for _, value in top_projects],
        "weekday_labels": weekday_labels, "weekday_values": weekday_values,
    }


def build_dashboard_png(metrics, title):
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(title, fontsize=18)
    ax1 = fig.add_subplot(2, 2, 1)
    if metrics["monthly_chart_labels"]:
        ax1.bar(metrics["monthly_chart_labels"], metrics["monthly_chart_values"])
        ax1.tick_params(axis="x", rotation=45)
    else:
        ax1.text(0.5, 0.5, "No data", ha="center", va="center")
    ax1.set_title("Hours per month")
    ax1.set_ylabel("Hours")
    ax2 = fig.add_subplot(2, 2, 2)
    split_values = [metrics["direct_hours"], metrics["indirect_hours"]]
    if sum(split_values) > 0: ax2.pie(split_values, labels=["Direct", "Indirect"], autopct="%1.1f%%")
    else: ax2.text(0.5, 0.5, "No data", ha="center", va="center")
    ax2.set_title("Direct vs indirect")
    ax3 = fig.add_subplot(2, 2, 3)
    if metrics["top_clients_labels"]: ax3.barh(metrics["top_clients_labels"][::-1], metrics["top_clients_values"][::-1])
    else: ax3.text(0.5, 0.5, "No data", ha="center", va="center")
    ax3.set_title("Top clients")
    ax3.set_xlabel("Hours")
    ax4 = fig.add_subplot(2, 2, 4)
    if metrics["weekday_labels"]: ax4.bar(metrics["weekday_labels"], metrics["weekday_values"])
    else: ax4.text(0.5, 0.5, "No data", ha="center", va="center")
    ax4.set_title("Hours by weekday")
    ax4.set_ylabel("Hours")
    summary = (
        f"Total: {format_hours_hm(metrics['total_hours'])} | Direct: {format_hours_hm(metrics['direct_hours'])} | "
        f"Indirect: {format_hours_hm(metrics['indirect_hours'])} | Active days: {metrics['active_days']} | "
        f"Avg/active day: {format_hours_hm(metrics['avg_hours_per_active_day'])}"
    )
    fig.text(0.5, 0.02, summary, ha="center", fontsize=10)
    fig.tight_layout(rect=[0, 0.05, 1, 0.95])
    stream = io.BytesIO()
    fig.savefig(stream, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    stream.seek(0)
    return stream


def user_retention_reference(user_row):
    return parse_iso(user_row["last_login"]) or parse_iso(user_row["created_at"]) or utcnow()


def run_retention_tasks():
    db = get_db()
    users = db.execute("SELECT * FROM users WHERE role != 'admin'").fetchall()
    deleted_count = 0
    warned_count = 0
    for user in users:
        ref_dt = user_retention_reference(user)
        age_days = (utcnow() - ref_dt).days
        deletion_dt = ref_dt + timedelta(days=current_app.config["RETENTION_DAYS"])
        warn_threshold = current_app.config["RETENTION_DAYS"] - current_app.config["RETENTION_WARNING_DAYS"]
        if age_days >= current_app.config["RETENTION_DAYS"]:
            db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
            deleted_count += 1
            continue
        if age_days >= warn_threshold and not user["deletion_reminder_sent_at"]:
            send_template_email(
                "template_retention_subject", "template_retention_body", user["email"],
                {"email": user["email"], "login_link": f"{current_app.config['PUBLIC_BASE_URL']}{url_for('login')}", "deletion_date": deletion_dt.date().isoformat()},
            )
            db.execute("UPDATE users SET deletion_reminder_sent_at = ? WHERE id = ?", (utcnow_iso(), user["id"]))
            warned_count += 1
    db.commit()
    return {"warned": warned_count, "deleted": deleted_count}


def run_retention_tasks_if_due():
    try:
        last_run = get_setting("last_retention_run_at")
        if last_run and parse_iso(last_run) and parse_iso(last_run) > utcnow() - timedelta(hours=20):
            return
        run_retention_tasks()
        set_setting("last_retention_run_at", utcnow_iso())
        get_db().commit()
    except Exception:
        get_db().rollback()


def export_csv_response():
    rows = build_entry_query(for_export=True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user", "date", "minutes", "hours", "duration", "client", "project", "activity", "type", "notes"])
    for row in rows:
        writer.writerow([row["id"], row["username"], row["work_date"], minutes_from_hours(row["hours"]), row["hours"], format_hours_hm(row["hours"]), row["client"], row["project"], row["activity"], row["hour_type"], row["notes"]])
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="hours_export.csv")


def export_xlsx_response():
    rows = build_entry_query(for_export=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Hours"
    headers = ["ID", "User", "Date", "Minutes", "Hours", "Duration", "Client", "Project", "Activity", "Type", "Notes"]
    ws.append(headers)
    for row in rows:
        ws.append([row["id"], row["username"], row["work_date"], minutes_from_hours(row["hours"]), row["hours"], format_hours_hm(row["hours"]), row["client"], row["project"], row["activity"], row["hour_type"], row["notes"]])
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 40)
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return send_file(stream, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="hours_export.xlsx")
