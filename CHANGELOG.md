# Changelog

All notable changes to WellRing Voice Agent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

> **Ground-truth rule (Phase 7.4):** Every entry requires raw tool output as proof —
> never a narrated summary. Add the verification artifact path or paste output inline.

---

## [Unreleased]

### Added
- `scripts/gen_railway_twilio_env.py` — validates the local `.env` Twilio
  credentials against the API and prints the exact env-var block to paste into
  Railway so the deployed backend uses the same Twilio account (the one that
  owns the WhatsApp number + its Account Auth Token). Warns when the SID is an
  `SK…` API key (webhook signatures can never validate) or `TWILIO_CONTENT_SID`
  is unset (outbound 21654 outside the 24h window).
- `context/README.md` — documented the frontend ↔ backend connection and the
  requirement that the deployed frontend bundle send the current `WELLRING_API_KEY`
  (the removed `wellring-secure-2026` key is rejected with 401).
- Inbound Twilio WhatsApp webhook: `POST /twilio-webhook` with `X-Twilio-Signature` validation,
  form-encoded body parsing, inbound-message logging, and a TwiML reply that replaces Twilio's
  canned "Standard auto-reply". 5 tests in `tests/test_twilio_webhook.py`.
  *Verification: `pytest -q` → 57 passed in 12.71s (2026-08-05)*
- `tests/test_history_sentinel_exclusion.py` — recreated from deleted source; 4 tests covering sentinel
  exclusion, orphan exclusion, real-user counting, and user-scoped isolation. All 4 pass.
  *Verification: `pytest tests/test_history_sentinel_exclusion.py -v` → 4 passed in 0.85s (2026-07-26)*
- `CHANGELOG.md` — this file (Phase 7.4)
- `CONTRIBUTING.md` — branch + review process documentation (Phase 7.1/7.3)
- Nemotron 70B watchdog integration: hallucination detection, confidence-based follow-ups,
  audit logging to `nemotron_audits` table (7 tests, all passing)
### Fixed
- `get_symptom_repeat_count` — excluded `is_system` sentinel user from global symptom
  counts to prevent score inflation (Phase 3.4); both Postgres and SQLite paths corrected
- `_migrate_sqlite_schema` — added `is_system INTEGER NOT NULL DEFAULT 0` to migration dict
  so legacy test DBs get the column automatically; resolves `no such column: u.is_system`
- `users` CREATE TABLE DDL — added `is_system` to schema so fresh DBs never miss the column
- `tests/conftest.py` — added session-scoped `_init_test_db` autouse fixture that patches
  `_use_postgres` → False and calls `init_db()` once per session, guaranteeing all tables
  exist for direct-DB tests (previously only the `client` fixture ran `init_db`)
- `test_nemotron_watchdog.py` — corrected mock targets to `src.main.trigger_alerts_if_needed`
  (the actual escalation path); removed stubs for non-existent `trigger_outbound_call` /
  `send_twilio_alert` symbols

### Added
- `USE_ROUTINE_UPDATES` env gate (default `false`) — LOW/MEDIUM "patient is fine"
  WhatsApp updates are now opt-in instead of firing on every check-in call (the
  primary driver of Twilio 63038 daily-quota storms). HIGH/CRITICAL alerts unaffected.
- `ALLOW_UNSIGNED_TWILIO_WEBHOOKS` env flag — explicit dev bypass for the inbound
  Twilio webhook; the webhook now FAILS CLOSED when `TWILIO_AUTH_TOKEN` is unset.
- Phone-normalization regression tests (`tests/test_phone_normalization.py`, 5 tests).
  *Verification: `pytest -q` → 78 passed in 17.14s (2026-08-07)*

### Fixed
- Routine WhatsApp updates now respect `twilio_quota_exhausted()` — no Twilio calls
  are burned during a 63038 cooldown (previously only the scheduler/watchdog checked).
- Phone lookup consistency: added shared `normalize_phone()` / `phone_match_candidates()`
  used by `get_user_by_phone`, `_get_user_health_context_pg`, `_get_call_timeline_pg`/
  `_sqlite`, and `_do_bolna_call`. Stored forms `9004261186`, `919004261186`,
  `+91 90042 61186` now all match the normalized call phone → health context,
  family contacts, and unanswered-call alerts resolve correctly again.
- Scheduler retry-counter wipe: `consecutive_failures` is no longer cleared while a
  reminder is mid-flight (a slow Bolna call holding its claim no longer resets the
  `MAX_REMINDER_ATTEMPTS` budget to zero).
