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
# SQLite fallback active - Sat Jun 20 11:16:46 PM IST 2026
