
import re
from io import BytesIO


def extract_token(link_body, path_fragment):
    match = re.search(rf"{re.escape(path_fragment)}\?token=([^\s]+)", link_body)
    assert match
    return match.group(1)


def test_register_page_loads(client):
    response = client.get('/register')
    assert response.status_code == 200
    assert b'Create account' in response.data


def test_open_redirect_blocked(client, create_confirmed_user):
    create_confirmed_user(username='openuser', email='open@example.com', password='StrongPass123!')
    response = client.post('/login?next=https://evil.example', data={'username': 'openuser', 'password': 'StrongPass123!'}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')


def test_self_registration_confirm_then_login(client, app_module):
    response = client.post('/register', data={'username': 'newuser', 'email': 'newuser@example.com', 'password': 'StrongPass123!', 'password2': 'StrongPass123!', 'theme_pref': 'dark'}, follow_redirects=True)
    assert b'Registration complete.' in response.data
    token = extract_token(app_module.sent_emails[0]['body'], '/confirm-email')
    client.get(f'/confirm-email?token={token}', follow_redirects=True)
    login_response = client.post('/login', data={'username': 'newuser', 'password': 'StrongPass123!'}, follow_redirects=True)
    assert b'Add entry' in login_response.data or b'Dashboard' in login_response.data


def test_invite_accept_flow(client, app_module, login_admin):
    login_admin()
    response = client.post('/admin/invite', data={'email': 'invitee@example.com'}, follow_redirects=True)
    assert b'Invite sent to invitee@example.com.' in response.data
    invite_token = extract_token(app_module.sent_emails[0]['body'], '/invite/accept')
    accept = client.post(f'/invite/accept?token={invite_token}', data={'username': 'invitee', 'email': 'invitee@example.com', 'password': 'InvitePass123!', 'password2': 'InvitePass123!', 'theme_pref': 'light'}, follow_redirects=True)
    assert b'Registration complete.' in accept.data


def test_password_reset_flow(client, app_module, create_confirmed_user):
    create_confirmed_user(username='resetuser', email='reset@example.com', password='OldPass123!')
    client.post('/forgot-password', data={'email': 'reset@example.com'}, follow_redirects=True)
    reset_token = extract_token(app_module.sent_emails[0]['body'], '/reset-password')
    response = client.post(f'/reset-password?token={reset_token}', data={'password': 'NewPass123!', 'password2': 'NewPass123!'}, follow_redirects=True)
    assert b'Password updated. You can now log in.' in response.data


def test_api_rate_limited_headers_present(client):
    response = client.get('/api/check-username?username=testuser')
    assert response.status_code == 200
    assert 'Content-Security-Policy' in response.headers
    assert 'X-Frame-Options' in response.headers


def test_csv_import_skips_duplicates(client, login_admin, db_conn):
    login_admin()
    csv_data = 'date,minutes,client,project,activity,type,notes\n2026-03-28,60,ACME,Proj,Task,direct,One\n2026-03-28,60,ACME,Proj,Task,direct,Duplicate\n'
    response = client.post('/import/csv', data={'file': (BytesIO(csv_data.encode('utf-8')), 'import.csv')}, content_type='multipart/form-data', follow_redirects=True)
    assert b'Import complete: 1 inserted, 1 duplicate(s) skipped.' in response.data


def test_exports_work(client, login_admin):
    login_admin()
    client.post('/', data={'work_date': '2026-03-28', 'minutes': '60', 'client': 'Export Co', 'project': 'Exports', 'activity': 'Prep', 'hour_type': 'direct', 'notes': 'Ready'}, follow_redirects=True)
    assert client.get('/export/csv').status_code == 200
    assert client.get('/export/xlsx').data[:2] == b'PK'
    assert client.get('/dashboard/export.png').mimetype == 'image/png'


def test_admin_self_delete_blocked(client, login_admin):
    login_admin()
    response = client.post('/profile/delete', follow_redirects=True)
    assert b'Admin accounts cannot self-delete' in response.data


def test_admin_role_preserved(client, login_admin, create_confirmed_user, db_conn):
    login_admin()
    admin_user = db_conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    response = client.post(f"/admin/edit-user/{admin_user['id']}", data={'username': 'admin', 'email': 'admin@example.com', 'role': 'user', 'email_confirmed': '1', 'theme_pref': 'auto'}, follow_redirects=True)
    assert b'must remain an admin' in response.data
