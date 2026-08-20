# TaskEarn MVP v7 — Admin Login Fix

This version keeps Worker and Client signup/login behavior while making the configured admin identity authoritative.

Admin behavior:
- Set `ADMIN_EMAIL` in Render.
- Optional: set `ADMIN_PASSWORD` to define/reset the admin password.
- If `ADMIN_PASSWORD` is temporarily absent but the admin email already exists as a user, the existing account password can promote that account to admin on login.
- A new admin account cannot be created without `ADMIN_PASSWORD`.

This is still an MVP with test funds only; no real payments are processed.
