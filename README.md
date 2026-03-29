# Hours Admin

Open-source, self-hosted time tracking app with a SaaS-like workflow, built with Flask and SQLite.

## Live application

The application is available here:

- https://happ.pilbartuzi.homeip.net/

## Highlights

- Multi-user support
- Admin user bootstrapped from Docker environment variables
- Minute-based entry UI with internal decimal-hour storage
- CSV import with duplicate skipping
- CSV and Excel export
- Dashboard with charts and PNG export
- Invite-only registration with secure tokenized links
- Optional public self-registration
- Email confirmation required before login
- Forgot-password flow with secure reset token
- Admin-managed email templates
- Account self-delete for regular users
- Admin account protection against self-delete and admin-screen delete
- Data retention policy with warning emails and automatic cleanup
- Theme preference: auto, dark, light
- Live username and email validation on registration pages
- Regression test suite runnable through Docker Compose

## Why SQLite

SQLite is a good fit for this app's expected scale:

- low operational overhead
- simple backup and restore
- reliable for small to medium datasets
- well suited to a single-container deployment

For the intended use case, SQLite remains appropriate.

## Architecture

The refactored version is split into a scalable structure:

```text
app/
  __init__.py
  config.py
  db.py
  decorators.py
  security.py
  routes/
    auth.py
    entries.py
    admin.py
  services/
    core.py
  templates/
  static/
run.py
app.py
tests/
Dockerfile
docker-compose.yml
```

## Quick start

```bash
docker compose up --build -d
```

Open locally:

- `http://localhost:8080`

Or use the hosted instance:

- `https://happ.pilbartuzi.homeip.net/`

## Default admin account

Set these before first start:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `ADMIN_EMAIL`
- `SECRET_KEY`

## Required environment variables

- `PUBLIC_BASE_URL` — used to generate invite, confirmation, reset, and policy links
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
- `SMTP_USE_TLS`
- `RETENTION_DAYS`
- `RETENTION_WARNING_DAYS`
- `SELF_REGISTRATION_ENABLED`
- `DB_PATH`

For Gmail, use an app password instead of your normal account password.

## Registration flow

1. Admin sends an invite from the **Users** page.
2. Invitee opens the secure token link.
3. Invitee completes username, password, and theme preference.
4. App sends a separate email confirmation link.
5. User confirms email.
6. User can log in.

## Self registration

The app also supports an optional public self-registration page at `/register`.

Behavior:

- controlled by `SELF_REGISTRATION_ENABLED`
- can also be toggled from the admin settings page
- requires email confirmation before first login
- does not bypass the confirmation flow

Default:

- disabled

Docker variable:

- `SELF_REGISTRATION_ENABLED=true|false`

## Live registration validation

Registration forms validate live while the user types:

- username uniqueness check with clickable suggested alternative
- email uniqueness check with direct link to password reset when the email already exists
- typed values are preserved after server-side validation errors

## Password reset flow

1. User opens **Forgot password**
2. User enters email
3. App sends a reset link if the account exists
4. User sets a new password

## Admin user editing

From the **Users** page, admins can open an **Edit** screen for any user and manually change:

- username
- email address
- email confirmed flag
- role
- theme preference

This is useful after upgrades from older databases or when fixing onboarding issues manually.

## Admin account safety

The application is designed so the admin account cannot be removed through the normal UI:

- admins cannot self-delete from the profile page
- admin users cannot be deleted from the admin users screen
- the bootstrap admin account cannot be demoted to a normal user

This protects against accidental lockout.

## Data retention

- Accounts inactive for `365` days are deleted by default
- Warning email is sent `28` days before deletion by default
- Deletion also removes related hour entries and tokens
- Retention cleanup runs automatically during normal app activity
- Admin can trigger cleanup manually from the admin panel

## Email templates

Configurable from **Admin**:

- invite subject/body
- confirmation subject/body
- reset subject/body
- retention warning subject/body
- application name

Supported placeholders:

- `{{app_name}}`
- `{{email}}`
- `{{public_base_url}}`
- `{{register_link}}`
- `{{confirm_link}}`
- `{{reset_link}}`
- `{{login_link}}`
- `{{expires_at}}`
- `{{deletion_date}}`

## Existing database migration

This version auto-migrates older SQLite databases by adding missing:

- user columns
- token table
- settings table

Legacy entries without a user are assigned to the admin account.

## Docker notes

- Keep the SQLite database on a mounted volume
- Use a real `SECRET_KEY`
- Run a single writing app container when using SQLite
- Prefer a reverse proxy with HTTPS in front of the app

Example persistent database mount:

```yaml
volumes:
  - ./data:/data
environment:
  DB_PATH: /data/hours.db
```

## Running tests

Run the regression suite through Docker Compose:

```bash
docker compose run --rm test pytest -v
```

Or if your `test` service already uses `pytest -v` as its command:

```bash
docker compose run --rm test
```

The tests should use an isolated database such as `/tmp/test_hours.db`, not your live database.

## Local development

```bash
pip install -r requirements.txt
pytest -v
python run.py
```

## Notes

- The admin account is marked as confirmed automatically
- Non-admin accounts must confirm email before first login
- Theme preference is stored per user and also persisted in a browser cookie
- The dashboard depends on Chart.js being allowed by the Content Security Policy

