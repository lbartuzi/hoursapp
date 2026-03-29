
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, storage_uri="memory://", default_limits=[])


def init_limiter(app):
    limiter.init_app(app)


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.endpoint == "static":
        return
    sent = request.form.get("_csrf_token") or request.headers.get("X-CSRFToken")
    if not sent or sent != session.get("_csrf_token"):
        abort(400, description="CSRF validation failed.")


def is_safe_redirect_target(target: str | None) -> bool:
    if not target:
        return False
    ref = urlsplit(request.host_url)
    test = urlsplit(target)
    return (not test.scheme and not test.netloc and target.startswith("/")) or (test.scheme in {"http", "https"} and test.netloc == ref.netloc)


def safe_redirect_target(target: str | None, default: str) -> str:
    return target if is_safe_redirect_target(target) else default
