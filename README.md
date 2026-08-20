# TaskEarn MVP v4 — Admin Control Center

This version adds an admin-only control center to the existing TaskEarn MVP.

## New features
- Admin account bootstrap from `ADMIN_EMAIL` + `ADMIN_PASSWORD` environment variables
- Admin dashboard with platform metrics
- User list with enable/disable controls
- Global task moderation: pause, resume, close
- Global pending-submission review: approve/reject
- Recent platform ledger view
- Active/inactive user enforcement for login and sessions

## Render environment variables
Set these on the TaskEarn Web Service:

- `ADMIN_EMAIL` — e.g. `admin@taskeearn.com`
- `ADMIN_PASSWORD` — use a strong password of at least 10 characters

After saving the variables, redeploy the service. The admin account will be created/updated automatically.

## Notes
- Test funds remain test-only; no real payment processor is connected.
- The current MVP still uses SQLite. PostgreSQL should be the next production hardening step before real users and real money.
