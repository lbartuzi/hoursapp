
from flask import abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import get_db, utcnow_iso
from app.decorators import login_required
from app.services.core import (
    app_name,
    build_dashboard_png,
    build_entry_query,
    compute_dashboard_metrics,
    create_token,
    current_user,
    entry_exists,
    export_csv_response,
    export_xlsx_response,
    format_hours_hm,
    get_filter_context,
    hours_from_minutes_input,
    minutes_from_hours,
    normalize_client,
    normalize_email,
    parse_import_file,
    send_template_email,
)


def register_entry_routes(app):
    @app.route('/', methods=['GET', 'POST'])
    @login_required
    def index():
        db = get_db(); me = current_user()
        if request.method == 'POST':
            target_user_id = request.form.get('user_id') or str(me['id'])
            if me['role'] != 'admin':
                target_user_id = str(me['id'])
            try:
                work_date = request.form['work_date']
                hours = hours_from_minutes_input(request.form.get('minutes'))
                client = normalize_client(request.form.get('client', ''))
            except ValueError as exc:
                flash(str(exc), 'danger'); return redirect(url_for('index'))
            if entry_exists(target_user_id, work_date, hours, client):
                flash('Duplicate entry skipped. An entry with the same date, hours, and client already exists for this user.', 'warning'); return redirect(url_for('index'))
            db.execute("""INSERT INTO entries (user_id, work_date, hours, client, project, activity, hour_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (int(target_user_id), work_date, hours, client, request.form.get('project', '').strip(), request.form.get('activity', '').strip(), request.form.get('hour_type', 'direct'), request.form.get('notes', '').strip(), utcnow_iso()))
            db.commit(); flash('Entry added.', 'success'); return redirect(url_for('index'))
        entries, users, date_from, date_to, client, project, selected_user = build_entry_query()
        total_hours = sum(float(row['hours']) for row in entries)
        direct_hours = sum(float(row['hours']) for row in entries if row['hour_type'] == 'direct')
        indirect_hours = sum(float(row['hours']) for row in entries if row['hour_type'] == 'indirect')
        return render_template('index.html', entries=entries, users=users, me=me, total_hours=total_hours, direct_hours=direct_hours, indirect_hours=indirect_hours, date_from=date_from, date_to=date_to, client=client, project=project, selected_user=selected_user)

    @app.route('/edit/<int:entry_id>', methods=['GET', 'POST'])
    @login_required
    def edit(entry_id):
        db = get_db(); me = current_user()
        entry = db.execute("SELECT entries.*, users.username FROM entries JOIN users ON users.id = entries.user_id WHERE entries.id = ?", (entry_id,)).fetchone()
        if not entry: abort(404)
        if me['role'] != 'admin' and entry['user_id'] != me['id']: abort(403)
        users = db.execute('SELECT id, username FROM users ORDER BY username').fetchall()
        if request.method == 'POST':
            target_user_id = request.form.get('user_id') or str(entry['user_id'])
            if me['role'] != 'admin': target_user_id = str(me['id'])
            try:
                work_date = request.form['work_date']; hours = hours_from_minutes_input(request.form.get('minutes')); client = normalize_client(request.form.get('client', ''))
            except ValueError as exc:
                flash(str(exc), 'danger'); return redirect(url_for('edit', entry_id=entry_id))
            if entry_exists(target_user_id, work_date, hours, client, exclude_id=entry_id):
                flash('Duplicate entry skipped. Another entry with the same date, hours, and client already exists for this user.', 'warning'); return redirect(url_for('edit', entry_id=entry_id))
            db.execute("""UPDATE entries SET user_id = ?, work_date = ?, hours = ?, client = ?, project = ?, activity = ?, hour_type = ?, notes = ? WHERE id = ?""", (int(target_user_id), work_date, hours, client, request.form.get('project', '').strip(), request.form.get('activity', '').strip(), request.form.get('hour_type', 'direct'), request.form.get('notes', '').strip(), entry_id))
            db.commit(); flash('Entry updated.', 'success'); return redirect(url_for('index'))
        return render_template('edit.html', entry=entry, users=users, me=me)

    @app.route('/delete/<int:entry_id>', methods=['POST'])
    @login_required
    def delete_entry(entry_id):
        db = get_db(); me = current_user(); entry = db.execute('SELECT id, user_id FROM entries WHERE id = ?', (entry_id,)).fetchone()
        if not entry: abort(404)
        if me['role'] != 'admin' and entry['user_id'] != me['id']: abort(403)
        db.execute('DELETE FROM entries WHERE id = ?', (entry_id,)); db.commit(); flash('Entry removed.', 'success'); return redirect(url_for('index'))

    @app.route('/import/csv', methods=['GET', 'POST'])
    @login_required
    def import_csv():
        me = current_user(); users = get_db().execute('SELECT id, username FROM users ORDER BY username').fetchall()
        if request.method == 'POST':
            file = request.files.get('file')
            if not file or not file.filename:
                flash('Please choose a CSV file to import.', 'danger'); return render_template('import.html', me=me, users=users)
            try:
                parsed_rows = parse_import_file(file)
            except ValueError as exc:
                flash(str(exc), 'danger'); return render_template('import.html', me=me, users=users)
            db = get_db(); inserted = 0; skipped = 0; resolved_users = {}
            target_user_id = request.form.get('user_id') or str(me['id'])
            if me['role'] != 'admin': target_user_id = str(me['id'])
            for row in parsed_rows:
                row_user_id = int(target_user_id)
                if me['role'] == 'admin' and row['username']:
                    if row['username'] not in resolved_users:
                        found = db.execute('SELECT id FROM users WHERE username = ?', (row['username'],)).fetchone()
                        resolved_users[row['username']] = found['id'] if found else None
                    if resolved_users[row['username']] is None:
                        flash(f"Unknown user in CSV: {row['username']} (row {row['line']}). Import stopped.", 'danger'); return render_template('import.html', me=me, users=users)
                    row_user_id = resolved_users[row['username']]
                if entry_exists(row_user_id, row['work_date'], row['hours'], row['client']):
                    skipped += 1; continue
                db.execute("""INSERT INTO entries (user_id, work_date, hours, client, project, activity, hour_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (row_user_id, row['work_date'], row['hours'], row['client'], row['project'], row['activity'], row['hour_type'], row['notes'], utcnow_iso())); inserted += 1
            db.commit(); flash(f'Import complete: {inserted} inserted, {skipped} duplicate(s) skipped.', 'success'); return redirect(url_for('index'))
        return render_template('import.html', me=me, users=users)

    @app.route('/dashboard')
    @login_required
    def dashboard():
        context = get_filter_context(); metrics = compute_dashboard_metrics(context['rows']); return render_template('dashboard.html', metrics=metrics, **context)

    @app.route('/dashboard/export.png')
    @login_required
    def export_dashboard_png():
        context = get_filter_context(); metrics = compute_dashboard_metrics(context['rows']); title_parts = [f"{app_name()} dashboard"]
        if context['me']['role'] == 'admin' and context['selected_user']:
            selected = next((u['username'] for u in context['users'] if str(u['id']) == str(context['selected_user'])), None)
            if selected: title_parts.append(f'user: {selected}')
        if context['date_from'] or context['date_to']: title_parts.append(f"period: {context['date_from'] or '...'} to {context['date_to'] or '...'}")
        if context['client']: title_parts.append(f"client: {context['client']}")
        if context['project']: title_parts.append(f"project: {context['project']}")
        stream = build_dashboard_png(metrics, ' | '.join(title_parts)); return send_file(stream, mimetype='image/png', as_attachment=True, download_name='hours_dashboard.png')

    @app.route('/export/csv')
    @login_required
    def export_csv():
        return export_csv_response()

    @app.route('/export/xlsx')
    @login_required
    def export_xlsx():
        return export_xlsx_response()

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        me = current_user()
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'theme':
                theme_pref = request.form.get('theme_pref', 'auto')
                if theme_pref not in ('auto', 'dark', 'light'): theme_pref = 'auto'
                get_db().execute('UPDATE users SET theme_pref = ? WHERE id = ?', (theme_pref, me['id'])); get_db().commit(); flash('Theme preference updated.', 'success')
            elif action == 'password':
                current_password = request.form.get('current_password', ''); new_password = request.form.get('new_password', ''); new_password2 = request.form.get('new_password2', '')
                if not check_password_hash(me['password_hash'], current_password): flash('Current password is incorrect.', 'danger')
                elif len(new_password) < 8: flash('New password must be at least 8 characters.', 'danger')
                elif new_password != new_password2: flash('New passwords do not match.', 'danger')
                else:
                    get_db().execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(new_password), me['id'])); get_db().commit(); flash('Password updated.', 'success')
            elif action == 'email':
                new_email = normalize_email(request.form.get('new_email', '')); current_password = request.form.get('current_password_for_email', '')
                if not check_password_hash(me['password_hash'], current_password): flash('Current password is incorrect.', 'danger')
                elif not new_email or '@' not in new_email: flash('Please provide a valid email address.', 'danger')
                elif new_email == me['email']: flash('That is already your current email address.', 'warning')
                elif get_db().execute('SELECT id FROM users WHERE lower(email) = ? AND id != ?', (new_email, me['id'])).fetchone(): flash('Another user already uses this email address.', 'danger')
                else:
                    raw_token, expires_at = create_token('email_change', user_id=me['id'], email=new_email, payload={'new_email': new_email}, expires_hours=24)
                    ok, err = send_template_email('template_confirm_subject', 'template_confirm_body', new_email, {'email': new_email, 'confirm_link': f"{current_app.config['PUBLIC_BASE_URL']}{url_for('confirm_email_change', token=raw_token)}", 'expires_at': expires_at})
                    if ok: flash(f'Confirmation link sent to {new_email}. Your address will change after confirmation.', 'success')
                    else: flash(f'Could not send confirmation email: {err}', 'danger')
            return redirect(url_for('profile'))
        return render_template('profile.html', me=me)

    @app.route('/profile/delete', methods=['POST'])
    @login_required
    def delete_self():
        me = current_user()
        if me['role'] == 'admin':
            flash('Admin accounts cannot self-delete from the profile page.', 'danger')
            return redirect(url_for('profile'))
        get_db().execute('DELETE FROM users WHERE id = ?', (me['id'],)); get_db().commit(); session.clear(); flash('Your account and related data have been removed.', 'success'); return redirect(url_for('login'))

    @app.route('/policy')
    def policy():
        return render_template('policy.html', retention_days=current_app.config['RETENTION_DAYS'], warning_days=current_app.config['RETENTION_WARNING_DAYS'], public_base_url=current_app.config['PUBLIC_BASE_URL'])
