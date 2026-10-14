# Demo app

The intentionally-broken **React + Node.js + PostgreSQL** project that DevOS imports, diagnoses and repairs. This is a *target* for DevOS, not part of DevOS's own backend (which is Python/FastAPI).

```
demo-app/
├─ frontend/   React app
├─ backend/    Node.js API
└─ db/         Postgres schema / seed
```

## Failure injections (to implement)

Each should be individually toggleable so Project Doctor checks and recovery plans can be tested one at a time.

| Category | Example |
|---|---|
| Configuration | missing / wrong `.env` variable |
| Dependency | package missing from `package.json` or `node_modules` |
| Port | backend port already in use / mismatched between frontend and backend |
| Database | wrong credentials, missing table / un-run migration |
| Runtime | unhandled exception on startup |
