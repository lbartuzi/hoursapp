# Hours Admin

Open-source, self-hosted time tracking app with a SaaS-like workflow, built with Flask and SQLite.

## Highlights

- Multi-user support
- Admin user bootstrapped from Docker environment variables
- Minute-based entry UI with internal decimal-hour storage
- CSV import with duplicate skipping
- CSV and Excel export
- Dashboard with charts and PNG export
- Invite-only registration with secure tokenized links
- Email confirmation required before login
- Forgot-password flow with secure reset token
- Admin-managed email templates
- Account self-delete and admin delete
- Data retention policy with warning emails and automatic cleanup
- Theme preference: auto, dark, light

## Why SQLite

SQLite is a good fit for this app's expected scale:
- low operational overhead
- simple backup and restore
- reliable for small to medium datasets
- well suited to a single-container deployment

For the intended use case, SQLite remains appropriate.

## Quick start

```bash
docker compose up --build -d
```

Open:
- `http://localhost:8080`

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

For Gmail, use an app password instead of your normal account password.

## Registration flow

1. Admin sends an invite from the **Users** page.
2. Invitee opens the secure token link.
3. Invitee completes username, password, and theme preference.
4. App sends a separate email confirmation link.
5. User confirms email.
6. User can log in.

## Password reset flow

1. User opens **Forgot password**
2. User enters email
3. App sends a reset link if the account exists
4. User sets a new password

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

## Notes

- The admin account is marked as confirmed automatically
- Non-admin accounts must confirm email before first login
- Theme preference is stored per user, and also persisted in a browser cookie


## Self registration

The app now also supports an optional public self-registration page at `/register`.

Behavior:
- controlled by `SELF_REGISTRATION_ENABLED`
- can also be toggled from the admin settings page
- requires email confirmation before first login
- does not bypass the confirmation flow

Default:
- disabled

Docker variable:
- `SELF_REGISTRATION_ENABLED=true|false`


## Admin user editing

From the **Users** page, admins can now open an **Edit** screen for any user and manually change:
- email address
- email confirmed flag
- role
- theme preference

This is useful after upgrades from older databases or when fixing onboarding issues manually.


## Additional account improvements

- Users can request an email-address change from the profile page
- The new email becomes active only after confirmation through a tokenized email link
- Username availability is checked live on registration pages
- If a username already exists, the UI suggests an alternative such as `name2`
- Admin user edit now also supports changing usernames
- Entry action buttons are aligned side-by-side for a cleaner table layout


## Fixes
- Restored missing username uniqueness helper used by self-registration and invite registration.
- Improved live username validation trigger behavior.


## Live registration validation

Registration forms now validate live while the user types:
- username uniqueness check with clickable suggested alternative
- email uniqueness check with direct link to password reset when the email already exists

The forms now also preserve typed values after server-side validation errors.


## Validation stability fix
- Fixed self-registration and invite-registration error handling so forms keep values without crashing.
- Live username and email checks are now rendered inside the page content and run while typing.


## Refactored structure

- `app/` application package
- `app/routes/` route modules
- `app/services/` shared business logic
- `tests/` regression suite
- `run.py` recommended entrypoint

Run locally:

```bash
pip install -r requirements.txt
pytest -q
python run.py
```
