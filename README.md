# TaskEarn — connected MVP

This version is a working English-first microtask marketplace prototype with a real SQLite database and backend API using Python's standard library (no external packages required).

## Run

```bash
python server.py
```

Open: http://127.0.0.1:8080

## Demo accounts

Worker: `worker@taskeearn.demo` / `demo123`
Client: `client@taskeearn.demo` / `demo123`

## Connected flows

- Registration + login
- Worker/client roles
- SQLite persistence
- Task marketplace and filters
- Worker starts task
- Worker submits answer
- Client can create tasks and review submissions through API
- Approved submissions credit the worker balance and create a ledger entry

## API

GET `/api/tasks`
POST `/api/register`
POST `/api/login`
GET `/api/me`
POST `/api/tasks/start`
POST `/api/tasks/submit`
POST `/api/client/tasks`
GET `/api/client/tasks`
POST `/api/client/review`

This is an MVP/test environment. It does **not** process real cash payouts, KYC, fraud detection, production CSRF/session hardening, or payment-provider transfers yet.