- Scheduler Twilio sends (`send_whatsapp_reminder`) offloaded via `asyncio.to_thread`
  — no more synchronous Twilio HTTP round-trips stalling the event loop.
- Reminder input validation: `ReminderCreate` now validates `type`, `frequency`,
  `time` (HH:MM or ISO), and `phone` (≥10 digits); `/assessments` limit bounded
  (1–500) and `/timeline` limit bounded (1–2000).
- Dashboard feed/stats now exclude the anonymous sentinel / `is_system` users and
  orphan rows (PG, SQLite, and Supabase paths), matching the symptom-count rule.
- `ANONYMOUS_USER_ID` resolved lazily via `get_anonymous_user_id()` — a Postgres
  blip at startup no longer freezes the sentinel to `""` (guard fails open).
- `sanitize_assess_payload` drops `%`-placeholder items inside a symptoms list
  (e.g. `["%(symptoms)s"]`) instead of silently suppressing the missing-symptoms warning.
- `calculate_score` unknown-symptom warning now uses `logger.warning` instead of `print`.
- `/risk-levels` derives score ranges from `baseline._THRESHOLDS` instead of hardcoding.
- Weekly reminders use ISO week (`%G-W%V`) instead of `%W`. Mid-year the two
  formats coincide (e.g. `2026-W32`), so existing markers keep working; only
  reminders due near a year boundary (where `%W` mislabels week 00/53) will
  re-fire once.
- `/upload-document`: 10 MB size cap, always deletes the local temp file (success or
  error), and no longer returns the server filesystem path (sanitized filename only).
- Twilio diagnostics: `_twilio_send` and `_twilio_request_valid` now log the
  masked Twilio SID plus its kind (`AC…` Account SID vs `SK…` API Key SID) on
  every send and every webhook rejection — an account mismatch is visible in one
  log line. Root cause it surfaced: Railway ran an `SK…` API key while Twilio
  signs webhooks with the Account Auth Token, so every inbound WhatsApp message
  was 403-rejected (Twilio error 12300) and outbound standalone sends failed
  with 21654 (no `TWILIO_CONTENT_SID`).
- `_twilio_send` computes the WhatsApp sender/recipient addresses before the
  API call, so the 21654 (ContentSid Required) error path can report the `from`
  number without risking a `NameError`.
  *Verification: `pytest tests/test_twilio_webhook.py tests/test_notifications.py -q`
  → 21 passed in 6.58s (2026-08-07)*
