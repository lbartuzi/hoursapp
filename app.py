
import csv
import io
import os
import sqlite3
import hashlib
import json
import secrets
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import (
    Flask,
    abort,
    flash,
    g,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

DB_PATH = os.environ.get("DB_PATH", "/app/data/hours.db")
DEFAULT_ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or DEFAULT_ADMIN_EMAIL)
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "on")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "365"))
RETENTION_WARNING_DAYS = int(os.environ.get("RETENTION_WARNING_DAYS", "28"))
SELF_REGISTRATION_ENABLED = os.environ.get("SELF_REGISTRATION_ENABLED", "false").lower() in ("1", "true", "yes", "on")


DEFAULT_SETTINGS = {
    "app_name": "Hours Admin",
    "template_invite_subject": "You're invited to {{app_name}}",
    "template_invite_body": (
        "Hello,\n\n"
        "You have been invited to create an account on {{app_name}}.\n\n"
        "Complete your registration here:\n{{register_link}}\n\n"
        "This invite expires on {{expires_at}}.\n"
    ),
    "self_registration_enabled": "true" if SELF_REGISTRATION_ENABLED else "false",
    "template_confirm_subject": "Confirm your email for {{app_name}}",
    "template_confirm_body": (
        "Hello,\n\n"
        "Please confirm your email address by opening the link below:\n"
        "{{confirm_link}}\n\n"
        "This confirmation link expires on {{expires_at}}.\n"
    ),
    "template_reset_subject": "Reset your password for {{app_name}}",
    "template_reset_body": (
        "Hello,\n\n"
        "A password reset was requested for your {{app_name}} account.\n"
        "Reset it here:\n{{reset_link}}\n\n"
        "If you did not request this, you can ignore this email.\n"
        "This reset link expires on {{expires_at}}.\n"
    ),
    "template_retention_subject": "Account removal warning for {{app_name}}",
    "template_retention_body": (
        "Hello,\n\n"
        "Your {{app_name}} account has been inactive. According to the data retention policy, "
        "your account and related data will be removed on {{deletion_date}} unless you log in before then.\n\n"
        "You can sign in here:\n{{login_link}}\n"
    ),
}


def utcnow():
    return datetime.utcnow()


def utcnow_iso():
    return utcnow().replace(microsecond=0).isoformat()


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def column_names(db, table_name):
    return [row[1] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()]


