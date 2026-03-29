def _login(client, username="admin", password="adminpass123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=True)


def test_dashboard_page_loads_and_includes_chart_script(client):
    _login(client)
    response = client.get('/dashboard')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Dashboard' in body
    assert 'chart.umd.min.js' in body
    assert 'monthlyChart' in body


def test_dashboard_csp_allows_chartjs_cdn(app_module):
    client = app_module.test_client()
    response = client.get('/login')
    csp = response.headers.get('Content-Security-Policy', '')
    assert "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'" in csp
