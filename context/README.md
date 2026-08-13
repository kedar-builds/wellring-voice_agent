# WellRing Voice Agent Platform

An AI-powered, voice-first health assistant for elderly people. WellRing listens, understands, and responds in real-time over the phone or web, utilizing a clinical scoring engine to detect emergencies and instantly alert caregivers.

## 🌟 Architecture Overview

WellRing has evolved into a production-ready cloud platform:

- **Voice Orchestration (Bolna):** Handles real-time WebRTC/telephony via the Bolna AI agent infrastructure.
- **Intelligence (LLM):** Powers the conversational assistant via Bolna, extracting structured symptoms and severity through tool calls.
- **Scoring Engine (FastAPI):** A decoupled backend that receives `assess_health_risk` function calls from Bolna to calculate clinical risk (Low, Medium, High, Critical).
- **Caregiver Dashboard (React + Vite):** A modern frontend (separate repo) where caregivers can view patient status, review assessment history, and manage reminders.
- **Alerts (Twilio):** Automatically dispatches WhatsApp messages to caregivers when a high or critical risk is detected.

## 📂 Project Structure

```text
wellring-voice_agent/
├── src/                   ← FastAPI Backend & Scoring Engine
│   ├── main.py            ← Entrypoint (all routes, scheduler)
│   ├── database.py        ← Multi-backend data access (PG, Supabase, SQLite)
│   ├── notifications.py   ← Twilio WhatsApp dispatch
│   ├── users.py           ← User profile lookups
│   ├── scoring_engine/    ← Risk calculation logic
│   │   ├── rules.py       ← Symptom weights & categories
│   │   ├── scoring.py     ← Score calculation
│   │   ├── alerts.py      ← Escalation actions
│   │   └── baseline.py    ← Risk level thresholds
│   └── db/                ← PostgreSQL schema & migration
│       ├── schema.sql     ← Full DB schema (6 tables)
│       └── migrate.py     ← Schema migration runner
├── tests/                 ← Pytest test suite
├── docs/                  ← Architecture & API docs
├── bolna_assistant.json    ← Bolna cloud assistant blueprint
├── voice_health.py        ← Local hardware testing script (Gemini + Whisper + Piper)
├── simulate_demo.py       ← Demo scenario runner
├── .github/workflows/     ← CI/CD pipeline
└── render.yaml            ← Backend deployment config
```

## 🚀 Setup Instructions

### 1. Backend API (FastAPI)

1. Clone the repository and create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
   *Fill in your API keys (Gemini, Twilio, Bolna) and set a strong `WELLRING_API_KEY`.*
4. (Optional) Set up PostgreSQL and run the migration:
   ```bash
   # Set DATABASE_URL in .env first
   python -m src.db.migrate
   ```
5. Start the server:
   ```bash
   uvicorn src.main:app --reload --port 8000
   ```

### 2. Bolna Cloud Setup

