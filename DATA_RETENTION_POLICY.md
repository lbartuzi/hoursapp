# Data Retention Policy

## Summary

This project deletes inactive user accounts and related data after **2555 days** / **7 years** without login activity by default.
Still strongly advised to export all registrations anually. 

## Scope

The following data is removed when an account is deleted:
- user profile
- hour entries
- related security tokens

## Retention trigger

The retention timer uses:
1. `last_login` when available
2. `created_at` if the user never logged in

## Warning notice

A reminder email is sent **28 days before deletion** when SMTP is configured and the account still has a valid email address.

## Admin controls

Administrators can:
- run retention cleanup manually
- customize warning email templates
- review users and last-login timestamps

## Configuration

Retention can be adjusted through Docker environment variables:
- `RETENTION_DAYS`
- `RETENTION_WARNING_DAYS`

## Legal note

This is a default operational policy for the application. Operators should verify whether additional legal or contractual retention requirements apply in their own jurisdiction or business context.
