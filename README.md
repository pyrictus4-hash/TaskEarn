# TaskEarn v9 — Separate Admin Portal

Admin now has a completely separate login at `/admin` and uses `/api/admin/login`.
Workers and clients continue using the normal TaskEarn login/register flow.

Admin credentials come from `ADMIN_EMAIL` and `ADMIN_PASSWORD`; defaults are provided for the local MVP.