def ensure_column(db, table, definition):
    col_name = definition.split()[0]
    if col_name not in column_names(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            email_confirmed INTEGER NOT NULL DEFAULT 0,
            theme_pref TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT NOT NULL,
            last_login TEXT,
            deletion_reminder_sent_at TEXT
        )
        """
    )

    existing = db.execute(
        "SELECT id FROM users WHERE username = ?",
        (DEFAULT_ADMIN_USER,),
    ).fetchone()
    if not existing:
        db.execute(
            """
            INSERT INTO users
            (username, email, password_hash, role, email_confirmed, theme_pref, created_at, last_login)
            VALUES (?, ?, ?, 'admin', 1, 'auto', ?, ?)
            """,
            (
                DEFAULT_ADMIN_USER,
                DEFAULT_ADMIN_EMAIL,
                generate_password_hash(DEFAULT_ADMIN_PASSWORD),
                utcnow_iso(),
                utcnow_iso(),
            ),
        )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            hours REAL NOT NULL,
            client TEXT,
            project TEXT,
            activity TEXT,
            hour_type TEXT NOT NULL CHECK(hour_type IN ('direct', 'indirect')),
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    ensure_column(db, "entries", "user_id INTEGER")
    ensure_column(db, "users", "email TEXT")
    ensure_column(db, "users", "role TEXT NOT NULL DEFAULT 'user'")
    ensure_column(db, "users", "email_confirmed INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "theme_pref TEXT NOT NULL DEFAULT 'auto'")
    ensure_column(db, "users", "last_login TEXT")
    ensure_column(db, "users", "deletion_reminder_sent_at TEXT")

    admin_id = db.execute(
        "SELECT id FROM users WHERE username = ?",
        (DEFAULT_ADMIN_USER,),
    ).fetchone()["id"]

    # Migrate legacy data when email/user_id missing
    users_cols = column_names(db, "users")
    if "email" in users_cols:
        db.execute(
            "UPDATE users SET email = COALESCE(email, username || '@local.invalid') WHERE email IS NULL OR email = ''"
        )
    db.execute("UPDATE users SET role = 'admin' WHERE username = ?", (DEFAULT_ADMIN_USER,))
    db.execute("UPDATE users SET email_confirmed = 1 WHERE username = ?", (DEFAULT_ADMIN_USER,))

    entries_cols = column_names(db, "entries")
    if "user_id" in entries_cols:
        db.execute("UPDATE entries SET user_id = ? WHERE user_id IS NULL", (admin_id,))

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            token_type TEXT NOT NULL,
            user_id INTEGER,
            email TEXT,
            payload_json TEXT,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    for key, value in DEFAULT_SETTINGS.items():
        existing_setting = db.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
        if not existing_setting:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    db.commit()
    db.close()


def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def app_name():
    return get_setting("app_name") or DEFAULT_SETTINGS["app_name"]


def render_text_template(template_text, context):
    result = template_text
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def send_email(to, subject, body):
    if not SMTP_HOST or not SMTP_FROM:
        return False, "SMTP is not configured."
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    return True, None


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


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        me = current_user()
        if not me or me["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped_view


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def normalize_client(value):
    return (value or "").strip()


def normalize_email(value):
    email = re.sub(r"\s+", "", (value or "").strip().lower())
    return email


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

        rows.append({
            "line": idx,
            "work_date": work_date,
            "hours": hours,
            "client": client,
            "project": project,
            "activity": activity,
            "hour_type": hour_type,
            "notes": notes,
            "username": username,
        })
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
    return {
        "rows": rows,
        "users": users,
        "me": me,
        "date_from": date_from,
        "date_to": date_to,
        "client": client,
        "project": project,
        "selected_user": selected_user,
    }


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
        if row["hour_type"] == "direct":
            direct_hours += hours
        else:
            indirect_hours += hours
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
        "total_hours": total_hours,
        "direct_hours": direct_hours,
        "indirect_hours": indirect_hours,
        "active_days": active_days,
        "avg_hours_per_active_day": avg_hours_per_active_day,
        "active_clients": active_clients,
        "active_projects": active_projects,
        "recent_month_total": recent_month_total,
        "busiest_month_label": busiest_month[0] if busiest_month else "n/a",
        "busiest_month_hours": busiest_month[1] if busiest_month else 0.0,
        "busiest_weekday_label": weekday_labels[busiest_weekday_idx] if busiest_weekday_idx is not None else "n/a",
        "monthly_chart_labels": [label for label, _ in monthly_labels],
        "monthly_chart_values": [round(value, 2) for _, value in monthly_labels],
        "top_clients_labels": [name for name, _ in top_clients],
        "top_clients_values": [round(value, 2) for _, value in top_clients],
        "top_projects_labels": [name for name, _ in top_projects],
        "top_projects_values": [round(value, 2) for _, value in top_projects],
        "weekday_labels": weekday_labels,
        "weekday_values": weekday_values,
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
    if sum(split_values) > 0:
        ax2.pie(split_values, labels=["Direct", "Indirect"], autopct="%1.1f%%")
    else:
        ax2.text(0.5, 0.5, "No data", ha="center", va="center")
    ax2.set_title("Direct vs indirect")

    ax3 = fig.add_subplot(2, 2, 3)
    if metrics["top_clients_labels"]:
        ax3.barh(metrics["top_clients_labels"][::-1], metrics["top_clients_values"][::-1])
    else:
        ax3.text(0.5, 0.5, "No data", ha="center", va="center")
    ax3.set_title("Top clients")
    ax3.set_xlabel("Hours")

    ax4 = fig.add_subplot(2, 2, 4)
    if metrics["weekday_labels"]:
        ax4.bar(metrics["weekday_labels"], metrics["weekday_values"])
    else:
        ax4.text(0.5, 0.5, "No data", ha="center", va="center")
    ax4.set_title("Hours by weekday")
    ax4.set_ylabel("Hours")

    summary = (
        f"Total: {format_hours_hm(metrics['total_hours'])} | "
        f"Direct: {format_hours_hm(metrics['direct_hours'])} | "
        f"Indirect: {format_hours_hm(metrics['indirect_hours'])} | "
        f"Active days: {metrics['active_days']} | "
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


def send_template_email(template_subject_key, template_body_key, to, context):
    context = {**context, "app_name": app_name(), "public_base_url": PUBLIC_BASE_URL}
    subject = render_text_template(get_setting(template_subject_key), context)
    body = render_text_template(get_setting(template_body_key), context)
    return send_email(to, subject, body)


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


def run_retention_tasks():
    db = get_db()
    users = db.execute("SELECT * FROM users WHERE role != 'admin'").fetchall()
    deleted_count = 0
    warned_count = 0

    for user in users:
        ref_dt = user_retention_reference(user)
        age_days = (utcnow() - ref_dt).days
        deletion_dt = ref_dt + timedelta(days=RETENTION_DAYS)
        warn_threshold = RETENTION_DAYS - RETENTION_WARNING_DAYS

        if age_days >= RETENTION_DAYS:
            db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
            deleted_count += 1
            continue

        if age_days >= warn_threshold and not user["deletion_reminder_sent_at"]:
            send_template_email(
                "template_retention_subject",
                "template_retention_body",
                user["email"],
                {
                    "email": user["email"],
                    "login_link": f"{PUBLIC_BASE_URL}{url_for('login')}",
                    "deletion_date": deletion_dt.date().isoformat(),
                },
            )
            db.execute(
                "UPDATE users SET deletion_reminder_sent_at = ? WHERE id = ?",
                (utcnow_iso(), user["id"]),
            )
            warned_count += 1

    db.commit()
    return {"warned": warned_count, "deleted": deleted_count}


@app.before_request
def load_globals():
    g.current_user = current_user()
    g.app_name = app_name()
    if request.endpoint != "static":
        run_retention_tasks_if_due()


@app.context_processor
def inject_globals():
    user = current_user()
    saved_theme = user["theme_pref"] if user else request.cookies.get("theme_pref", "auto")
    if saved_theme not in ("auto", "dark", "light"):
        saved_theme = "auto"
    return {
        "config_admin_user": DEFAULT_ADMIN_USER,
        "format_hours_hm": format_hours_hm,
        "minutes_from_hours": minutes_from_hours,
        "resolved_theme": saved_theme,
        "app_name_value": app_name(),
        "self_registration_enabled": get_setting("self_registration_enabled").lower() == "true",
    }


@app.route("/theme", methods=["POST"])
def set_theme():
    theme = request.form.get("theme", "auto")
    next_url = request.form.get("next") or url_for("index")
    if theme not in ("auto", "dark", "light"):
        theme = "auto"
    user = current_user()
    if user:
        get_db().execute("UPDATE users SET theme_pref = ? WHERE id = ?", (theme, user["id"]))
        get_db().commit()
    resp = make_response(redirect(next_url))
    resp.set_cookie("theme_pref", theme, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username_or_email = request.form.get("username", "").strip()
        username_or_email_normalized = normalize_email(username_or_email) if "@" in username_or_email else username_or_email
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (username_or_email, username_or_email_normalized),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if not user["email_confirmed"]:
                flash("Please confirm your email before logging in.", "warning")
                return render_template("login.html")
            session.clear()
            session["user_id"] = user["id"]
            get_db().execute(
                "UPDATE users SET last_login = ?, deletion_reminder_sent_at = NULL WHERE id = ?",
                (utcnow_iso(), user["id"]),
            )
            get_db().commit()
            return redirect(request.args.get("next") or url_for("index"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        user = get_db().execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if user:
            raw_token, expires_at = create_token("password_reset", user_id=user["id"], email=user["email"], expires_hours=2)
            send_template_email(
                "template_reset_subject",
                "template_reset_body",
                user["email"],
                {
                    "email": user["email"],
                    "reset_link": f"{PUBLIC_BASE_URL}{url_for('reset_password', token=raw_token)}",
                    "expires_at": expires_at,
                },
            )
        flash("If that email exists, a reset link has been sent.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token", "")
    token_row = fetch_token(token, "password_reset")
    if not token_row:
        flash("This password reset link is invalid or expired.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("reset_password.html")
        if password != password2:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html")

        get_db().execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), token_row["user_id"]),
        )
        mark_token_used(token_row["id"])
        get_db().commit()
        flash("Password updated. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html")





@app.route("/api/check-email")
def api_check_email():
    email = normalize_email(request.args.get("email", ""))
    available = bool(email) and ("@" in email) and not get_db().execute(
        "SELECT id FROM users WHERE lower(email) = ?",
        (email,),
    ).fetchone()
    return {
        "email": email,
        "available": available,
        "exists": not available if email and "@" in email else False,
        "forgot_password_url": url_for("forgot_password"),
    }


@app.route("/api/check-username")
def api_check_username():
    username = request.args.get("username", "").strip()
    exclude_user_id = request.args.get("exclude_user_id")
    exclude_id = int(exclude_user_id) if exclude_user_id and exclude_user_id.isdigit() else None
    available = bool(username) and not username_exists(username, exclude_id)
    suggestion = None if available else suggest_username(username)
    return {
        "username": username,
        "available": available,
        "suggestion": suggestion,
    }


@app.route("/register", methods=["GET", "POST"])
def self_register():
    enabled = get_setting("self_registration_enabled").lower() == "true"
    if not enabled:
        flash("Self registration is currently disabled.", "warning")
        return redirect(url_for("login"))

    if current_user():
        return redirect(url_for("index"))

    form_data = {
        "username": request.form.get("username", "").strip(),
        "email": normalize_email(request.form.get("email", "")),
        "theme_pref": request.form.get("theme_pref", "auto"),
    }

    if request.method == "POST":
        username = form_data["username"]
        email = form_data["email"]
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        theme_pref = form_data["theme_pref"]

        if not username:
            flash("Username is required.", "danger")
            return render_template("self_register.html", form_data=form_data)
        if not email or "@" not in email:
            flash("A valid email address is required.", "danger")
            return render_template("self_register.html", form_data=form_data)
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("self_register.html", form_data=form_data)
        if password != password2:
            flash("Passwords do not match.", "danger")
            return render_template("self_register.html", form_data=form_data)
        if username_exists(username):
            flash(f"Username already exists. Suggested alternative: {suggest_username(username)}", "danger")
            return render_template("self_register.html", form_data=form_data)
        if get_db().execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone():
            flash("Email already exists.", "danger")
            return render_template("self_register.html", form_data=form_data)

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO users
            (username, email, password_hash, role, email_confirmed, theme_pref, created_at)
            VALUES (?, ?, ?, 'user', 0, ?, ?)
            """,
            (
                username,
                email,
                generate_password_hash(password),
                theme_pref if theme_pref in ("auto", "dark", "light") else "auto",
                utcnow_iso(),
            ),
        )
        user_id = cursor.lastrowid
        db.commit()

        raw_token, expires_at = create_token("email_confirm", user_id=user_id, email=email, expires_hours=48)
        send_template_email(
            "template_confirm_subject",
            "template_confirm_body",
            email,
            {
                "email": email,
                "confirm_link": f"{PUBLIC_BASE_URL}{url_for('confirm_email', token=raw_token)}",
                "expires_at": expires_at,
            },
        )
        flash("Registration complete. Please confirm your email address before logging in.", "success")
        return redirect(url_for("login"))

    return render_template("self_register.html", form_data=form_data)


