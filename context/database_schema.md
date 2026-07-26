# Database Schema

WellRing supports three database backends with automatic fallback:
1. **PostgreSQL** (primary) — full schema via `DATABASE_URL`
2. **Supabase** (managed Postgres alternative) — via `USE_SUPABASE=true`
3. **SQLite** (local dev/test fallback)

The canonical schema is defined in [`src/db/schema.sql`](../src/db/schema.sql).

---

## Tables

### `users`

Stores both elderly patients and caregiver profiles.

| Column | Type | Description |
|---|---|---|
| `user_id` | UUID (PK) | Auto-generated primary key |
| `firebase_uid` | Text (UNIQUE) | Firebase authentication UID |
| `name` | Text | User's name |
| `age` | Integer | Age (1–149) |
| `role` | Text | `'elderly'` or `'caregiver'` |
| `phone` | Text | Phone number |
| `email` | Text | Email address |
| `medical_conditions` | Text[] | Array of known conditions |
| `medications` | Text[] | Array of current medications |
| `medical_notes` | Text | Unstructured medical notes |
| `caregiver_for_user_id` | UUID (FK) | Links caregiver → elderly user |
| `relationship` | Text | How caregiver is related to elder |
| `caregiver_name` | Text | Denormalised caregiver name |
| `caregiver_phone` | Text | Denormalised caregiver phone |
| `created_at` | Timestamptz | Auto-set on create |
| `updated_at` | Timestamptz | Auto-updated via trigger |

### `assessments`

Every health risk assessment triggered by a voice interaction.

| Column | Type | Description |
|---|---|---|
| `assessment_id` | UUID (PK) | Auto-generated |
| `user_id` | UUID (FK → users) | Patient who was assessed |
| `intent` | Text | e.g. `'health_issue'` |
| `symptoms` | Text[] | Array of symptom keys |
| `severity` | Text | `low` / `medium` / `high` / `critical` |
| `confidence` | Numeric(4,3) | LLM confidence [0.0–1.0] |
| `score` | Integer | Final risk score |
| `base_score` | Integer | Raw score before confidence scaling |
| `risk_level` | Text | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `category` | Text | Primary clinical category |
| `action` | Text | Escalation action identifier |
| `message` | Text | Human-readable summary |
| `steps` | Text[] | Ordered escalation steps |
| `breakdown` | Text[] | Per-component score explanation |
| `bolna_call_id` | Text | Bolna session identifier |
| `recording_url` | Text | URL to call recording |
| `assessed_at` | Timestamptz | Auto-set on create |

### `alerts`

Notification records triggered after assessments.

| Column | Type | Description |
|---|---|---|
| `alert_id` | UUID (PK) | Auto-generated |
| `assessment_id` | UUID (FK → assessments) | Triggering assessment |
| `alert_type` | Text | `sms` / `call` / `email` / `push` / `emergency_services` / `in_app` |
| `status` | Text | `pending` / `sent` / `delivered` / `failed` |
| `recipient_name` | Text | Who was notified |
| `recipient_phone` | Text | Phone number |
| `recipient_email` | Text | Email address |
| `payload` | JSONB | Raw request/response payload |
| `error_message` | Text | Error details if failed |
| `created_at` | Timestamptz | Auto-set |
| `sent_at` | Timestamptz | When delivery succeeded |

### `conversations`

Voice/text conversation turns with Riley (the AI assistant).

| Column | Type | Description |
|---|---|---|
| `conversation_id` | UUID (PK) | Auto-generated |
| `user_id` | UUID (FK → users) | Patient |
| `assessment_id` | UUID (FK → assessments) | Linked assessment (nullable) |
| `bolna_call_id` | Text | Bolna session ID |
| `channel` | Text | `web` / `phone` / `whatsapp` |
| `role` | Text | `user` / `assistant` / `system` |
| `content` | Text | Message content |
| `audio_url` | Text | Audio recording URL |
| `duration_secs` | Integer | Audio duration |
| `spoken_at` | Timestamptz | Auto-set |

### `health_history`

Aggregated symptom trends for escalation scoring.

| Column | Type | Description |
|---|---|---|
| `health_id` | UUID (PK) | Auto-generated |
| `user_id` | UUID (FK → users) | Patient |
| `symptom` | Text | Symptom key |
| `window_start` | Timestamptz | Aggregation window start |
| `window_end` | Timestamptz | Aggregation window end |
| `occurrence_count` | Integer | Times symptom appeared |
| `peak_severity` | Text | Highest severity in window |
| `peak_risk_level` | Text | Highest risk level in window |
| `last_assessment_id` | UUID (FK → assessments) | Most recent assessment |
| `escalation_flagged` | Boolean | Whether threshold was crossed |
| `recorded_at` | Timestamptz | Auto-set |

### `reminders`

Scheduled medicine, checkup, and call reminders.

| Column | Type | Description |
|---|---|---|
| `id` | Serial (PK) | Auto-incrementing |
| `type` | Text | `call` / `medicine` / `checkup` |
| `title` | Text | Reminder name |
| `time` | Text | `HH:MM` or ISO datetime |
| `frequency` | Text | `daily` / `weekly` / `monthly` / `yearly` / `once` |
| `phone` | Text | WhatsApp delivery number |
| `notes` | Text | Additional notes |
| `last_triggered` | Text | Last trigger timestamp |

---

## SQLite Fallback Schema

When PostgreSQL is not configured, a simplified SQLite schema is used with tables:
- `interactions` (maps to `assessments`)
- `users` (simplified, no UUID or role)
- `alerts_log` (maps to `alerts`)
- `reminders` (identical)

The SQLite schema is auto-created on startup by `init_db()`.
