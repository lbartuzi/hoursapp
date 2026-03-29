
from functools import wraps

from flask import abort, redirect, request, session, url_for

from app.services.core import current_user


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
