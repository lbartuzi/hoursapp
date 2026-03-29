
from flask import abort, current_app, flash, redirect, render_template, request, url_for

from app.db import DEFAULT_SETTINGS, get_db, get_setting, set_setting, utcnow_iso
from app.decorators import admin_required, login_required
from app.services.core import create_token, current_user, normalize_email, run_retention_tasks, send_template_email, suggest_username, username_exists


def register_admin_routes(app):
    @app.route('/users')
    @login_required
    @admin_required
    def users():
        user_rows = get_db().execute("SELECT id, username, email, role, email_confirmed, theme_pref, created_at, last_login FROM users ORDER BY role DESC, username").fetchall()
        return render_template('users.html', users=user_rows, me=current_user())

    @app.route('/admin/invite', methods=['POST'])
    @login_required
    @admin_required
    def admin_invite():
        email = normalize_email(request.form.get('email', ''))
        if not email or '@' not in email:
            flash('Please provide a valid email address.', 'danger'); return redirect(url_for('users'))
        if get_db().execute('SELECT id FROM users WHERE lower(email) = ?', (email,)).fetchone():
            flash('A user with this email already exists.', 'danger'); return redirect(url_for('users'))
        raw_token, expires_at = create_token('invite', email=email, payload={'email': email}, expires_hours=7 * 24)
        ok, err = send_template_email('template_invite_subject', 'template_invite_body', email, {'email': email, 'register_link': f"{current_app.config['PUBLIC_BASE_URL']}{url_for('accept_invite', token=raw_token)}", 'expires_at': expires_at})
        if ok: flash(f'Invite sent to {email}.', 'success')
        else: flash(f'Invite token created, but email could not be sent: {err}', 'warning')
        return redirect(url_for('users'))

    @app.route('/admin/edit-user/<int:user_id>', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def admin_edit_user(user_id):
        db = get_db(); me = current_user(); user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user: abort(404)
        if request.method == 'POST':
            username = request.form.get('username', '').strip(); email = normalize_email(request.form.get('email', '')); role = request.form.get('role', user['role']).strip().lower(); email_confirmed = 1 if request.form.get('email_confirmed') == '1' else 0; theme_pref = request.form.get('theme_pref', user['theme_pref']).strip().lower()
            if not username: flash('Please provide a username.', 'danger'); return render_template('admin_edit_user.html', target_user=user, me=me, suggested_username=suggest_username(user['username']))
            if username_exists(username, user_id): flash(f'Another user already uses this username. Suggested alternative: {suggest_username(username)}', 'danger'); return render_template('admin_edit_user.html', target_user=user, me=me, suggested_username=suggest_username(username))
            if not email or '@' not in email: flash('Please provide a valid email address.', 'danger'); return render_template('admin_edit_user.html', target_user=user, me=me, suggested_username=suggest_username(user['username']))
            if role not in ('admin', 'user'): role = user['role']
            if theme_pref not in ('auto', 'light', 'dark'): theme_pref = user['theme_pref']
            existing_email = db.execute('SELECT id FROM users WHERE lower(email) = ? AND id != ?', (email, user_id)).fetchone()
            if existing_email: flash('Another user already uses this email address.', 'danger'); return render_template('admin_edit_user.html', target_user=user, me=me, suggested_username=suggest_username(user['username']))
            if user['username'] == current_app.config['DEFAULT_ADMIN_USER'] and role != 'admin':
                flash('The bootstrap admin account must remain an admin.', 'warning'); role = 'admin'
            db.execute('UPDATE users SET username = ?, email = ?, email_confirmed = ?, role = ?, theme_pref = ? WHERE id = ?', (username, email, email_confirmed, role, theme_pref, user_id)); db.commit(); flash('User updated.', 'success'); return redirect(url_for('users'))
        return render_template('admin_edit_user.html', target_user=user, me=me, suggested_username=suggest_username(user['username']))

    @app.route('/admin/delete-user/<int:user_id>', methods=['POST'])
    @login_required
    @admin_required
    def admin_delete_user(user_id):
        me = current_user()
        if me['id'] == user_id:
            flash('Use your personal delete option from the profile page instead.', 'warning'); return redirect(url_for('users'))
        db = get_db(); victim = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if not victim: abort(404)
        if victim['role'] == 'admin': flash('Admin users cannot be deleted from this screen.', 'danger'); return redirect(url_for('users'))
        db.execute('DELETE FROM users WHERE id = ?', (user_id,)); db.commit(); flash('User and related entries removed.', 'success'); return redirect(url_for('users'))

    @app.route('/admin/settings', methods=['GET', 'POST'])
    @login_required
    @admin_required
    def admin_settings():
        keys = ['app_name', 'self_registration_enabled', 'template_invite_subject', 'template_invite_body', 'template_confirm_subject', 'template_confirm_body', 'template_reset_subject', 'template_reset_body', 'template_retention_subject', 'template_retention_body']
        if request.method == 'POST':
            for key in keys:
                set_setting(key, request.form.get(key, '').strip() or DEFAULT_SETTINGS.get(key, ''))
            get_db().commit(); flash('Settings updated.', 'success'); return redirect(url_for('admin_settings'))
        settings = {key: get_setting(key) for key in keys}
        return render_template('admin_settings.html', settings=settings, public_base_url=current_app.config['PUBLIC_BASE_URL'])

    @app.route('/admin/run-retention', methods=['POST'])
    @login_required
    @admin_required
    def admin_run_retention():
        result = run_retention_tasks() or {'warned': 0, 'deleted': 0}
        set_setting('last_retention_run_at', utcnow_iso()); get_db().commit(); flash(f"Retention run completed: {result['warned']} warning(s), {result['deleted']} deletion(s).", 'success'); return redirect(url_for('admin_settings'))
