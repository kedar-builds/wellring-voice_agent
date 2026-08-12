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

1. **Rebuild with the current API key.** The deployed bundle hardcodes the removed key
   `wellring-secure-2026` → every authenticated request gets `401`. Set the frontend's
   key to the backend's current `WELLRING_API_KEY` value.
2. **Wire the scheduling buttons to the backend.** Point them at the real calls:
   `POST /call` for an immediate check-in, or `POST /reminders` with `type: "call"`
   for a scheduled one (the backend scheduler then fires the Bolna call).
   "Schedule AI Call" currently does the same — nothing is sent to the backend.
3. **Add Vercel serverless functions** (or rewrites) for `/api/ai-simulator`,
   `/api/ai-evaluate`, and `/api/profile*` — they currently return 405.
# SQLite fallback active - Sat Jun 20 11:16:46 PM IST 2026