@app.route("/invite/accept", methods=["GET", "POST"])
def accept_invite():
    token = request.args.get("token", "")
    token_row = fetch_token(token, "invite")
    if not token_row:
        flash("This invite link is invalid or expired.", "danger")
        return redirect(url_for("login"))

    payload = json.loads(token_row["payload_json"] or "{}")
    invite_email = normalize_email((token_row["email"] or payload.get("email") or ""))

    form_data = {
        "username": request.form.get("username", "").strip(),
        "email": normalize_email(request.form.get("email", invite_email)),
        "theme_pref": request.form.get("theme_pref", "auto"),
    }

    if request.method == "POST":
        username = form_data["username"]
        email = form_data["email"]
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        theme_pref = form_data["theme_pref"]

        if email != invite_email:
            flash("Registration email must match the invited email address.", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)
        if not username:
            flash("Username is required.", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)
        if password != password2:
            flash("Passwords do not match.", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)
        if username_exists(username):
            flash(f"Username already exists. Suggested alternative: {suggest_username(username)}", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)
        if get_db().execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone():
            flash("Email already exists.", "danger")
            return render_template("register.html", invite_email=invite_email, form_data=form_data)

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO users
            (username, email, password_hash, role, email_confirmed, theme_pref, created_at)
            VALUES (?, ?, ?, 'user', 0, ?, ?)
            """,
            (
                username,
                email,
                generate_password_hash(password),
                theme_pref if theme_pref in ("auto", "dark", "light") else "auto",
                utcnow_iso(),
            ),
        )
        user_id = cursor.lastrowid
        mark_token_used(token_row["id"])
        db.commit()

        raw_token, expires_at = create_token("email_confirm", user_id=user_id, email=email, expires_hours=48)
        send_template_email(
            "template_confirm_subject",
            "template_confirm_body",
            email,
            {
                "email": email,
                "confirm_link": f"{PUBLIC_BASE_URL}{url_for('confirm_email', token=raw_token)}",
                "expires_at": expires_at,
            },
        )
        flash("Registration complete. Please confirm your email address before logging in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", invite_email=invite_email, form_data=form_data)


@app.route("/confirm-email")
def confirm_email():
    token = request.args.get("token", "")
    token_row = fetch_token(token, "email_confirm")
    if not token_row:
        flash("This confirmation link is invalid or expired.", "danger")
        return redirect(url_for("login"))

    get_db().execute("UPDATE users SET email_confirmed = 1 WHERE id = ?", (token_row["user_id"],))
    mark_token_used(token_row["id"])
    get_db().commit()
    flash("Email confirmed. You can now log in.", "success")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    db = get_db()
    me = current_user()

    if request.method == "POST":
        target_user_id = request.form.get("user_id") or str(me["id"])
        if me["role"] != "admin":
            target_user_id = str(me["id"])

        try:
            work_date = request.form["work_date"]
            hours = hours_from_minutes_input(request.form.get("minutes"))
            client = normalize_client(request.form.get("client", ""))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("index"))

        if entry_exists(target_user_id, work_date, hours, client):
            flash("Duplicate entry skipped. An entry with the same date, hours, and client already exists for this user.", "warning")
            return redirect(url_for("index"))

        db.execute(
            """
            INSERT INTO entries (user_id, work_date, hours, client, project, activity, hour_type, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(target_user_id),
                work_date,
                hours,
                client,
                request.form.get("project", "").strip(),
                request.form.get("activity", "").strip(),
                request.form.get("hour_type", "direct"),
                request.form.get("notes", "").strip(),
                utcnow_iso(),
            ),
        )
        db.commit()
        flash("Entry added.", "success")
        return redirect(url_for("index"))

    entries, users, date_from, date_to, client, project, selected_user = build_entry_query()
    total_hours = sum(float(row["hours"]) for row in entries)
    direct_hours = sum(float(row["hours"]) for row in entries if row["hour_type"] == "direct")
    indirect_hours = sum(float(row["hours"]) for row in entries if row["hour_type"] == "indirect")

    return render_template(
        "index.html",
        entries=entries,
        users=users,
        me=me,
        total_hours=total_hours,
        direct_hours=direct_hours,
        indirect_hours=indirect_hours,
        date_from=date_from,
        date_to=date_to,
        client=client,
        project=project,
        selected_user=selected_user,
    )