- Cross-account data isolation completed across the remaining dashboard surfaces
  (the deployed Railway backend was still running the pre-isolation build that
  returned every account's data — redeploying fixes the reported "common
  dashboard" leak):
  - `/timeline` accepts `clerk_id` and only resolves the elder owned by that
    uid (legacy phone-only lookup preserved when omitted); an unmatched phone now
    returns `[]` instead of every user's interactions (SQLite fallback removed).
  - `/recordings/{assessment_id}` accepts `clerk_id` and enforces ownership
    (404 unless the assessment belongs to the caller's elder).
  - Reminders now carry an owner `user_id` (`POST /reminders` resolves the elder
    from the optional `clerk_id` body field) and `GET /reminders?clerk_id=`
    scopes by owner, falling back to phone matching only for pre-ownership rows.
    `reminders.user_id` is self-healing on Postgres (startup ALTER) and SQLite
    (migration).
  - 5 new regression tests in `tests/test_data_isolation.py` pin the isolation
    contract; `pytest.ini` `python_files` allowlist updated.
  *Verification: `pytest -q` → 93 passed in ~16s (2026-08-07)*
- `analyze_emotion_from_audio` always cleans up its temp audio file, even on Gemini failure.
- `/bolna-webhook` token check uses `secrets.compare_digest` (timing-safe) and rejects
  missing tokens. (Secret still travels in the query string — Bolna limitation.)
- Dashboard "latest conversation" quality fixes:
  - `/bolna-webhook` is now idempotent — `assessment_exists_for_call()` skips creating a
    second assessment when Bolna retries delivery (retries were producing up to 3 rows per
    call and flooding the feed/timeline). Checked before recording upload/emotion analysis.
  - Prefers the in-call Bolna extraction (symptoms + valid severity) over post-hoc Gemini
    re-analysis of the ASR transcript, which systematically under-extracted (empty symptoms,
    `LOW/10` risk for a call where the patient reported fever).
  - `dedupe_transcript()` collapses repeated/truncated assistant lines before storing and
    before analysis (the "garbled transcript" on the dashboard).
  - `analyze_transcript_for_health_issues` prompt now maps patient speech to the exact
    `SYMPTOM_WEIGHTS` vocabulary.
  - `get_user_by_phone` prefers the onboarded (`clerk_id`) profile when multiple users
    share a phone (e.g. `Test User` vs `Mr. Sharma` both `+918421971145`) — future calls
    attribute to the real dashboard user.
  - SQLite `interactions` now persists `bolna_call_id` (CREATE TABLE + `_migrate_sqlite_schema`
    ALTER for existing DBs).
  *Verification: `pytest -q` → 88 passed in 17.02s (2026-08-07)*

### Security
- `/config-check` now requires a valid `X-API-Key` (was unauthenticated — leaked
  masked-but-identifiable key fragments and service-configuration state).
- `global_exception_handler` returns a generic `"Internal server error"` — `str(exc)`
  internals only surface when `DEBUG=true`.
- Twilio inbound webhook fails closed when `TWILIO_AUTH_TOKEN` is unset unless
  `ALLOW_UNSIGNED_TWILIO_WEBHOOKS=true` is explicitly set (was fully open in prod
  if the token was ever missing).

### Removed
- Appointment page: removed the `/appointments` API (`GET/POST` + `DELETE /appointments/{id}`),
  the `appointments` table (SQLite + Postgres `schema.sql`), and `tests/test_appointments.py`
  (2026-08-07)
- `render.yaml` — Render is no longer the deployment target; Railway is the sole deploy
  platform (Phase 5.5)
- Hardcoded `X-API-Key: ***REMOVED***` auth bypass (Phase 6.1)

### Security
- Rotated Bolna API key (previously leaked as a hardcode fallback in git history —
  commit contains `bn-0d9f1aa2347d4aa68b593c8e0680aed5`; key has been invalidated)
- Rotated PostgreSQL password (Phase 6.3)
- CORS origins audited; stale `onrender.com` references removed (Phase 5.2)
- Verified git history for leaked credentials via `git log -G` regex search; confirmed no active external credentials exist in history (Phase 6.4)
- Stripped `wellring-secure-2026` from 7 JSON config snapshots and 2 helper scripts
  (`check_system.py`, `smoke_test.py`); scripts now fail loudly if `WELLRING_API_KEY` is
  unset rather than silently using the old bypass. Stale `onrender.com` URL in
  `book_appointment` webhook replaced with Railway URL across all config files. (2026-07-26)

---

## [0.9.0] — 2026-07-25

### Added
- Postgres-first data layer: `get_pg_conn`, `log_assessment_pg`, `upsert_health_history`,
  `log_conversation_turn`
- `nemotron_audits` table (SQLite + Postgres) for watchdog audit trail
- Session-scoped test DB initialisation in `conftest.py`
- `get_symptom_repeat_count` user_id-scoped filtering

### Fixed
- Deterministic `get_user_by_phone` using `ORDER BY created_at DESC LIMIT 1`
  (commit `c7a52b8`); prevents duplicate-row ambiguity
- `_ensure_anonymous_user_pg` — replaced epoch-timestamp hack with proper
  `is_system` boolean column and idempotent upsert
- `/assess` endpoint — `intent` field made required again after accidental revert

### Security
- All Railway environment variables audited; Render-targeted values removed
- `.env` confirmed absent from git history (only `.env.example` committed)

---

## [0.8.0] — 2026-07-19

### Added
- Initial Railway deployment configuration (`railway.toml` / `Dockerfile`)
- Automated `pg_dump` backup via GitHub Actions to B2 (Phase 1.2)
- Staging database isolation (Phase 1.3)

### Fixed
- `assessments` and `conversations` tables recreated after data-loss incident
  (Phase 0/1 root cause: unscoped DELETE with cascade from test harness)

### Security
- Rotated B2 Application Key (exposed in prior session log)

---

## [0.7.0] — 2026-07-18

### Added
- FastAPI backend: `/assess`, `/health`, `/symptoms`, `/risk-levels`, `/alerts`,
  `/reminders`, `/users` endpoints
- SQLite → Supabase → PostgreSQL backend priority chain
- Bolna v2 voice agent integration
- WhatsApp/Twilio alert pipeline triggered automatically on HIGH/CRITICAL assessments

### Known Issues
- `assessments` and `conversations` tables wiped (data loss — see Phase 0/1)
- Hardcoded auth bypass present (removed in 0.9.0)
