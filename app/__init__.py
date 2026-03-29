
from flask import Flask, abort, current_app, g, request

from app.config import Config
from app.db import close_db, get_setting, init_db
from app.routes.admin import register_admin_routes
from app.routes.auth import register_auth_routes
from app.routes.entries import register_entry_routes
from app.security import csrf_token, init_limiter, validate_csrf
from app.services.core import app_name, current_user, format_hours_hm, minutes_from_hours, run_retention_tasks_if_due


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    init_limiter(app)
    app.before_request(validate_csrf)
    app.teardown_appcontext(close_db)

    @app.before_request
    def load_globals():
        g.current_user = current_user()
        g.app_name = app_name()
        if request.endpoint != 'static':
            run_retention_tasks_if_due()

    @app.context_processor
    def inject_globals():
        user = current_user()
        saved_theme = user['theme_pref'] if user else request.cookies.get('theme_pref', 'auto')
        if saved_theme not in ('auto', 'dark', 'light'):
            saved_theme = 'auto'
        return {
            'config_admin_user': current_app.config['DEFAULT_ADMIN_USER'],
            'format_hours_hm': format_hours_hm,
            'minutes_from_hours': minutes_from_hours,
            'resolved_theme': saved_theme,
            'app_name_value': app_name(),
            'self_registration_enabled': get_setting('self_registration_enabled').lower() == 'true',
            'csrf_token': csrf_token,
        }

    @app.after_request
    def set_security_headers(response):
        csp = "default-src 'self'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data:; font-src 'self' https://cdn.jsdelivr.net; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers.setdefault('Content-Security-Policy', csp)
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Cache-Control', 'no-store' if request.endpoint in {'login', 'forgot_password', 'reset_password'} else 'no-cache')
        return response

    @app.errorhandler(413)
    def too_large(_):
        return 'Upload too large.', 413

    init_db(app)
    register_auth_routes(app)
    register_entry_routes(app)
    register_admin_routes(app)
    return app