@app.route("/edit/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit(entry_id):
    db = get_db()
    me = current_user()
    entry = db.execute(
        """
        SELECT entries.*, users.username
        FROM entries JOIN users ON users.id = entries.user_id
        WHERE entries.id = ?
        """,
        (entry_id,),
    ).fetchone()
    if not entry:
        abort(404)
    if me["role"] != "admin" and entry["user_id"] != me["id"]:
        abort(403)

    users = db.execute("SELECT id, username FROM users ORDER BY username").fetchall()

    if request.method == "POST":
        target_user_id = request.form.get("user_id") or str(entry["user_id"])
        if me["role"] != "admin":
            target_user_id = str(me["id"])

        try:
            work_date = request.form["work_date"]
            hours = hours_from_minutes_input(request.form.get("minutes"))
            client = normalize_client(request.form.get("client", ""))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("edit", entry_id=entry_id))

        if entry_exists(target_user_id, work_date, hours, client, exclude_id=entry_id):
            flash("Duplicate entry skipped. Another entry with the same date, hours, and client already exists for this user.", "warning")
            return redirect(url_for("edit", entry_id=entry_id))

        db.execute(
            """
            UPDATE entries
            SET user_id = ?, work_date = ?, hours = ?, client = ?, project = ?, activity = ?, hour_type = ?, notes = ?
            WHERE id = ?
            """,
            (
                int(target_user_id),
                work_date,
                hours,
                client,
                request.form.get("project", "").strip(),
                request.form.get("activity", "").strip(),
                request.form.get("hour_type", "direct"),
                request.form.get("notes", "").strip(),
                entry_id,
            ),
        )
        db.commit()
        flash("Entry updated.", "success")
        return redirect(url_for("index"))

    return render_template("edit.html", entry=entry, users=users, me=me)


