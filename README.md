# TaskEarn MVP v3

English-first microtask marketplace MVP.

## Current features
- Worker / Client registration and login
- Sessions and password hashing
- Worker task marketplace
- Task start and submission flow
- Client task creation with quantity + reward
- Client funded-task budget checks
- Client submission review: approve / reject
- Worker balance + ledger credits on approval
- Client wallet with clearly labeled **test funds**
- Test funds endpoint credits the client wallet without processing real payments

## Test wallet
Client accounts can add $10 test funds from the Client Dashboard. This is for development only and does **not** charge a card or move real money.

## Production next steps
- PostgreSQL instead of SQLite
- Real payment provider + webhook verification
- Withdrawals / payouts
- Admin moderation
- Worker qualifications and quality scoring
- Rate limiting, CSRF/session hardening, audit logs
- Email verification and password reset
