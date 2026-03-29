
import os
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g
from werkzeug.security import generate_password_hash


DEFAULT_SETTINGS = {
    "app_name": "Hours Admin",
    "template_invite_subject": "You're invited to {{app_name}}",
    "template_invite_body": (
        "Hello,\n\n"
        "You have been invited to create an account on {{app_name}}.\n\n"
        "Complete your registration here:\n{{register_link}}\n\n"
        "This invite expires on {{expires_at}}.\n"
    ),
    "self_registration_enabled": "false",
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
    return datetime.now(timezone.utc)


def utcnow_iso():
    return utcnow().replace(microsecond=0).isoformat()


def parse_iso(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


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


def init_db(app):
    db_path = app.config["DB_PATH"]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = sqlite3.connect(db_path)
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

    existing = db.execute("SELECT id FROM users WHERE username = ?", (app.config["DEFAULT_ADMIN_USER"],)).fetchone()
    if not existing:
        db.execute(
            """
            INSERT INTO users
            (username, email, password_hash, role, email_confirmed, theme_pref, created_at, last_login)
            VALUES (?, ?, ?, 'admin', 1, 'auto', ?, ?)
            """,
            (
                app.config["DEFAULT_ADMIN_USER"],
                app.config["DEFAULT_ADMIN_EMAIL"],
                generate_password_hash(app.config["DEFAULT_ADMIN_PASSWORD"]),
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

    admin_id = db.execute("SELECT id FROM users WHERE username = ?", (app.config["DEFAULT_ADMIN_USER"],)).fetchone()["id"]

    db.execute("UPDATE users SET email = COALESCE(email, username || '@local.invalid') WHERE email IS NULL OR email = ''")
    db.execute("UPDATE users SET role = 'admin' WHERE username = ?", (app.config["DEFAULT_ADMIN_USER"],))
    db.execute("UPDATE users SET email_confirmed = 1 WHERE username = ?", (app.config["DEFAULT_ADMIN_USER"],))
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
        if key == "self_registration_enabled":
            value = "true" if app.config["SELF_REGISTRATION_ENABLED"] else "false"
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
