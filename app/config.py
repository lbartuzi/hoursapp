
import os


def as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    DB_PATH = os.environ.get("DB_PATH", "/app/data/hours.db")
    DEFAULT_ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")
    DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or DEFAULT_ADMIN_EMAIL)
    SMTP_USE_TLS = as_bool(os.environ.get("SMTP_USE_TLS", "true"), True)

    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "365"))
    RETENTION_WARNING_DAYS = int(os.environ.get("RETENTION_WARNING_DAYS", "28"))
    SELF_REGISTRATION_ENABLED = as_bool(os.environ.get("SELF_REGISTRATION_ENABLED", "false"), False)
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024)))

    CSRF_ENABLED = as_bool(os.environ.get("CSRF_ENABLED", "true"), True)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