To enable the actual voice calling functionality:
1. Go to the [Bolna AI Dashboard](https://bolna.ai).
2. Create an Agent and configure your Prompts and Voice.
3. Configure the `assess_health_risk` tool call to point to this backend.
4. Update the headers in the tool server config to include `X-API-Key` matching your `WELLRING_API_KEY`.
5. Link a Twilio phone number in Bolna if you wish to use a custom caller ID.

### 3. Running Tests

```bash
python -m pytest -v
```

## 🚨 Emergency Detection

The system automatically detects critical keywords during the conversation:
- Chest pain
- Difficulty breathing
- Fallen down
- Unconscious
- Stroke symptoms

On detection, the assistant is instructed to immediately tell the patient to call emergency services (112/911), while the backend simultaneously dispatches a critical WhatsApp alert to the assigned caregiver.

## 🔐 Security

- All API endpoints require authentication via the `X-API-Key` header.
- Set `WELLRING_API_KEY` in your `.env` — the server will refuse to start without it.
- CORS is restricted to known frontend domains.
- File uploads are validated for allowed types (PDF, images only).
- **Never commit `.env` to git.** Use `.env.example` as a template.

## 🛡️ Security hardening (login / auth)

Beyond Clerk session verification, the backend ships with:

- **Rate limiting** (`src/ratelimit.py` — dependency-free, in-memory): every
  protected route is capped per client IP (`RATE_LIMIT_REQUESTS_PER_MINUTE`,
  default 600/min), and any IP that produces too many `401`s
  (`RATE_LIMIT_FAILURES_PER_WINDOW`, default 20 per 10 min) is blocked for
  `RATE_LIMIT_BLOCK_SECONDS` (default 15 min). Webhooks
  (`/twilio-webhook`, `/bolna-webhook`) and `/health*` are exempt.
- **Auth-health watchdog** (`src/auth_health.py`): when `CLERK_SECRET_KEY` is
  missing in a production-like environment, or a burst of rejected Clerk
  tokens suggests a broken frontend login flow, the team is paged via
  `DEV_ALERT_WEBHOOK_URL` (at most once/hour).
- **`GET /health/auth`** — auth status endpoint (no secrets): returns `200`
  with `{status, mode, clerk_secret_key, secure, alerts}` when auth is
  configured, or **`503` when insecure** (e.g. `CLERK_SECRET_KEY` missing in a
  production-like env). `railway.toml` points the deployment healthcheck here,
  so a deploy that would ship with login auth off is marked failed instead of
  going live silently.
- **`GET /auth/events`** — recent auth-health / rate-limit events for the ops
  dashboard (Clerk-protected, same contract as `/watchdog/logs`): rate-limit
  IP blocks, missing-secret alerts, and Clerk rejection spikes, persisted to
  the `auth_events` table (SQLite + Postgres).
- **Production startup guard** — refuses to boot without `CLERK_SECRET_KEY`
  when `ENV` / `RAILWAY_ENVIRONMENT` / `VERCEL_ENV` is production-like.

### End-to-end login smoke test

`scripts/smoke_test_login.py` boots the real app against an isolated SQLite
DB and exercises the full caregiver login → onboarding → dashboard flow
(setup-profile, reminders, family contacts, patients, assess, assessments,
stats, timeline, data isolation):

```bash
venv/bin/python scripts/smoke_test_login.py        # dev mode
CLERK_SECRET_KEY=sk_... venv/bin/python scripts/smoke_test_login.py \
    --token eyJhbGci...                             # production mode
```

In production mode it also asserts every dashboard endpoint rejects requests
without the Bearer token (401). Get a real token from the browser console:
`await window.Clerk.session.getToken()`.

**`--live` probe (no token needed):** verifies a *deployed* backend's
fail-closed contract — every dashboard endpoint must return 401 with just the
static API key, and `/health/auth` must report secure:

```bash
venv/bin/python scripts/smoke_test_login.py \
    --live https://wellring-backend-production.up.railway.app
```

Run it before/after every redeploy. As of 2026-08-13 it **fails** (deployed
backend still runs an old build with Clerk verification disabled).

## 🗄️ Using Supabase as the main database

Supabase is managed PostgreSQL, and the backend is already Postgres-first
(`DATABASE_URL` + `src/db/schema.sql` + self-healing `init_pg_tables()` at
startup). Switching to Supabase requires **no code changes** — only a new
connection string.

### Steps

1. **Create a Supabase project** and open **Project Settings → Database →
   Connection string**. Copy the **URI** (direct connection, port `5432`) or the
   Transaction pooler URI (port `6543`, for connection limits).
2. **Migrate + verify** from this repo (one command):

   ```bash
   SUPABASE_DATABASE_URL="postgresql://postgres.xxxx:password@aws-0-xx.pooler.supabase.com:5432/postgres" \
     python scripts/setup_supabase.py
   ```

   This applies `src/db/schema.sql` (users, assessments, alerts, conversations,
   health_history, reminders, …) and runs a full verification suite (tables,
   key columns including `reminders.user_id`, read/write round-trip).
3. **Set `DATABASE_URL`** to the same connection string in Railway (or `.env`)
   and redeploy. On startup the app self-heals any missing columns and seeds
   the anonymous system user.

### Notes

- Supabase's own `auth.users` table lives in the same Postgres instance — if you
  later switch login to Supabase Auth, the login data and app data stay together.
  Until then, the app keeps linking accounts via the `users.clerk_id` column.
- The pooler URI may include `?sslmode=require` — that's fine for psycopg2.
- Local dev/tests keep using SQLite (`WELLRING_DB_PATH`); they never touch this DB.

## 🔌 Frontend ↔ Backend Connection

The caregiver dashboard is a **separate React repo** deployed on Vercel
(`https://wellring-frontend.vercel.app`). It calls this backend at
`https://wellring-backend-production.up.railway.app` (the `BASE_WEBHOOK_URL` in `.env`).

### ⚠️ API key must match on both sides (read before redeploying the frontend)

Every dashboard request sends `X-API-Key` and is rejected with `401` if the value differs
from the backend's `WELLRING_API_KEY` env var.

- The old hardcoded key `wellring-secure-2026` was **removed** from the backend in the
  Phase 6.1 security cleanup — the deployed backend now rejects it with `401`.
- **Any frontend build that still hardcodes `wellring-secure-2026` will show empty/error
  states for every authenticated feature** (dashboard feed, timeline, reminders,
  profile, family contacts).
- To fix: rebuild/redeploy the frontend with the **current** `WELLRING_API_KEY` value
  (the same value set in the deployed backend's Railway env).

### 🔑 Clerk session token — REQUIRED in production (login contract)

When `CLERK_SECRET_KEY` is set on the backend (production), **every dashboard and
outbound endpoint additionally requires a valid Clerk session JWT**. The backend accepts
it either way:

1. **`Authorization: Bearer <session token>` header** — the recommended approach. In
   React, wrap the app in `<ClerkProvider>` and call `useAuth().getToken()` before each
   request:

   ```tsx
   const { getToken } = useAuth();
   const token = await getToken(); // Clerk session JWT (auto-refreshes)
   const res = await fetch(`${API_BASE}/assessments?limit=50`, {
     headers: {
       "X-API-Key": API_KEY,
       ...(token ? { Authorization: `Bearer ${token}` } : {}),
     },
   });
   // 401 → session expired/missing: refresh via Clerk, then retry
   if (res.status === 401) { /* trigger Clerk re-auth / token refresh */ }
   ```

2. **`__session` cookie** — if the frontend uses Clerk's cookie-based sessions, the
   backend reads the cookie automatically (CORS credentials are now enabled, so the
   browser sends it cross-origin). No extra header code needed.

**Failure modes to know:**
- Missing/invalid/expired token → `401` on ALL dashboard endpoints (the backend fails
  closed). The frontend must handle 401 by refreshing the Clerk token, not by treating
  it as "logged out" — a 401 often just means the token needs a refresh.
- The verified JWT `sub` **overrides** any `clerk_id` the client sends — impersonation
  is impossible; `clerk_id` params become a dev-only fallback.
- If the frontend never sends a token, production shows empty/error states everywhere
  even though the user "logged in" successfully in Clerk.
- `/config-check` and `/watchdog/logs` are also token-protected now — the
  monitoring/debug pages must send the Bearer token too.

### 🚀 Frontend production checklist (must all be true)

1. **Clerk is configured** — `VITE_CLERK_PUBLISHABLE_KEY` set, `<ClerkProvider>` wraps
   the app, and every API call sends `Authorization: Bearer <getToken()>` (or uses the
   `__session` cookie).
2. **API key updated** — remove the hardcoded `wellring-secure-2026`; use the backend's
   current `WELLRING_API_KEY` via `VITE_API_KEY`.
3. **CORS origin** — the deployed frontend URL must be in the backend's `ALLOWED_ORIGINS`
   (add new/preview domains there before deploying).
4. **Onboarding** — after login, `POST /setup-profile` with the token; the profile is
   stored under the verified uid, so subsequent GETs need no `clerk_id` at all.
5. **Optional hardening** — set `CLERK_AUTHORIZED_PARTIES` on the backend to lock tokens
   to the frontend origin(s) (see `.env.example`; only after all sessions are fresh).

### Frontend feature → backend endpoint map

> **Per-user isolation (required contract):** every data-returning dashboard
> endpoint scopes by the current Firebase UID. The frontend MUST pass
> `clerk_id` as a query param (or in the POST body) on every request below.
> Without it the endpoints return **empty** results — they never fall back to
> returning every account's data (that fallback was the "common dashboard"
> cross-account leak).

| Frontend feature | Backend route |
|---|---|
| Health indicator | `GET /health` |
| Dashboard feed | `GET /assessments?clerk_id=…&limit=50` |
| Dashboard stats | `GET /assessments/stats?clerk_id=…` |
| Call timeline | `GET /timeline?phone=…&limit=365&clerk_id=…` |
| Elder list | `GET /patients?clerk_id=…` |
| Reminders (list/add/delete) | `GET /reminders?clerk_id=…`, `POST /reminders` (body includes `clerk_id`), `DELETE /reminders/{id}` |
| Profile | `GET/POST /setup-profile` (body/query `clerk_id`) |
| Family contacts | `GET/POST /family-contacts` (`clerk_id`), `DELETE /family-contacts/{id}` |
| Recording playback | `GET /recordings/{assessment_id}?clerk_id=…` |
| Outbound call (immediate) | `POST /call` `{phone}` |
| Scheduled AI check-in call | `POST /reminders` `{type: "call", time, frequency, phone, clerk_id}` |

> The frontend also calls Vercel-relative paths (`/api/ai-simulator`, `/api/ai-evaluate`,
> `/api/profile*`) that are served by the frontend repo's own serverless functions — not
> by this backend. Those return 405 if the functions are missing on Vercel.

### Fix checklist (frontend repo) — required for the app to work end-to-end

1. **Send the Clerk session token.** Add `Authorization: Bearer <getToken()>` (or the
   `__session` cookie) to every dashboard/outbound request — without it production
   returns `401` on everything even after a successful Clerk login.
2. **Rebuild with the current API key.** The deployed bundle hardcodes the removed key
   `wellring-secure-2026` → every authenticated request gets `401`. Set the frontend's
   key to the backend's current `WELLRING_API_KEY` value.
3. **Handle 401s by refreshing the Clerk token**, then retrying — do not log the user
   out on the first 401 (Clerk tokens auto-refresh via `getToken()`).
4. **Wire the scheduling buttons to the backend.** Point them at the real calls:
   `POST /call` for an immediate check-in, or `POST /reminders` with `type: "call"`
   for a scheduled one (the backend scheduler then fires the Bolna call).
   "Schedule AI Call" currently does the same — nothing is sent to the backend.
5. **Add Vercel serverless functions** (or rewrites) for `/api/ai-simulator`,
   `/api/ai-evaluate`, and `/api/profile*` — they currently return 405.
# SQLite fallback active - Sat Jun 20 11:16:46 PM IST 2026
