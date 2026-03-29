import re


def test_register_page_includes_live_validation_script(client):
    response = client.get('/register')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="usernameInput"' in html
    assert 'id="emailInput"' in html
    assert '/static/js/live_validation.js' in html


def test_self_register_api_check_email_reports_existing_user(client, create_confirmed_user):
    create_confirmed_user(username='existinguser', email='existing@example.com', password='StrongPass123!')
    response = client.get('/api/check-email?email=existing@example.com')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['available'] is False
    assert payload['exists'] is True
    assert payload['forgot_password_url'].endswith('/forgot-password')


def test_api_check_username_suggests_alternative_for_taken_name(client, create_confirmed_user):
    create_confirmed_user(username='takenname', email='taken@example.com', password='StrongPass123!')
    response = client.get('/api/check-username?username=takenname')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['available'] is False
    assert payload['suggestion']
    assert re.match(r'^takenname[._-]?\d+$|^takenname\d+$', payload['suggestion']) or payload['suggestion'] != 'takenname'
