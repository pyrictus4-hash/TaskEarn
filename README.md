# TaskEarn MVP v8 — Admin login fix

Admin login now works independently of the worker/client role.

Admin defaults for the test MVP if Render env vars are missing:
- Email: admin@taskeearn.com
- Password: TaskEarnAdmin#2026!X9

For production, set ADMIN_EMAIL and ADMIN_PASSWORD in Render and change the default password.
