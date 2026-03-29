
import os
import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    db_path = tmp_path / 'test_hours.db'
    monkeypatch.setenv('DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'adminpass123')
    monkeypatch.setenv('ADMIN_EMAIL', 'admin@example.com')
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key')
    monkeypatch.setenv('SELF_REGISTRATION_ENABLED', 'true')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://localhost')
    from app import create_app
    app = create_app({'TESTING': True, 'CSRF_ENABLED': False})
    sent_emails = []
    def fake_send_email(to, subject, body):
        sent_emails.append({'to': to, 'subject': subject, 'body': body})
        return True, None
    import app.services.core as core
    monkeypatch.setattr(core, 'send_email', fake_send_email)
    app.sent_emails = sent_emails
    yield app


@pytest.fixture()
def client(app_module):
    return app_module.test_client()


@pytest.fixture()
def db_conn(app_module):
    conn = sqlite3.connect(app_module.config['DB_PATH'])
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def login(client, username='admin', password='adminpass123', follow_redirects=True):
    return client.post('/login', data={'username': username, 'password': password}, follow_redirects=follow_redirects)


@pytest.fixture()
def login_admin(client):
    def _login_admin(**kwargs):
        return login(client, **kwargs)
    return _login_admin


@pytest.fixture()
def create_confirmed_user(db_conn):
    def _create_user(username='user1', email='user1@example.com', password='Password123!', role='user', confirmed=1):
        db_conn.execute("INSERT INTO users (username, email, password_hash, role, email_confirmed, theme_pref, created_at, last_login) VALUES (?, ?, ?, ?, ?, 'auto', datetime('now'), datetime('now'))", (username, email, generate_password_hash(password), role, confirmed))
        db_conn.commit()
        return db_conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    return _create_user
