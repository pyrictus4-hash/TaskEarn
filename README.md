# TaskEarn MVP v2

English-first microtask marketplace prototype.

## Included
- Worker and client registration/login
- Persistent sessions in SQLite
- Worker task marketplace
- Worker earnings summary and ledger
- Task submissions and client review
- Client wallet and funded task creation
- Task quantity/budget tracking
- Automatic worker credit after client approval
- Basic responsive UI

## Demo accounts
- Worker: worker@taskeearn.demo / demo123
- Client: client@taskeearn.demo / demo123

## Run locally
```bash
python server.py
```
Open `http://127.0.0.1:8091`.

## Production note
This MVP uses SQLite for simplicity. For real traffic and durable production storage, migrate to PostgreSQL before onboarding large numbers of users. Add a production secret/session strategy, HTTPS, rate limiting, CSRF protection, email verification, password reset, audit logging, moderation, and a real payment provider before taking real money.
