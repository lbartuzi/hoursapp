
import json

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import get_db, utcnow_iso
from app.security import limiter, safe_redirect_target
from app.services.core import (
    app_name,
    create_token,
    current_user,
    fetch_token,
    mark_token_used,
    normalize_email,
    send_template_email,
    suggest_username,
    username_exists,
)


def register_auth_routes(app):
    @app.route('/theme', methods=['POST'])
    def set_theme():
        theme = request.form.get('theme', 'auto')
        next_url = request.form.get('next') or url_for('index')
        if theme not in ('auto', 'dark', 'light'):
            theme = 'auto'
        user = current_user()
        if user:
            get_db().execute('UPDATE users SET theme_pref = ? WHERE id = ?', (theme, user['id']))
            get_db().commit()
            resp = redirect(safe_redirect_target(next_url, url_for('index')))
        else:
            resp = redirect(safe_redirect_target(next_url, url_for('login')))
        resp.set_cookie('theme_pref', theme, max_age=31536000, samesite='Lax', secure=request.is_secure)
        return resp

    @app.route('/login', methods=['GET', 'POST'])
    @limiter.limit('5 per minute', methods=['POST'])
    def login():
        if current_user():
            return redirect(url_for('index'))
        next_target = request.args.get('next') or request.form.get('next')
        if request.method == 'POST':
            username_or_email = request.form.get('username', '').strip()
            username_or_email_normalized = normalize_email(username_or_email)
            password = request.form.get('password', '')
            user = get_db().execute('SELECT * FROM users WHERE username = ? OR email = ?', (username_or_email, username_or_email_normalized)).fetchone()
            if user and check_password_hash(user['password_hash'], password):
                if not user['email_confirmed']:
                    flash('Please confirm your email before logging in.', 'warning')
                    return render_template('login.html', next_target=next_target)
                session.clear()
                session['user_id'] = user['id']
                get_db().execute('UPDATE users SET last_login = ?, deletion_reminder_sent_at = NULL WHERE id = ?', (utcnow_iso(), user['id']))
                get_db().commit()
                return redirect(safe_redirect_target(next_target, url_for('index')))
            flash('Invalid credentials.', 'danger')
        return render_template('login.html', next_target=next_target)

    @app.route('/logout', methods=['POST'])
    def logout():
        if not current_user():
            return redirect(url_for('login'))
        session.clear()
        return redirect(url_for('login'))

    @app.route('/forgot-password', methods=['GET', 'POST'])
    @limiter.limit('3 per hour', methods=['POST'])
    def forgot_password():
        if request.method == 'POST':
            email = normalize_email(request.form.get('email', ''))
            user = get_db().execute('SELECT * FROM users WHERE lower(email) = ?', (email,)).fetchone()
            if user:
                raw_token, expires_at = create_token('password_reset', user_id=user['id'], email=user['email'], expires_hours=2)
                send_template_email('template_reset_subject', 'template_reset_body', user['email'], {'email': user['email'], 'reset_link': f"{current_app.config['PUBLIC_BASE_URL']}{url_for('reset_password', token=raw_token)}", 'expires_at': expires_at})
            flash('If that email exists, a reset link has been sent.', 'success')
            return redirect(url_for('login'))
        return render_template('forgot_password.html')

    @app.route('/reset-password', methods=['GET', 'POST'])
    def reset_password():
        token = request.args.get('token', '')
        token_row = fetch_token(token, 'password_reset')
        if not token_row:
            flash('This password reset link is invalid or expired.', 'danger')
            return redirect(url_for('forgot_password'))
        if request.method == 'POST':
            password = request.form.get('password', '')
            password2 = request.form.get('password2', '')
            if len(password) < 8:
                flash('Password must be at least 8 characters.', 'danger')
                return render_template('reset_password.html')
            if password != password2:
                flash('Passwords do not match.', 'danger')
                return render_template('reset_password.html')
            get_db().execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(password), token_row['user_id']))
            mark_token_used(token_row['id'])
            get_db().commit()
            flash('Password updated. You can now log in.', 'success')
            return redirect(url_for('login'))
        return render_template('reset_password.html')

    @app.route('/api/check-email')
    @limiter.limit('20 per minute')
    def api_check_email():
        if not current_user() and get_db().execute("SELECT value FROM settings WHERE key = 'self_registration_enabled'").fetchone()['value'].lower() != 'true':
            abort(404)
        email = normalize_email(request.args.get('email', ''))
        available = bool(email) and ('@' in email) and not get_db().execute('SELECT id FROM users WHERE lower(email) = ?', (email,)).fetchone()
        return {'email': email, 'available': available, 'exists': (not available if email and '@' in email else False), 'forgot_password_url': url_for('forgot_password')}

    @app.route('/api/check-username')
    @limiter.limit('20 per minute')
    def api_check_username():
        if not current_user() and get_db().execute("SELECT value FROM settings WHERE key = 'self_registration_enabled'").fetchone()['value'].lower() != 'true':
            abort(404)
        username = request.args.get('username', '').strip()
        exclude_user_id = request.args.get('exclude_user_id')
        exclude_id = int(exclude_user_id) if exclude_user_id and exclude_user_id.isdigit() else None
        available = bool(username) and not username_exists(username, exclude_id)
        suggestion = None if available else suggest_username(username)
        return {'username': username, 'available': available, 'suggestion': suggestion}

    @app.route('/register', methods=['GET', 'POST'])
    def self_register():
        enabled = get_db().execute("SELECT value FROM settings WHERE key = 'self_registration_enabled'").fetchone()['value'].lower() == 'true'
        if not enabled:
            flash('Self registration is currently disabled.', 'warning')
            return redirect(url_for('login'))
        if current_user():
            return redirect(url_for('index'))
        form_data = {'username': request.form.get('username', '').strip(), 'email': normalize_email(request.form.get('email', '')), 'theme_pref': request.form.get('theme_pref', 'auto')}
        if request.method == 'POST':
            username = form_data['username']
            email = form_data['email']
            password = request.form.get('password', '')
            password2 = request.form.get('password2', '')
            theme_pref = form_data['theme_pref']
            if not username:
                flash('Username is required.', 'danger'); return render_template('self_register.html', form_data=form_data)
            if not email or '@' not in email:
                flash('A valid email address is required.', 'danger'); return render_template('self_register.html', form_data=form_data)
            if len(password) < 8:
                flash('Password must be at least 8 characters.', 'danger'); return render_template('self_register.html', form_data=form_data)
            if password != password2:
                flash('Passwords do not match.', 'danger'); return render_template('self_register.html', form_data=form_data)
            if username_exists(username):
                flash(f'Username already exists. Suggested alternative: {suggest_username(username)}', 'danger'); return render_template('self_register.html', form_data=form_data)
            if get_db().execute('SELECT id FROM users WHERE lower(email) = ?', (email,)).fetchone():
                flash('Email already exists.', 'danger'); return render_template('self_register.html', form_data=form_data)
            cursor = get_db().execute("""INSERT INTO users (username, email, password_hash, role, email_confirmed, theme_pref, created_at) VALUES (?, ?, ?, 'user', 0, ?, ?)""", (username, email, generate_password_hash(password), theme_pref if theme_pref in ('auto', 'dark', 'light') else 'auto', utcnow_iso()))
            user_id = cursor.lastrowid
            get_db().commit()
            raw_token, expires_at = create_token('email_confirm', user_id=user_id, email=email, expires_hours=48)
            send_template_email('template_confirm_subject', 'template_confirm_body', email, {'email': email, 'confirm_link': f"{current_app.config['PUBLIC_BASE_URL']}{url_for('confirm_email', token=raw_token)}", 'expires_at': expires_at})
            flash('Registration complete. Please confirm your email address before logging in.', 'success')
            return redirect(url_for('login'))
        return render_template('self_register.html', form_data=form_data)

    @app.route('/invite/accept', methods=['GET', 'POST'])
    def accept_invite():
        token = request.args.get('token', '')
        token_row = fetch_token(token, 'invite')
        if not token_row:
            flash('This invite link is invalid or expired.', 'danger')
            return redirect(url_for('login'))
        payload = json.loads(token_row['payload_json'] or '{}')
        invite_email = normalize_email((token_row['email'] or payload.get('email') or ''))
        form_data = {'username': request.form.get('username', '').strip(), 'email': normalize_email(request.form.get('email', invite_email)), 'theme_pref': request.form.get('theme_pref', 'auto')}
        if request.method == 'POST':
            username = form_data['username']; email = form_data['email']; password = request.form.get('password', ''); password2 = request.form.get('password2', ''); theme_pref = form_data['theme_pref']
            if email != invite_email:
                flash('Registration email must match the invited email address.', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            if not username:
                flash('Username is required.', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            if len(password) < 8:
                flash('Password must be at least 8 characters.', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            if password != password2:
                flash('Passwords do not match.', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            if username_exists(username):
                flash(f'Username already exists. Suggested alternative: {suggest_username(username)}', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            if get_db().execute('SELECT id FROM users WHERE lower(email) = ?', (email,)).fetchone():
                flash('Email already exists.', 'danger'); return render_template('register.html', invite_email=invite_email, form_data=form_data)
            cursor = get_db().execute("""INSERT INTO users (username, email, password_hash, role, email_confirmed, theme_pref, created_at) VALUES (?, ?, ?, 'user', 0, ?, ?)""", (username, email, generate_password_hash(password), theme_pref if theme_pref in ('auto', 'dark', 'light') else 'auto', utcnow_iso()))
            mark_token_used(token_row['id'])
            get_db().commit()
            raw_token, expires_at = create_token('email_confirm', user_id=cursor.lastrowid, email=email, expires_hours=48)
            send_template_email('template_confirm_subject', 'template_confirm_body', email, {'email': email, 'confirm_link': f"{current_app.config['PUBLIC_BASE_URL']}{url_for('confirm_email', token=raw_token)}", 'expires_at': expires_at})
            flash('Registration complete. Please confirm your email address before logging in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html', invite_email=invite_email, form_data=form_data)

    @app.route('/confirm-email')
    def confirm_email():
        token = request.args.get('token', '')
        token_row = fetch_token(token, 'email_confirm')
        if not token_row:
            flash('This confirmation link is invalid or expired.', 'danger')
            return redirect(url_for('login'))
        get_db().execute('UPDATE users SET email_confirmed = 1 WHERE id = ?', (token_row['user_id'],))
        mark_token_used(token_row['id'])
        get_db().commit()
        flash('Email confirmed. You can now log in.', 'success')
        return redirect(url_for('login'))

    @app.route('/confirm-email-change')
    def confirm_email_change():
        token = request.args.get('token', '')
        token_row = fetch_token(token, 'email_change')
        if not token_row:
            flash('This email change link is invalid or expired.', 'danger')
            return redirect(url_for('login'))
        payload = json.loads(token_row['payload_json'] or '{}')
        new_email = normalize_email(payload.get('new_email') or token_row['email'] or '')
        if not new_email or '@' not in new_email:
            flash('This email change request is invalid.', 'danger')
            return redirect(url_for('profile'))
        existing = get_db().execute('SELECT id FROM users WHERE lower(email) = ? AND id != ?', (new_email, token_row['user_id'])).fetchone()
        if existing:
            flash('That email address is already in use.', 'danger')
            return redirect(url_for('profile'))
        get_db().execute('UPDATE users SET email = ?, email_confirmed = 1 WHERE id = ?', (new_email, token_row['user_id']))
        mark_token_used(token_row['id'])
        get_db().commit()
        flash('Email address confirmed and updated.', 'success')
        return redirect(url_for('profile'))