@app.route("/delete/<int:entry_id>", methods=["POST"])
@login_required
def delete_entry(entry_id):
    db = get_db()
    me = current_user()
    entry = db.execute("SELECT id, user_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        abort(404)
    if me["role"] != "admin" and entry["user_id"] != me["id"]:
        abort(403)
    db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    db.commit()
    flash("Entry removed.", "success")
    return redirect(url_for("index"))


@app.route("/import/csv", methods=["GET", "POST"])
@login_required
def import_csv():
    me = current_user()
    users = get_db().execute("SELECT id, username FROM users ORDER BY username").fetchall()

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a CSV file to import.", "danger")
            return render_template("import.html", me=me, users=users)

        try:
            parsed_rows = parse_import_file(file)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("import.html", me=me, users=users)

        db = get_db()
        inserted = 0
        skipped = 0
        resolved_users = {}
        target_user_id = request.form.get("user_id") or str(me["id"])
        if me["role"] != "admin":
            target_user_id = str(me["id"])

        for row in parsed_rows:
            row_user_id = int(target_user_id)
            if me["role"] == "admin" and row["username"]:
                if row["username"] not in resolved_users:
                    found = db.execute("SELECT id FROM users WHERE username = ?", (row["username"],)).fetchone()
                    resolved_users[row["username"]] = found["id"] if found else None
                if resolved_users[row["username"]] is None:
                    flash(f"Unknown user in CSV: {row['username']} (row {row['line']}). Import stopped.", "danger")
                    return render_template("import.html", me=me, users=users)
                row_user_id = resolved_users[row["username"]]

            if entry_exists(row_user_id, row["work_date"], row["hours"], row["client"]):
                skipped += 1
                continue

            db.execute(
                """
                INSERT INTO entries (user_id, work_date, hours, client, project, activity, hour_type, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_user_id,
                    row["work_date"],
                    row["hours"],
                    row["client"],
                    row["project"],
                    row["activity"],
                    row["hour_type"],
                    row["notes"],
                    utcnow_iso(),
                ),
            )
            inserted += 1

        db.commit()
        flash(f"Import complete: {inserted} inserted, {skipped} duplicate(s) skipped.", "success")
        return redirect(url_for("index"))

    return render_template("import.html", me=me, users=users)


@app.route("/dashboard")
@login_required
def dashboard():
    context = get_filter_context()
    metrics = compute_dashboard_metrics(context["rows"])
    return render_template("dashboard.html", metrics=metrics, **context)


@app.route("/dashboard/export.png")
@login_required
def export_dashboard_png():
    context = get_filter_context()
    metrics = compute_dashboard_metrics(context["rows"])
    title_parts = [f"{app_name()} dashboard"]
    if context["me"]["role"] == "admin" and context["selected_user"]:
        selected = next((u["username"] for u in context["users"] if str(u["id"]) == str(context["selected_user"])), None)
        if selected:
            title_parts.append(f"user: {selected}")
    if context["date_from"] or context["date_to"]:
        title_parts.append(f"period: {context['date_from'] or '...'} to {context['date_to'] or '...'}")
    if context["client"]:
        title_parts.append(f"client: {context['client']}")
    if context["project"]:
        title_parts.append(f"project: {context['project']}")
    stream = build_dashboard_png(metrics, " | ".join(title_parts))
    return send_file(stream, mimetype="image/png", as_attachment=True, download_name="hours_dashboard.png")


@app.route("/export/csv")
@login_required
def export_csv():
    rows = build_entry_query(for_export=True)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user", "date", "minutes", "hours", "duration", "client", "project", "activity", "type", "notes"])
    for row in rows:
        writer.writerow([
            row["id"],
            row["username"],
            row["work_date"],
            minutes_from_hours(row["hours"]),
            row["hours"],
            format_hours_hm(row["hours"]),
            row["client"],
            row["project"],
            row["activity"],
            row["hour_type"],
            row["notes"],
        ])
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="hours_export.csv")


@app.route("/export/xlsx")
@login_required
def export_xlsx():
    rows = build_entry_query(for_export=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Hours"
    headers = ["ID", "User", "Date", "Minutes", "Hours", "Duration", "Client", "Project", "Activity", "Type", "Notes"]
    ws.append(headers)
    for row in rows:
        ws.append([
            row["id"],
            row["username"],
            row["work_date"],
            minutes_from_hours(row["hours"]),
            row["hours"],
            format_hours_hm(row["hours"]),
            row["client"],
            row["project"],
            row["activity"],
            row["hour_type"],
            row["notes"],
        ])
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 40)
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="hours_export.xlsx",
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    me = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "theme":
            theme_pref = request.form.get("theme_pref", "auto")
            if theme_pref not in ("auto", "dark", "light"):
                theme_pref = "auto"
            get_db().execute("UPDATE users SET theme_pref = ? WHERE id = ?", (theme_pref, me["id"]))
            get_db().commit()
            flash("Theme preference updated.", "success")
        elif action == "password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            new_password2 = request.form.get("new_password2", "")
            if not check_password_hash(me["password_hash"], current_password):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters.", "danger")
            elif new_password != new_password2:
                flash("New passwords do not match.", "danger")
            else:
                get_db().execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), me["id"]))
                get_db().commit()
                flash("Password updated.", "success")
        elif action == "email":
            new_email = normalize_email(request.form.get("new_email", ""))
            current_password = request.form.get("current_password_for_email", "")
            if not check_password_hash(me["password_hash"], current_password):
                flash("Current password is incorrect.", "danger")
            elif not new_email or "@" not in new_email:
                flash("Please provide a valid email address.", "danger")
            elif new_email == me["email"]:
                flash("That is already your current email address.", "warning")
            elif get_db().execute("SELECT id FROM users WHERE lower(email) = ? AND id != ?", (new_email, me["id"])).fetchone():
                flash("Another user already uses this email address.", "danger")
            else:
                raw_token, expires_at = create_token("email_change", user_id=me["id"], email=new_email, payload={"new_email": new_email}, expires_hours=24)
                ok, err = send_template_email(
                    "template_confirm_subject",
                    "template_confirm_body",
                    new_email,
                    {
                        "email": new_email,
                        "confirm_link": f"{PUBLIC_BASE_URL}{url_for('confirm_email_change', token=raw_token)}",
                        "expires_at": expires_at,
                    },
                )
                if ok:
                    flash(f"Confirmation link sent to {new_email}. Your address will change after confirmation.", "success")
                else:
                    flash(f"Could not send confirmation email: {err}", "danger")
        return redirect(url_for("profile"))
    return render_template("profile.html", me=me)


@app.route("/confirm-email-change")
def confirm_email_change():
    token = request.args.get("token", "")
    token_row = fetch_token(token, "email_change")
    if not token_row:
        flash("This email change link is invalid or expired.", "danger")
        return redirect(url_for("login"))

    payload = json.loads(token_row["payload_json"] or "{}")
    new_email = normalize_email(payload.get("new_email") or token_row["email"] or "")
    if not new_email or "@" not in new_email:
        flash("This email change request is invalid.", "danger")
        return redirect(url_for("profile"))

    existing = get_db().execute(
        "SELECT id FROM users WHERE lower(email) = ? AND id != ?",
        (new_email, token_row["user_id"]),
    ).fetchone()
    if existing:
        flash("That email address is already in use.", "danger")
        return redirect(url_for("profile"))

    get_db().execute(
        "UPDATE users SET email = ?, email_confirmed = 1 WHERE id = ?",
        (new_email, token_row["user_id"]),
    )
    mark_token_used(token_row["id"])
    get_db().commit()
    flash("Email address confirmed and updated.", "success")
    return redirect(url_for("profile"))


@app.route("/profile/delete", methods=["POST"])
@login_required
def delete_self():
    me = current_user()
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (me["id"],))
    db.commit()
    session.clear()
    flash("Your account and related data have been removed.", "success")
    return redirect(url_for("login"))


@app.route("/users")
@login_required
@admin_required
def users():
    user_rows = get_db().execute(
        """
        SELECT id, username, email, role, email_confirmed, theme_pref, created_at, last_login
        FROM users
        ORDER BY role DESC, username
        """
    ).fetchall()
    return render_template("users.html", users=user_rows, me=current_user())


@app.route("/admin/invite", methods=["POST"])
@login_required
@admin_required
def admin_invite():
    email = normalize_email(request.form.get("email", ""))
    if not email or "@" not in email:
        flash("Please provide a valid email address.", "danger")
        return redirect(url_for("users"))
    if get_db().execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone():
        flash("A user with this email already exists.", "danger")
        return redirect(url_for("users"))

    raw_token, expires_at = create_token("invite", email=email, payload={"email": email}, expires_hours=7 * 24)
    ok, err = send_template_email(
        "template_invite_subject",
        "template_invite_body",
        email,
        {
            "email": email,
            "register_link": f"{PUBLIC_BASE_URL}{url_for('accept_invite', token=raw_token)}",
            "expires_at": expires_at,
        },
    )
    if ok:
        flash(f"Invite sent to {email}.", "success")
    else:
        flash(f"Invite token created, but email could not be sent: {err}", "warning")
    return redirect(url_for("users"))



@app.route("/admin/edit-user/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_edit_user(user_id):
    db = get_db()
    me = current_user()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = normalize_email(request.form.get("email", ""))
        role = request.form.get("role", user["role"]).strip().lower()
        email_confirmed = 1 if request.form.get("email_confirmed") == "1" else 0
        theme_pref = request.form.get("theme_pref", user["theme_pref"]).strip().lower()

        if not username:
            flash("Please provide a username.", "danger")
            return render_template("admin_edit_user.html", target_user=user, me=me, suggested_username=suggest_username(user["username"]))
        if username_exists(username, user_id):
            flash(f"Another user already uses this username. Suggested alternative: {suggest_username(username)}", "danger")
            return render_template("admin_edit_user.html", target_user=user, me=me, suggested_username=suggest_username(username))
        if not email or "@" not in email:
            flash("Please provide a valid email address.", "danger")
            return render_template("admin_edit_user.html", target_user=user, me=me, suggested_username=suggest_username(user["username"]))
        if role not in ("admin", "user"):
            role = user["role"]
        if theme_pref not in ("auto", "light", "dark"):
            theme_pref = user["theme_pref"]

        existing_email = db.execute(
            "SELECT id FROM users WHERE lower(email) = ? AND id != ?",
            (email, user_id),
        ).fetchone()
        if existing_email:
            flash("Another user already uses this email address.", "danger")
            return render_template("admin_edit_user.html", target_user=user, me=me, suggested_username=suggest_username(user["username"]))

        if user["username"] == DEFAULT_ADMIN_USER and role != "admin":
            flash("The bootstrap admin account must remain an admin.", "warning")
            role = "admin"

        db.execute(
            "UPDATE users SET username = ?, email = ?, email_confirmed = ?, role = ?, theme_pref = ? WHERE id = ?",
            (username, email, email_confirmed, role, theme_pref, user_id),
        )
        db.commit()
        flash("User updated.", "success")
        return redirect(url_for("users"))

    return render_template("admin_edit_user.html", target_user=user, me=me, suggested_username=suggest_username(user["username"]))


@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    me = current_user()
    if me["id"] == user_id:
        flash("Use your personal delete option from the profile page instead.", "warning")
        return redirect(url_for("users"))
    db = get_db()
    victim = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not victim:
        abort(404)
    if victim["role"] == "admin":
        flash("Admin users cannot be deleted from this screen.", "danger")
        return redirect(url_for("users"))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash("User and related entries removed.", "success")
    return redirect(url_for("users"))


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def admin_settings():
    keys = [
        "app_name",
        "self_registration_enabled",
        "template_invite_subject",
        "template_invite_body",
        "template_confirm_subject",
        "template_confirm_body",
        "template_reset_subject",
        "template_reset_body",
        "template_retention_subject",
        "template_retention_body",
    ]
    if request.method == "POST":
        for key in keys:
            set_setting(key, request.form.get(key, "").strip() or DEFAULT_SETTINGS.get(key, ""))
        get_db().commit()
        flash("Settings updated.", "success")
        return redirect(url_for("admin_settings"))

    settings = {key: get_setting(key) for key in keys}
    return render_template("admin_settings.html", settings=settings, public_base_url=PUBLIC_BASE_URL)


@app.route("/admin/run-retention", methods=["POST"])
@login_required
@admin_required
def admin_run_retention():
    result = run_retention_tasks() or {"warned": 0, "deleted": 0}
    set_setting("last_retention_run_at", utcnow_iso())
    get_db().commit()
    flash(f"Retention run completed: {result['warned']} warning(s), {result['deleted']} deletion(s).", "success")
    return redirect(url_for("admin_settings"))


@app.route("/policy")
def policy():
    return render_template(
        "policy.html",
        retention_days=RETENTION_DAYS,
        warning_days=RETENTION_WARNING_DAYS,
        public_base_url=PUBLIC_BASE_URL,
    )


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=False)
